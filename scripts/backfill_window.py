"""Process one closed historical window without publishing or rediscovery."""
import argparse
from contextlib import closing
from datetime import date
import json
from pathlib import Path

from library import ROOT, DATA, connect, worker_lock, now
from automation import cycle
from backtest import state
from usage_report import report as usage
from webapp import atomic_write


def run(start, end, max_tokens, max_groups=12, folder=None):
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date or end_date >= date.today():
        raise ValueError('Backfill requires one closed historical window')
    folder = Path(folder or ROOT / 'outputs/backfills' / f'{start}_{end}')
    folder.mkdir(parents=True, exist_ok=True)
    progress = folder / 'progress.json'
    with closing(connect()) as db:
        initial = usage(db)
        plan = json.loads(progress.read_text('utf-8')) if progress.exists() else {
            'start': start, 'end': end, 'kind': 'weekly', 'cohort': 'baseline',
            'initial_usage': initial['total'], 'initial_unknown': initial['unknown'],
            'runs': [], 'started_at': now()}
        if (plan.get('start'), plan.get('end')) != (start, end):
            raise ValueError('Checkpoint window does not match request')

        def save(status, snapshot):
            current = usage(db)
            plan.update(status=status, updated_at=now(), state=snapshot,
                        spent_tokens=current['total'] - plan['initial_usage'],
                        unknown_calls_added=current['unknown'] - plan['initial_unknown'])
            atomic_write(progress, json.dumps(plan, ensure_ascii=False, indent=2))

        with worker_lock(DATA):
            for attempt in range(100):
                before = state(db, {'kind': 'weekly', 'start': start, 'end': end})
                save('running', {k: before[k] for k in ('records', 'states', 'runnable', 'eligible')})
                if not before['runnable']:
                    save('completed_available_material', {k: before[k] for k in ('records', 'states', 'runnable', 'eligible')})
                    return plan
                if plan['unknown_calls_added']:
                    save('billing_needs_attention', {k: before[k] for k in ('records', 'states', 'runnable', 'eligible')})
                    return plan
                remaining = max_tokens - plan['spent_tokens']
                if remaining <= 0:
                    save('soft_threshold_reached', {k: before[k] for k in ('records', 'states', 'runnable', 'eligible')})
                    return plan
                result = cycle(db, live=True, max_tokens=remaining, max_groups=max_groups,
                               window=(start, end), kind='weekly', cohort='baseline', discover=False)
                plan['runs'].append(result['cycle_id'])
                atomic_write(folder / (result['cycle_id'] + '.json'), json.dumps(result, ensure_ascii=False, indent=2))
                after = state(db, {'kind': 'weekly', 'start': start, 'end': end})
                save('running', {k: after[k] for k in ('records', 'states', 'runnable', 'eligible')})
                print(json.dumps({'start': start, 'end': end, 'spent_tokens': plan['spent_tokens'],
                                  'runnable': after['runnable'], 'eligible': after['eligible']}, ensure_ascii=False), flush=True)
                if result['execution_status'] != 'completed':
                    save('execution_needs_attention', {k: after[k] for k in ('records', 'states', 'runnable', 'eligible')})
                    return plan
                if after['fingerprint'] == before['fingerprint']:
                    save('no_progress_needs_attention', {k: after[k] for k in ('records', 'states', 'runnable', 'eligible')})
                    return plan
            save('iteration_guard', {k: state(db, {'kind': 'weekly', 'start': start, 'end': end})[k]
                                     for k in ('records', 'states', 'runnable', 'eligible')})
            return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--max-tokens', required=True, type=int)
    parser.add_argument('--max-groups', type=int, default=12)
    args = parser.parse_args()
    if args.max_tokens <= 0 or args.max_groups < 1:
        parser.error('Positive token and group limits are required')
    with worker_lock(DATA):
        plan = run(args.start, args.end, args.max_tokens, args.max_groups)
    print(json.dumps({'status': plan['status'], 'spent_tokens': plan['spent_tokens'],
                      'runnable': plan['state']['runnable'], 'published': False}, ensure_ascii=False), flush=True)
    if plan['status'] != 'completed_available_material':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
