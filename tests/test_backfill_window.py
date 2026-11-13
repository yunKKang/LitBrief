import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import backfill_window


class BackfillWindowTests(unittest.TestCase):
    def test_closed_window_reuses_checkpoint_and_never_publishes(self):
        before = {'records': 2, 'states': {'pending': 1}, 'runnable': 1, 'eligible': 0, 'fingerprint': 'a', 'gates': []}
        after = {'records': 2, 'states': {'awaiting_review': 1}, 'runnable': 0, 'eligible': 1, 'fingerprint': 'b', 'gates': []}
        usage = {'total': 0, 'unknown': 0}
        db = object()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(backfill_window, 'connect', return_value=db), \
             patch.object(backfill_window, 'closing', lambda value: _Closed(value)), \
             patch.object(backfill_window, 'worker_lock', _identity_lock), \
             patch.object(backfill_window, 'usage', side_effect=lambda _: dict(usage)), \
             patch.object(backfill_window, 'state', side_effect=[before, after, after]), \
             patch.object(backfill_window, 'cycle', return_value={'cycle_id': 'fixture', 'execution_status': 'completed'}) as cycle:
            result = backfill_window.run('2026-11-01', '2026-11-07', 1000, folder=Path(tmp))
        self.assertEqual(result['status'], 'completed_available_material')
        self.assertEqual(cycle.call_args.kwargs['window'], ('2026-11-01', '2026-11-07'))
        self.assertFalse(cycle.call_args.kwargs['publish'] if 'publish' in cycle.call_args.kwargs else False)

    def test_open_window_is_rejected(self):
        with self.assertRaises(ValueError):
            backfill_window.run('2026-11-08', '2026-11-14', 1000, folder=Path(tempfile.mkdtemp()))


class _Closed:
    def __init__(self, value): self.value = value
    def __enter__(self): return self.value
    def __exit__(self, *args): return False


class _Identity:
    def __enter__(self): return None
    def __exit__(self, *args): return False


def _identity_lock(*args, **kwargs): return _Identity()


if __name__ == '__main__': unittest.main()
