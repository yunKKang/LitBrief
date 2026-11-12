"""LitBrief reading layout: paper typography and lab-aligned dark colors."""
from html import escape
from library import TOPICS, safe_url, publication_day

SHORT_TOPICS=['产业转型','生产网络','绿色标准','供应链','住房']
SITE_NAME='LitBrief'
SITE_SUBTITLE='文献简报'
def h(value):return escape(str(value or ''),quote=True)

THEME_SCRIPT='''<script>(function(){const root=document.documentElement;let mode='light';try{mode=localStorage.getItem('literaturehot-theme')||'light'}catch(e){}const media=matchMedia('(prefers-color-scheme: dark)');function apply(){root.dataset.theme=mode==='system'?(media.matches?'dark':'light'):mode;document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===mode)))}apply();media.addEventListener('change',apply);document.addEventListener('DOMContentLoaded',()=>{apply();document.querySelectorAll('[data-theme-choice]').forEach(b=>b.addEventListener('click',()=>{mode=b.dataset.themeChoice;try{localStorage.setItem('literaturehot-theme',mode)}catch(e){}apply()}))})})();</script>'''

NAV_SCRIPT="""<script>(function(){
const root=document.documentElement;let collapsed=matchMedia('(max-width: 700px)').matches;
try{const saved=localStorage.getItem('litbrief-nav-collapsed');if(saved!==null)collapsed=saved==='true'}catch(e){}
root.dataset.navCollapsed=String(collapsed);
document.addEventListener('DOMContentLoaded',()=>{
const button=document.querySelector('#nav-toggle');
function apply(){root.dataset.navCollapsed=String(collapsed);button.setAttribute('aria-expanded',String(!collapsed));const label=collapsed?'展开导航':'收起导航';button.setAttribute('aria-label',label);button.dataset.tooltip=label}
apply();button.addEventListener('click',()=>{collapsed=!collapsed;try{localStorage.setItem('litbrief-nav-collapsed',String(collapsed))}catch(e){}apply()});
});})();</script>"""

# Tabler Icons, MIT; vendored paths from layout-sidebar-left-collapse/expand.
# License accompanies both source and static export in tabler-icons-LICENSE.txt.
NAV_ICON = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 6a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2l0 -12"/><path d="M9 4v16"/><path class="nav-collapse-icon" d="M15 10l-2 2l2 2"/><path class="nav-expand-icon" d="M14 10l2 2l-2 2"/></svg>'

def shell(title,body,admin=False,prefix='',preview=False):
    reading='/reading' if admin or preview else prefix+'index.html'
    archive='/issues' if admin or preview else prefix+'archive.html'
    about='/about' if admin or preview else prefix+'about.html'
    nav=f'<a href="{reading}">本期文献</a><a href="{reading}#related">其他相关文献</a><a href="{archive}">往期归档</a><a href="{about}">选文标准</a>'
    if admin or preview:nav+='<details class="workspace-tools"><summary>管理与运行</summary><a href="/queue">候选记录</a><a href="/sources">运行状态</a><a href="/issues">期次管理</a><a href="/retention">数据保留</a></details>'
    if preview or title=='最新一期':
        nav=nav.replace('href="'+reading+'"','aria-current="page" href="'+reading+'"',1)
    elif not admin and title in {'往期文献','选文标准'}:
        current=archive if title=='往期文献' else about
        nav=nav.replace('href="'+current+'"','aria-current="page" href="'+current+'"',1)
    themes='<div class="theme-switch" role="group" aria-label="外观">'+''.join(f'<button type="button" data-theme-choice="{v}" aria-pressed="false">{label}</button>' for v,label in [('light','浅色'),('system','系统'),('dark','深色')])+'</div>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="能源、环境与社会系统的精选文献，每周一期。"><title>{h(title)} · {SITE_NAME} · {SITE_SUBTITLE}</title>{THEME_SCRIPT}{NAV_SCRIPT}<link rel="stylesheet" href="{'/fonts/fonts.css' if admin or preview else prefix+'fonts/fonts.css'}"><link rel="stylesheet" href="{'/style.css' if admin or preview else prefix+'style.css'}"></head><body class="{'admin' if admin else 'publication'}"><a class="skip" href="#main">跳到正文</a><button id="nav-toggle" class="nav-toggle" type="button" aria-controls="site-navigation" aria-expanded="true" aria-label="收起导航" data-tooltip="收起导航">{NAV_ICON}</button><div class="compact-masthead"><a href="{reading}" class="brand">{SITE_NAME}</a></div><aside class="sidebar" id="site-navigation"><a class="brand" href="{reading}">{SITE_NAME}</a><p class="brand-subtitle">{SITE_SUBTITLE}</p><nav aria-label="主导航">{nav}</nav>{themes}</aside><main id="main">{body}<footer><span>{SITE_NAME} · {SITE_SUBTITLE}</span><span>{'内部预览（未发布）' if preview else '短评仅依据摘要，引用请核对原文'}</span></footer></main></body></html>'''

def search_box():
    buttons='<button type="button" data-topic="" aria-pressed="true">全部</button>'+''.join(f'<button type="button" data-topic="{h(t)}" aria-pressed="false">{label}</button>' for t,label in zip(TOPICS,SHORT_TOPICS))
    return f'''<div class="reading-tools"><div class="topic-tabs" role="group" aria-label="研究主题">{buttons}</div><label class="search"><span class="sr-only">查找文献</span><input id="search" type="search" placeholder="搜索标题、期刊与概况"></label></div><p id="search-count" class="muted" aria-live="polite"></p><div id="search-empty" class="empty" hidden><p>没有符合当前筛选条件的文献。</p><button type="button" id="clear-search">清除筛选</button></div><script>
document.addEventListener('DOMContentLoaded',()=>{{
    const input=document.querySelector('#search');let topic='';
    const filter=()=>{{
        const query=input.value.trim().toLowerCase();const filtering=Boolean(query||topic);let n=0;
        document.querySelectorAll('article.paper').forEach(a=>{{
            a.hidden=!(a.textContent.toLowerCase().includes(query)&&(!topic||a.dataset.topics.includes(topic)));
            if(!a.hidden)n++;
        }});
        document.querySelector('#search-count').textContent=filtering?`找到 ${{n}} 篇文献`:'';
        document.querySelector('#search-empty').hidden=!filtering||n>0;
        document.querySelectorAll('.important-section,.related-section').forEach(section=>{{
            const visible=section.querySelectorAll('article.paper:not([hidden])').length;
            section.hidden=filtering&&visible===0;
            const count=section.querySelector('[data-section-count]');if(count)count.textContent=`${{visible}} 篇`;
        }});
    }};
    const reset=()=>{{input.value='';topic='';document.querySelectorAll('[data-topic]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.topic==='')));filter()}};
    input.addEventListener('input',filter);
    document.querySelector('#clear-search').addEventListener('click',()=>{{reset();input.focus()}});
    document.querySelectorAll('[data-topic]').forEach(b=>b.addEventListener('click',()=>{{topic=b.dataset.topic;document.querySelectorAll('[data-topic]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));filter()}}));
    document.querySelectorAll('.reading-map a[href^="#paper-"]').forEach(a=>a.addEventListener('click',()=>{{const target=document.getElementById(a.hash.slice(1));if(target&&target.hidden)reset()}}));
}});</script>'''


def archive_index(issues):
    """An issue directory; article content lives on each issue's own page."""
    body = '<h1>往期归档</h1>'
    if not issues:
        return body + '<p class="empty">第一期正在准备中。</p>'
    months = sorted({i['id'][:7] for i in issues}, reverse=True)
    options = '<option value="">全部月份</option>' + ''.join(
        f'<option value="{h(month)}">{h(month)}</option>' for month in months)
    body += f'''<div class="archive-tools"><label>按月份<select id="archive-month">{options}</select></label><label>查找期次<input id="archive-search" type="search" placeholder="输入日期，如 2026-11"></label></div><p id="archive-count" class="muted" aria-live="polite">共 {len(issues)} 期</p><div class="issue-directory">'''
    for issue in issues:
        label = f'{issue["id"][:7]} 月度补充' if issue['id'].endswith('-monthly') else f'{issue["id"]} 至 {issue["end_date"]}'
        body += f'''<a class="issue-entry" data-month="{h(issue['id'][:7])}" href="issues/{h(issue['id'])}.html"><span class="issue-range">{h(label)}</span><span class="issue-meta">{issue['count']} 篇 <span aria-hidden="true">↗</span></span></a>'''
    body += '''</div><p id="archive-empty" class="empty" hidden>没有符合条件的期次。</p><script>
document.addEventListener('DOMContentLoaded',()=>{
    const month=document.querySelector('#archive-month'),search=document.querySelector('#archive-search');
    function filter(){
        let count=0;const query=search.value.trim().toLowerCase();
        document.querySelectorAll('.issue-entry').forEach(entry=>{
            entry.hidden=Boolean((month.value&&entry.dataset.month!==month.value)||!entry.textContent.toLowerCase().includes(query));
            if(!entry.hidden)count++;
        });
        document.querySelector('#archive-count').textContent=`共 ${count} 期`;
        document.querySelector('#archive-empty').hidden=count>0;
    }
    month.addEventListener('change',filter);search.addEventListener('input',filter);
});</script>'''
    return body


def card(item,prefix='',detail=False,featured=False,preview=False):
    href=f'/paper?id={item["id"]}' if preview else f'{prefix}papers/{item["id"]}.html'
    title=item.get('title_zh') or item['title']
    title_html=h(title) if detail else f'<a href="{href}">{h(title)}</a>'
    tags=item.get('topics',[])
    original=f'<p class="original-title" lang="en">{h(item["title"])}</p>' if item.get('title_zh') else ''
    compact=item['display']!='重点文献' and not detail
    summary=f'<p class="summary">{h(item.get("summary"))}</p>' if not compact else ''
    reason=f'<details class="recommendation"><summary>入选依据</summary><p>{h(item.get("reason"))}</p></details>' if item.get('reason') and not compact else ''
    url=safe_url(item.get('url'))
    external=f'<a class="read-link" href="{h(url)}" target="_blank" rel="noopener noreferrer">阅读原文 <span aria-hidden="true">›</span></a>' if url else ''
    day=publication_day(item)
    stamp=day[5:].replace('-','.') if day else '日期待核'
    if item.get('late_arrival'):stamp+=' · 补录'
    date_attr=f' datetime="{h(day)}" title="{h(day)}"' if day else ''
    return f'''<article id="paper-{item['id']}" class="paper {'compact-paper' if compact else ''}" data-topics="{h(' '.join(tags))}"><time class="paper-date"{date_attr}>{h(stamp)}</time><div class="paper-card"><div class="paper-meta"><span class="journal">{h(item['journal'])}</span><span class="topic-label">{h(' · '.join(tags))}</span></div><h3>{title_html}</h3>{original}{summary}<div class="paper-foot">{reason}{external}</div></div></article>'''

def render_collection(items,start,end,preview=False,prefix='../',review_pending=None):
    important=[i for i in items if i['display']=='重点文献'];related=[i for i in items if i['display']!='重点文献']
    label='<span class="draft-tag">内部预览</span>' if preview else '<span class="draft-tag">'+('已发布' if items else '准备中')+'</span>'
    body=f'<div class="edition-heading"><h1>本期文献</h1><p>{h(start.replace("-","."))} 至 {h(end.replace("-","."))} {label}</p></div>'+search_box()
    if preview and review_pending:
        body+=f'<p class="review-note">{review_pending} 篇尚未确认。<a href="/queue?cohort=monitoring&amp;state=awaiting_review">查看候选</a></p>'
    if items:
        body+='<details class="reading-map"><summary>本期目录 <span>'+str(len(items))+' 篇</span></summary><ol>'+''.join(f'<li><a href="#paper-{p["id"]}">{h(p.get("title_zh") or p["title"])}</a></li>' for p in items)+'</ol></details>'
    body+='<section class="important-section"><div class="section-heading"><h2>重要文献</h2><span>仅依据摘要</span></div>'
    body+=''.join(card(p,prefix,preview=preview) for p in important) or '<div class="empty">本期正在准备中。完成筛选与审核后，文献将在这里展示。</div>'
    body+='</section><section id="related" class="related-section"><div class="section-heading"><h2>其他相关文献</h2><span data-section-count>'+str(len(related))+' 篇</span></div>'
    body+=''.join(card(p,prefix,preview=preview) for p in related) or '<div class="empty">本期暂无其他相关文献。</div>'
    return body+'</section>'
