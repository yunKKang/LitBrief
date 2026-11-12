"""Loopback-only review UI and static publication renderer, with no third-party assets."""
import argparse
from contextlib import closing
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import secrets
import shutil
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from library import DATA, ROOT, TOPICS, build_issue, connect, publish_issue, registry, review, safe_url, ingest, today, now, worker_lock
from datetime import timedelta, date, datetime, timezone


from presentation import h, shell, search_box, card, render_collection


def issue_body(db, issue_id, preview=False):
    issue = db.execute('SELECT * FROM issues WHERE id=?', (issue_id,)).fetchone()
    if not issue:
        raise ValueError('Unknown issue')
    items = [json.loads(r[0]) for r in db.execute('SELECT snapshot FROM issue_items WHERE issue_id=? ORDER BY paper_id', (issue_id,))]
    return render_collection(items, issue_id, issue['end_date'], preview=preview)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def export_site(db, target):
    """Build an immutable release; latest.json advances only after complete validation.

    The returned directory, never this container directory, is the public web root.
    Existing flat exports are preserved during migration and never overwritten.
    """
    target = Path(target).resolve()
    # Never allow a website exporter to write into a source/config/secret directory.
    if target == ROOT or ROOT / 'config' == target or ROOT / 'scripts' == target or target == DATA.resolve():
        raise ValueError('Export requires a dedicated public directory')
    if target.exists() and any(p.name not in {'style.css', 'index.html', 'archive.html', 'about.html', 'issues', 'papers','fonts','tabler-icons-LICENSE.txt','releases','latest.json','latest.json.tmp','worker.lock'} for p in target.iterdir()):
        raise ValueError('Public output contains unexpected files; use a clean dedicated directory')
    target.mkdir(parents=True, exist_ok=True)
    with worker_lock(target):
        release = target / 'releases' / uuid4().hex
        detail = {'directory': str(release), 'issues': []}
        with db:
            cur = db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                             (now(), 'export', 'building', json.dumps(detail)))
        run_id = cur.lastrowid
        try:
            # A single read snapshot prevents concurrent approvals changing this build.
            with db:
                db.execute('BEGIN')
                detail['issues'] = [r[0] for r in db.execute("SELECT id FROM issues WHERE state='published' ORDER BY id")]
                _render_site(db, release)
            detail['files'] = validate_release(release)
            atomic_write(target / 'latest.json', json.dumps({**detail, 'directory': release.relative_to(target).as_posix(), 'run_id': run_id}, ensure_ascii=False, indent=2))
        except Exception as exc:
            detail['error_type'] = type(exc).__name__
            with db:
                db.execute("UPDATE runs SET status='failed',detail=? WHERE id=?", (json.dumps(detail), run_id))
            raise
        with db:
            db.execute("UPDATE runs SET status='ready',detail=? WHERE id=?", (json.dumps(detail), run_id))
        return release


def validate_release(folder):
    """Validate local HTML links and assets; never follow external URLs."""
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(); self.urls = []
        def handle_starttag(self, tag, attrs):
            self.urls.extend(v for k, v in attrs if k in {'href', 'src'} and v)
    for name in ('index.html', 'archive.html', 'about.html', 'style.css', 'fonts/fonts.css', 'fonts/OFL.txt'):
        if not (folder / name).is_file():
            raise ValueError('Incomplete release')
    for path in folder.rglob('*.html'):
        parser = Links(); parser.feed(path.read_text('utf-8'))
        for value in parser.urls:
            url = urlsplit(value)
            if url.scheme or url.netloc or not url.path:
                continue
            dest = (path.parent / url.path).resolve()
            if not dest.is_relative_to(folder.resolve()) or not dest.is_file():
                raise ValueError('Broken local release link')
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file()}


def _render_site(db, target):
    target.mkdir(parents=True, exist_ok=False)
    atomic_write(target / 'style.css', (ROOT / 'web/style.css').read_text(encoding='utf-8'))
    shutil.copyfile(ROOT/'web/tabler-icons-LICENSE.txt',target/'tabler-icons-LICENSE.txt')
    font_dir=ROOT/'web/fonts'
    if (font_dir/'manifest.json').exists():
        font_files=json.loads((font_dir/'manifest.json').read_text('utf-8'))['files']+['fonts.css','OFL.txt']
        (target/'fonts').mkdir(exist_ok=True)
        for name in font_files:
            source=(font_dir/name).resolve()
            if source.parent!=font_dir.resolve():raise ValueError('Invalid font asset path')
            shutil.copyfile(source,target/'fonts'/name)
    issues = db.execute("SELECT * FROM issues WHERE state='published' ORDER BY id DESC").fetchall()
    for issue in issues:
        atomic_write(target / 'issues' / (issue['id'] + '.html'), shell(issue['id'], issue_body(db, issue['id']), prefix='../'))
    for row in db.execute("SELECT snapshot,issue_id FROM issue_items JOIN issues ON issues.id=issue_id WHERE issues.state='published'"):
        item = json.loads(row['snapshot'])
        body = f'<p><a href="../issues/{h(row["issue_id"])}.html">返回本期</a></p>' + card(item, '../', detail=True)
        atomic_write(target / 'papers' / (str(item['id']) + '.html'), shell(item['title'], body, prefix='../'))
    archive = '<h1>往期文献</h1><p class="lead">每四天一期，积累可回看的研究线索。</p>'
    archive += ''.join(f'<p class="issue-link"><a href="issues/{h(i["id"])}.html">{h(i["id"])} 至 {h(i["end_date"])}</a></p>' for i in issues) or '<p class="empty">第一期正在准备中。</p>'
    archive += search_box()
    for row in db.execute("SELECT snapshot FROM issue_items JOIN issues ON issues.id=issue_id WHERE issues.state='published' ORDER BY issue_id DESC,paper_id"):
        archive += card(json.loads(row[0]))
    atomic_write(target / 'archive.html', shell('往期文献', archive))
    if issues:
        # Render the same content, adjusting links to the root rather than redirecting.
        latest = issue_body(db, issues[0]['id']).replace('href="../papers/', 'href="papers/')
    else:
        latest = render_collection([], (today()-timedelta(days=3)).isoformat(), today().isoformat(), prefix='')
    atomic_write(target / 'index.html', shell('最新一期', latest))
    about = '<h1>选文标准</h1><p class="lead">以期刊影响力和研究价值为起点，以主题相关性决定收录。</p><h2>关注的问题</h2><ul>' + ''.join(f'<li>{h(t)}</li>' for t in TOPICS) + '</ul><p>水风险作为相关研究的情境，不设独立核心方向。单篇需有明确的产业、供应网络、政策或住房系统问题及实质贡献。工程与环境工程技术、单厂运行控制、材料性能、土壤过程和工艺优化不纳入；附带节能减排或生命周期评价不能替代系统研究。新闻和普通社论不纳入。</p><h2>如何阅读</h2><p>每四天一期，不设固定篇数。重要文献附中文短评，其他相关文献以列表展示。AI 辅助筛选与概括，前几期人工审核。短评仅依据摘要，无法替代全文阅读或质量审查。</p><h2>关注期刊</h2><ul class="journal-list">' + ''.join(f'<li>{h(s["title"])}</li>' for s in registry().values()) + '</ul>'
    atomic_write(target / 'about.html', shell('选文标准', about))


STATES = {'pending': '待 AI 筛选', 'awaiting_abstract': '待补摘要', 'screening': '调用中／中断待核',
          'needs_review': '材料变化待复审',
          'awaiting_review': '推荐待审', 'approved': '已通过', 'rejected': '人工排除', 'ai_error': '调用或校验失败',
          'ai_excluded':'AI 排除抽检', 'needs_material':'材料待核','type_excluded':'类型排除'}


def reading_preview(db):
    items=[];pending=0
    for row in db.execute("SELECT * FROM papers WHERE cohort='monitoring' AND state IN ('awaiting_review','approved') ORDER BY id"):
        data,result=json.loads(row['data']),json.loads(row['result'] or '{}')
        if result.get('decision')!='include':continue
        source=registry().get(row['journal'])
        if not source:continue
        if row['state']=='awaiting_review':pending+=1
        items.append({'id':row['id'],'title':data['title'],'title_zh':result.get('title_zh',''),
                      'journal':source['title'],'display':source['display'],'dates':data.get('dates',{}),
                      'summary':result.get('summary',''),'reason':result.get('reason',''),'topics':json.loads(row['topics']),
                      'url':('https://doi.org/'+data['doi']) if data.get('doi') else next((u for u in data.get('urls',[]) if safe_url(u)), '')})
    start,end=preview_window(db)
    return render_collection(items,start,end,preview=True,review_pending=pending)


def preview_window(db):
    # A clock tick must not make yesterday's unrefreshed data look like today's issue.
    for row in db.execute("SELECT detail FROM runs WHERE kind='collect' ORDER BY id DESC"):
        try:
            end=date.fromisoformat(json.loads(row['detail'])['end'])
            return (end-timedelta(days=3)).isoformat(),end.isoformat()
        except (ValueError,KeyError,TypeError):
            continue
    row=db.execute('SELECT max(first_seen) FROM papers').fetchone()
    end=datetime.fromisoformat(row[0]).astimezone(timezone(timedelta(hours=8))).date() if row[0] else today()
    return (end-timedelta(days=3)).isoformat(),end.isoformat()


def review_index(db, query):
    state = query.get('state', ['awaiting_review'])[0]
    cohort = query.get('cohort', ['monitoring'])[0]
    scopes={'monitoring':'日常监测','baseline':'历史校准样本','date_unverified':'日期待核','inactive_source':'停用来源留档','all':'全部记录'}
    if state not in STATES and state != 'all' or cohort not in scopes:
        raise ValueError('Invalid queue selection')
    page=max(1,int(query.get('page',['1'])[0]))
    terms=[];params=[]
    if state!='all':terms.append('state=?');params.append(state)
    if cohort!='all':terms.append('cohort=?');params.append(cohort)
    where=(' WHERE '+' AND '.join(terms)) if terms else ''
    total=db.execute('SELECT count(*) FROM papers'+where,params).fetchone()[0]
    rows=db.execute('SELECT * FROM papers'+where+' ORDER BY id DESC LIMIT 30 OFFSET ?',params+[(page-1)*30]).fetchall()
    cwhere='' if cohort=='all' else ' WHERE cohort=?'
    cparams=[] if cohort=='all' else [cohort]
    counts={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM papers'+cwhere+' GROUP BY state',cparams)}
    cohorts={r[0]:r[1] for r in db.execute('SELECT cohort,count(*) FROM papers GROUP BY cohort')}
    body='<div class="workspace-heading"><div><h1>审核文献</h1><p>先看 AI 推荐，再抽检排除结果。原始候选不等于阅读清单。</p></div><a class="button" href="/reading">预览待审试刊</a></div>'
    body+='<div class="queue-summary">'+''.join(f'<a href="/?cohort={cohort}&state={key}">{label}<strong>{counts.get(key,0)}</strong></a>' for key,label in [('awaiting_review','推荐待审'),('awaiting_abstract','待补材料'),('ai_excluded','排除结果可抽检')])+'</div>'
    body+='<div class="cohort-nav">'+''.join(f'<a href="/?cohort={key}&state=all" {"aria-current=page" if cohort==key else ""}>{label} {sum(cohorts.values()) if key=="all" else cohorts.get(key,0)}</a>' for key,label in scopes.items())+'</div>'
    body+='<div class="filters">'+''.join(f'<a href="/?cohort={cohort}&state={key}" {"aria-current=page" if state==key else ""}>{label} {counts.get(key,0)}</a>' for key,label in STATES.items())+'</div>'
    if cohort=='baseline':body+='<p class="notice">历史核验资料保留用于校准，不自动纳入本期推荐。可逐篇查看或抽样处理。</p>'
    if cohort=='date_unverified':body+='<p class="notice">尚未核实在线发表日期，未视为本期新增，也未因缺少日期而排除。</p>'
    if cohort=='inactive_source':body+='<p class="notice">这些期刊已停止跟踪。原始记录和既有筛选结果保留用于回溯，不再进入日常补摘要、AI 筛选或组期。</p>'
    for row in rows:
        data,result=json.loads(row['data']),json.loads(row['result'] or '{}')
        title=result.get('title_zh') or data['title']
        original=f'<p class="original-title">{h(data["title"])}</p>' if result.get('title_zh') else ''
        body+=f'<article class="paper"><div class="paper-meta"><span class="journal">{h(data["journal_title"])}</span><span class="decision">{h(STATES[row["state"]])}</span></div><h3><a href="/paper?id={row["id"]}">{h(title)}</a></h3>{original}<p class="summary">{h(result.get("summary") or result.get("reason") or "等待处理，尚无相关性判断。")}</p><div class="review-action"><span>{h(result.get("basis", "原始候选"))} · #{row["id"]}</span><a href="/paper?id={row["id"]}">查看依据与审核 ›</a></div></article>'
    if not rows:body+='<div class="empty">这个队列目前没有文献。你可以切换其他处理状态。</div>'
    body+=f'<div class="pagination">共 {total} 篇 · 第 {page} 页 '
    if page>1:body+=f'<a href="/?cohort={cohort}&state={state}&page={page-1}">上一页</a> '
    if page*30<total:body+=f'<a href="/?cohort={cohort}&state={state}&page={page+1}">下一页</a>'
    return body+'</div>'


def paper_form(db, paper_id, token):
    row = db.execute('SELECT * FROM papers WHERE id=?', (paper_id,)).fetchone()
    if not row:
        raise ValueError('Unknown paper')
    data, result = json.loads(row['data']), json.loads(row['result'] or '{}')
    selected = json.loads(row['topics'])
    material_labels={'not_attempted':'尚未尝试补全','available':'已有正式摘要','not_available':'已尝试的补充源未提供摘要','access_blocked':'补充源访问受限','request_failed':'请求失败，尚未确认可得性','parser_failed':'摘要解析待核','record_not_found':'补充源尚无该记录','abstract_missing':'已匹配记录，但无摘要字段','rate_limited':'来源限流，延后补充','content_mismatch':'返回身份或摘要冲突，需人工核对','provider_deferred':'来源已暂停，本轮延后处理'}
    schedule=db.execute('SELECT attempts,next_attempt_at,last_status FROM enrichment_schedule WHERE paper_id=?',(paper_id,)).fetchone()
    evidence = result.get('evidence', '')
    if isinstance(evidence, list):
        evidence = '\n\n'.join(evidence)
    url = ('https://doi.org/' + data['doi']) if data.get('doi') else next((safe_url(u) for u in data.get('urls', []) if safe_url(u)), '')
    body = f'<p class="eyebrow">{h(data["journal_title"])} · #{row["id"]} · {h(STATES[row["state"]])}</p><h1 class="article-title">{h(data["title"])}</h1>'
    if row['material_changed']:
        body += '<p class="notice">原始材料已变化，旧判断已留档。人工排除继续保留；已通过的未发布文章需要重新审核后才能组期。</p>'
    if url:
        body += f'<p><a href="{h(url)}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a></p>'
    body += f'<div class="review-grid"><section><h2>摘要依据</h2><p class="muted">{h(data.get("abstract_source") or "缺少可核实摘要")}</p><div class="abstract">{h(data.get("abstract") or "保留待补材料，不凭标题生成研究结论。")}</div><h2>AI 判断</h2><p>{h(result.get("decision"))} · {h(result.get("reason"))}</p><blockquote>{h(evidence)}</blockquote></section>'
    body += f'<section><h2>人工审核</h2><form method="post" action="/review"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="id" value="{paper_id}"><input type="hidden" name="revision" value="{row["revision"]}"><label>中文概况<textarea name="summary" rows="8">{h(result.get("summary", ""))}</textarea></label><fieldset><legend>研究主题（可多选）</legend>'
    body += ''.join(f'<label class="check"><input type="checkbox" name="topics" value="{h(t)}" {"checked" if t in selected else ""}>{h(t)}</label>' for t in TOPICS)
    body += '</fieldset><label>审核备注<textarea name="note" rows="2"></textarea></label><div class="actions"><button name="action" value="approve">通过审核</button><button class="secondary" name="action" value="reject">排除</button><button class="secondary" name="action" value="requeue">重新排队</button></div><p class="muted">通过审核后仍需组期与发布。重新排队不会立即调用模型。</p></form></section></div>'
    if not data.get('abstract'):
        body += f'<section><h2>补充经核实的摘要</h2><p class="notice">补全状态：{h(material_labels.get(row["enrichment_state"],row["enrichment_state"]))}。这不代表出版社一定没有摘要。{"当前还需核实 DOI 或文章页面。" if not data.get("doi") else ""}</p><p>可在高校认证浏览器中核对原文后粘贴。只填正式 Abstract，不填 RSS 导读或搜索摘要。</p><form method="post" action="/abstract"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="id" value="{paper_id}"><input type="hidden" name="revision" value="{row["revision"]}"><label>原文网页 URL<input type="url" name="source_url" required></label><label>正式摘要<textarea name="abstract" rows="8" required></textarea></label><label class="check"><input type="checkbox" name="verified" value="yes" required>已核对标题和 DOI，确认摘要属于本篇文章</label><button>保存摘要并进入待筛选队列</button></form></section>'
    if schedule and row['state']=='awaiting_abstract':
        body+=f'<p class="notice">补充尝试 {schedule["attempts"]} 次 · '+(f'下次尝试：{h(schedule["next_attempt_at"])}' if schedule['next_attempt_at'] else '自动尝试已暂停，请人工核对来源或材料。')+'</p>'
    return body


def issues_page(db, token):
    body = f'<h1>期次管理</h1><p class="lead">每四个自然日一期，已发布内容保留稳定链接。</p><form method="post" action="/prepare"><input type="hidden" name="csrf" value="{token}"><button>将已通过文献加入本期草稿</button></form>'
    last_export = db.execute("SELECT status,detail FROM runs WHERE kind='export' ORDER BY id DESC LIMIT 1").fetchone()
    if last_export:
        detail = json.loads(last_export['detail'])
        label = {'ready':'完整版本已生成','building':'导出未完成，请检查进程后重试','failed':'导出失败，上一完整版本保持不变'}[last_export['status']]
        body += f'<p class="notice">{label}。可部署路径：{h(detail.get("directory")) if last_export["status"]=="ready" else "本次无可部署版本"}。内容确认与导出状态分别记录。</p>'
    body += f'<form method="post" action="/export"><input type="hidden" name="csrf" value="{token}"><button class="secondary">重新导出已确认期次</button></form>'
    for row in db.execute('SELECT * FROM issues ORDER BY id DESC'):
        count = db.execute('SELECT count(*) FROM issue_items WHERE issue_id=?', (row['id'],)).fetchone()[0]
        body += f'<section><h2>{h(row["id"])} 至 {h(row["end_date"])}</h2><p>{"已发布" if row["state"] == "published" else "草稿"} · {count} 篇 <a href="/preview?id={h(row["id"])}">预览本期</a></p>'
        if row['state'] == 'draft':
            body += f'<form method="post" action="/publish"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="id" value="{h(row["id"])}"><label class="check"><input type="checkbox" name="confirmed" value="yes" required>我已审核本期，确认生成公开页面</label><button>发布至本地公开目录</button></form><form method="post" action="/discard"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="id" value="{h(row["id"])}"><button class="secondary">撤回草稿，继续编辑</button></form>'
        body += '</section>'
    return body


def sources_page(db):
    diagnostic_file=ROOT/'outputs/access-diagnostics/latest.json'
    diagnostics=json.loads(diagnostic_file.read_text('utf-8')) if diagnostic_file.exists() else {}
    probes={p['seed_issn']:p for p in diagnostics.get('journals',[])}
    labels={'accessible':'首页可读','browser_challenge':'访问挑战','forbidden_unexplained':'403，原因未明','rate_limited':'限流','authentication_required':'需要认证','certificate_error':'证书问题','network_error':'网络问题'}
    body = '<h1>来源与运行</h1><p class="notice">独立采集程序不依赖 Codex。Nature 目录已有自动化；本机认证浏览器仅用于辅助补材料，不代表服务器能无人值守。以下访问状态来自本机无认证 HTTP 检查，尚非生产服务器验证。</p><div class="table-wrap"><table><caption>日常监测队列与来源状态（历史样本和日期待核不计入本表）</caption><thead><tr><th>期刊</th><th>候选</th><th>待补摘要</th><th>待审／通过</th><th>排除</th><th>首页访问诊断</th></tr></thead><tbody>'
    for seed, source in registry().items():
        counts = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM papers WHERE journal=? AND cohort='monitoring' GROUP BY state", (seed,))}
        probe=probes.get(seed,{})
        diagnostic=f'<details><summary>{h(labels.get(probe.get("classification"),probe.get("classification","未测试")))}</summary><p>HTTP {h(probe.get("status"))} · {h(", ".join(probe.get("signals",[])))}</p><p>{h(probe.get("solution"))}</p></details>'
        body += f'<tr><th>{h(source["title"])}</th><td>{sum(counts.values())}</td><td>{counts.get("awaiting_abstract",0)}</td><td>{counts.get("approved",0)+counts.get("awaiting_review",0)}</td><td>{counts.get("rejected",0)+counts.get("ai_excluded",0)+counts.get("type_excluded",0)}</td><td>{diagnostic}</td></tr>'
    body += '</tbody></table></div><h2>模型用量</h2>'
    pending_requests = db.execute("SELECT id,mode,status,usage_json FROM ai_requests WHERE status NOT IN ('applied','acknowledged') ORDER BY created_at").fetchall()
    if pending_requests:
        body += '<h3>调用恢复待处理</h3><p>先恢复已保存响应；没有响应时核对供应商计费，再明确解除暂停。重新排队不会绕过未解决的调用。</p><ul>'
        body += ''.join(f'<li>{h(r["id"])} · {h(r["mode"])} · {h(r["status"])} · {"用量已记录" if r["usage_json"] and r["usage_json"]!="null" else "计费待核"}</li>' for r in pending_requests) + '</ul>'
    usage_file = ROOT / 'outputs/ai-trial/usage.jsonl'
    totals = {'calls': 0, 'input': 0, 'output': 0, 'reasoning': 0, 'cached': 0, 'unknown': 0, 'tier_mismatch': 0}
    if usage_file.exists():
        for line in usage_file.read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # A concurrently written final line may be incomplete; next load will include it.
            totals['calls'] += 1
            usage = event.get('usage') or {}
            if 'input_tokens' not in usage or 'output_tokens' not in usage:
                totals['unknown'] += 1
            totals['input'] += usage.get('input_tokens', 0)
            totals['output'] += usage.get('output_tokens', 0)
            totals['reasoning'] += (usage.get('output_tokens_details') or {}).get('reasoning_tokens', 0)
            totals['cached'] += (usage.get('input_tokens_details') or {}).get('cached_tokens', 0)
            totals['tier_mismatch'] += event.get('requested_tier') == 'fast' and event.get('returned_tier') not in {'fast','priority'}
    body += f'<p>调用 {totals["calls"]} 次 · 输入 {totals["input"]:,} + 输出 {totals["output"]:,} = {totals["input"]+totals["output"]:,} tokens</p><p class="muted">其中缓存 {totals["cached"]:,}、推理 {totals["reasoning"]:,}（均已包含在上方）；{totals["unknown"]} 次未返回完整用量，fast 档位未获确认 {totals["tier_mismatch"]} 次。校验失败但返回用量的调用也计入；金额以供应商账单为准。</p><h2>最近运行</h2>'
    for row in db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 10'):
        body += f'<p>{h(row["at"])} · {h(row["kind"])} · {h(row["status"])}</p><pre>{h(row["detail"])}</pre>'
    return body


def make_handler(db_path, token, public):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, content_type='text/html; charset=utf-8', cache_control='no-store'):
            data = body if isinstance(body,bytes) else body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', cache_control)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, 'Invalid host')
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            try:
                if path.path == '/style.css':
                    return self.send(200, (ROOT / 'web/style.css').read_text(encoding='utf-8'), 'text/css; charset=utf-8')
                if path.path.startswith('/fonts/'):
                    font_dir=(ROOT/'web/fonts').resolve()
                    name=path.path.removeprefix('/fonts/')
                    asset=(font_dir/name).resolve()
                    manifest=json.loads((font_dir/'manifest.json').read_text('utf-8'))
                    if name not in manifest['files']+['fonts.css','OFL.txt'] or asset.parent!=font_dir:
                        return self.send(404,'Not found')
                    kind='font/woff2' if asset.suffix=='.woff2' else 'text/css; charset=utf-8' if asset.suffix=='.css' else 'text/plain; charset=utf-8'
                    cache='public, max-age=31536000, immutable' if asset.suffix=='.woff2' else 'no-store'
                    return self.send(200,asset.read_bytes(),kind,cache)
                with closing(connect(db_path)) as db:
                    if path.path == '/reading':
                        return self.send(200, shell('待审试刊', reading_preview(db), preview=True))
                    elif path.path == '/':
                        body = review_index(db, query)
                    elif path.path == '/paper':
                        body = paper_form(db, int(query['id'][0]), token)
                    elif path.path == '/issues':
                        body = issues_page(db, token)
                    elif path.path == '/sources':
                        body = sources_page(db)
                    elif path.path == '/preview':
                        body = issue_body(db, query['id'][0], preview=True)
                        # Draft previews stay on authenticated-by-loopback review routes.
                        import re
                        body = re.sub(r'href="../papers/(\d+)\.html"', r'href="/paper?id=\1"', body)
                    else:
                        return self.send(404, 'Not found')
                self.send(200, shell('审核工作台', body, admin=True))
            except (ValueError, KeyError) as exc:
                self.send(400, shell('请求未完成', f'<h1>请求未完成</h1><p>{h(exc)}</p><a href="/">返回工作台</a>', admin=True))

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, 'Invalid host')
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
                return self.send(403, 'Invalid origin')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 100000:
                    return self.send(413, 'Invalid body size')
                form = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
                if not secrets.compare_digest(form.get('csrf', [''])[0], token):
                    return self.send(403, 'Invalid CSRF token')
                with closing(connect(db_path)) as db:
                    if self.path == '/review':
                        review(db, int(form['id'][0]), form['action'][0], form.get('summary', [''])[0], form.get('topics', []), form.get('note', [''])[0], int(form['revision'][0]))
                    elif self.path == '/prepare':
                        build_issue(db)
                    elif self.path == '/abstract':
                        row = db.execute('SELECT * FROM papers WHERE id=?', (int(form['id'][0]),)).fetchone()
                        if not row or row['revision'] != int(form['revision'][0]):
                            raise ValueError('Paper changed; reload before saving')
                        url = safe_url(form.get('source_url', [''])[0])
                        abstract = form.get('abstract', [''])[0].strip()
                        if not url or not abstract or form.get('verified') != ['yes']:
                            raise ValueError('A verified abstract and original source URL are required')
                        data = json.loads(row['data'])
                        if data.get('abstract'):
                            raise ValueError('This paper already has an abstract; reload before editing')
                        with db:
                            ingest(db, {**data, 'abstract': abstract, 'abstract_source': 'Publisher abstract, manually verified',
                                        'abstract_url': url}, 'manual-editor')
                    elif self.path == '/publish':
                        if form.get('confirmed') != ['yes']:
                            raise ValueError('Confirm review before publishing')
                        publish_issue(db, form['id'][0])
                        export_site(db, public)
                    elif self.path == '/export':
                        export_site(db, public)
                    elif self.path == '/discard':
                        with db:
                            issue = db.execute('SELECT state FROM issues WHERE id=?', (form['id'][0],)).fetchone()
                            if not issue or issue['state'] != 'draft':
                                raise ValueError('Only drafts can be discarded')
                            db.execute('DELETE FROM issue_items WHERE issue_id=?', (form['id'][0],))
                            db.execute('DELETE FROM issues WHERE id=?', (form['id'][0],))
                    else:
                        return self.send(404, 'Not found')
                self.send_response(303)
                self.send_header('Location', '/' if self.path in {'/review', '/abstract'} else '/issues')
                self.end_headers()
            except (ValueError, KeyError) as exc:
                self.send(400, shell('操作未完成', f'<h1>操作未完成</h1><p>{h(exc)}</p><a href="/">返回工作台</a>', admin=True))
            except (OSError, RuntimeError):
                self.send(503, shell('导出未完成', '<h1>操作未完成</h1><p>已确认内容保留，上一完整版本不变。请检查运行状态后重新导出。</p><a href="/issues">返回期次管理</a>', admin=True))

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--db', type=Path, default=DATA / 'library.sqlite3')
    p.add_argument('--public-output', type=Path, default=ROOT / 'outputs/public')
    args = p.parse_args()
    server = HTTPServer(('127.0.0.1', args.port), make_handler(args.db, secrets.token_urlsafe(32), args.public_output))
    print(f'Review UI: http://127.0.0.1:{args.port} (loopback only)', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
