# 出版社访问问题与解决路径

## 当前攻关范围（2026-11-12 更新）

用户确认：先保留已有正常采集路径，只针对剩余缺口解决问题或讨论取舍。OA/公开摘要、网页防护和API权限分开判断；HTTP403不是付费墙证明，不为公共摘要先购买API。已有路径按持续观察维护，不等同于跨周期“稳定零遗漏”验收。

- **暂不新增API适配的13刊**：Nature系列。历史核验已逐刊遍历分页目录跨过窗口起点，部分正式摘要解析已验证；沿用公开目录/文章页，只有新增实际失败才重新进入攻关。
- **保留现有路径、仅对差集排查的4刊**：Science、Science Advances、PNAS、Earth's Future。RSS可读，历史Crossref样本多数带摘要；目前仍缺非Nature完整目录对账，不能把Crossref样本率视为总体覆盖。先核验缺失文章类型与时效，不整刊重做。
- **ES&T入口已修复，继续观察完整性**：ACS迁移后的官方RSS已接入并通过单次解析，后续核对滚动feed差集，不再沿用旧入口失败结论。
- **集中处理的9刊**：ScienceDirect现有9刊重点查正式摘要路径及目录差集。其中Applied Energy和Building and Environment按已同意的月度机制作为首批实施候选，不删除来源。用户同意月度机制，生成器与分刊配置尚未落地。

下表引用2026-10-13至11-11的历史核验，只过滤为当前27刊；不是本轮重新联网跑27刊。摘要数是Crossref原始非空字段计数，分母混合研究与非研究类型，不能称为“研究论文摘要获取率”。“目录跨窗”表示该次遍历成功，不表示长期稳定或此刻最新。

| 当前期刊 | 发现证据 | 历史Crossref摘要/题录 | 当前动作 |
|---|---|---:|---|
| Nature | RSS＋出版社目录跨窗 | 37/399 | 沿用公开路径，暂不攻关API |
| Science | RSS可读；完整目录未验收 | 144/174 | 保留现有路径，仅排查差集 |
| Proceedings of the National Academy of Sciences of the United States of America | RSS可读；完整目录未验收 | 378/430 | 保留现有路径，仅排查差集 |
| Nature Communications | RSS＋出版社目录跨窗 | 340/832 | 沿用公开路径，暂不攻关API |
| Science Advances | RSS可读；完整目录未验收 | 329/336 | 保留现有路径，仅排查差集 |
| Nature Sustainability | RSS＋出版社目录跨窗 | 5/24 | 沿用公开路径，暂不攻关API |
| Nature Energy | RSS＋出版社目录跨窗 | 5/18 | 沿用公开路径，暂不攻关API |
| Nature Climate Change | RSS＋出版社目录跨窗 | 7/33 | 沿用公开路径，暂不攻关API |
| Nature Cities | RSS＋出版社目录跨窗 | 2/13 | 沿用公开路径，暂不攻关API |
| Nature Water | RSS＋出版社目录跨窗 | 1/21 | 沿用公开路径，暂不攻关API |
| One Earth | RSS可读；完整目录未验收 | 0/13 | 按缺口补摘要/核对目录 |
| Environmental Science & Ecotechnology | RSS可读；完整目录未验收 | 0/19 | 按缺口补摘要/核对目录 |
| Renewable and Sustainable Energy Reviews | RSS可读；完整目录未验收 | 0/241 | 按缺口补摘要/核对目录 |
| Earth's Future | RSS可读；完整目录未验收 | 49/51 | 保留现有路径，仅排查差集 |
| Nature Geoscience | RSS＋出版社目录跨窗 | 4/15 | 沿用公开路径，暂不攻关API |
| Nature Reviews Earth & Environment | RSS＋出版社目录跨窗 | 0/8 | 沿用公开路径，暂不攻关API |
| Nature Reviews Clean Technology | RSS＋出版社目录跨窗 | 0/6 | 沿用公开路径，暂不攻关API |
| Environmental Science & Technology | 旧RSS未通；11-12新官方RSS实测解析成功 | 266/300 | 保留ASAP与Current Issue双入口，待完整性对账 |
| Global Environmental Change | RSS可读；完整目录未验收 | 0/30 | 按缺口补摘要/核对目录 |
| Energy Economics | RSS可读；完整目录未验收 | 0/110 | 按缺口补摘要/核对目录 |
| Communications Earth & Environment | RSS＋出版社目录跨窗 | 37/78 | 沿用公开路径，暂不攻关API |
| Nature Food | RSS＋出版社目录跨窗 | 0/13 | 沿用公开路径，暂不攻关API |
| Nature Health | RSS＋出版社目录跨窗 | 5/9 | 沿用公开路径，暂不攻关API |
| The Lancet Planetary Health | RSS可读；完整目录未验收 | 0/18 | 按缺口补摘要/核对目录 |
| Resources, Conservation and Recycling | RSS可读；完整目录未验收 | 0/34 | 按缺口补摘要/核对目录 |
| Applied Energy | RSS可读；完整目录未验收 | 0/485 | 按缺口补摘要/核对目录 |
| Building and Environment | RSS可读；完整目录未验收 | 0/424 | 按缺口补摘要/核对目录 |

### 本轮真实诊断

- ACS官方RSS目录 `https://pubs.acs.org/pages/rss` 指向新入口 `/rss/esthag/asap.xml` 与 `/rss/esthag/currentIssue.xml`。本地保存的2026-11-12 08:16 UTC生产解析记录分别为HTTP200、20条和91条，feed标题均准确匹配ES&T。注册表已替换旧action/showFeed地址，并增加feed刊名校验，错刊返回不能入库。证据在本地 `outputs/access-diagnostics/acs-current/production-feed-check/`；没有把两份feed条数相加当作去重新增量或覆盖率。
- 同日08:17 UTC保存的ScienceDirect ESE及Energy Economics两篇公开文章请求仍返回403。OA属性、浏览器可读性与程序通路继续分开判断；本轮没有重复请求已被拒绝的Elsevier认证接口。
- 同日08:30 UTC通过统一补全队列实测唯一日常待补DOI `10.1016/j.lanplh.2026.101554`：OpenAlex和Europe PMC均HTTP200、DOI匹配、缺摘要字段。它仍是Discussion且尚未核实出版社是否提供正式摘要，保留待补；下一次最早1天后，再失败则3天后，最多3轮。本轮没有调用AI。
- `.env` 中Elsevier配置已与原Markdown内的唯一32位密钥候选做本地布尔比对，结果一致，未使用全文作为key；未打印密钥或原凭证文件。

- 用户确认当前电脑在南大校园网；程序会继承本机HTTP代理。显式禁用本次HTTP代理的对照后，Scopus仍返回401/AUTHORIZATION_ERROR，ScienceDirect仍返回403/AUTHENTICATION_ERROR。共6次串行请求（首测、错误代码诊断、直连对照各2次），无AI调用、无自动重试。不能因此证明学校未订阅或必须付费；也未核实系统TUN/最终机构出口。
- key已配置在被Git忽略的本地.env。原凭证文件仅按文本提取，未执行其中内容，密钥及原始错误响应未公开。
- 当前真正待补仅1条：The Lancet Planetary Health的Discussion。ScienceDirect浏览器本轮明确显示人机挑战，尚不能确定是否有正式摘要，保留待核，不据此买API或伪造摘要。
- 两条PNAS原待补记录已核对成Comment/Commentary：`10.1073/pnas.2634314123`由PubMed精确DOI记录确认Comment且没有Abstract字段；`10.1073/pnas.2634836123`由官方文章页确认Commentary。按既有研究/综述主范围做类型排除，保存来源证据。未把数据库缺摘要泛化成文章无摘要。
- 新类型规则排除明确Comment/Commentary，Discussion和Perspective仍保留判断空间。正式研究论文不能因一次抓取失败被排除。

下一步观察ES&T新RSS的连续窗口差集；ScienceDirect仅对明确相关研究的缺摘要样本核查公开正式摘要及授权接口。已有Nature公开路径不追加付费方案。仅在确认存在“应有摘要的研究论文”而公共路径仍失败时，才申请对应API权限。成本高、收益低的来源讨论月度或退出，不提高并发。

### 已实现的有限补全

`workflow.py enrich` 与旧 `enrich_europepmc.py` 共用队列，处理启用刊、日常候选且已经标题初筛通过的记录。每篇先用Nature正式摘要区（适用时），再按需OpenAlex、Europe PMC；一处成功即停止，全部精确核对DOI。不会请求受限的出版社API，也不会下载全文。

索引延迟、缺字段和临时失败使用最多3轮的持久计划；401/403暂停提供方直到人工恢复，429至少等待1天并遵守更长的Retry-After，重启进程不消除暂停。提供方暂停不阻断其他来源的后续补录；全被暂停的文章不消耗尝试次数。身份冲突或解析失败停止自动尝试。后台展示补全原因与下次时间，`status` 展示提供方暂停；人工核实后用 `resume-enrichment` 恢复，写入运行记录。材料修订会使旧文章计划失效，但不会清除提供方的访问限制。

新增离线回归验证了来源身份、重试上限、跨重启暂停、人工修订竞争和恢复事务；真实单篇结果仍未取得摘要。这些测试不代表全刊覆盖已验收。

API对应期刊、收费判断与申请入口见 [API访问指南](publisher-api-access.md)。部署采用本地/校内采集筛选、外部站点仅接收审核后的静态文件；不以校外机构Token为前提。

---

## 历史诊断记录（保留原30刊范围）


核验日期：2026-11-11。完整逐刊 HTTP 状态、挑战文字、响应摘要哈希和不含 Cookie 的响应头见 [本地诊断报告](../outputs/access-diagnostics/report.md) 与同目录 `latest.json`。本轮环境是本机 Windows 的无认证 HTTP 请求，并非生产服务器。不能据此承诺服务器可用，也不能将可读首页当作目录或摘要已完整覆盖。

## 30 本来源的观察结果

| 期刊／组 | 本轮证据 | 当前结论与下一步 |
|---|---|---|
| Nature、Nature Communications、Nature Sustainability、Nature Energy、Nature Climate Change、Nature Cities、Nature Water、Nature Geoscience、Nature Reviews Earth & Environment、Nature Reviews Clean Technology、Communications Earth & Environment、Nature Food、Nature Health（13 本） | 无认证首页 HTTP 200，未出现挑战文字 | 公开目录可作为独立程序路线；Nature Sustainability 的目录回看已实际跑通。其余来源仍需在目标服务器验证目录分页和正式摘要，不等于 13 本全部完整覆盖。 |
| Science、Science Advances、PNAS（3 本） | HTTP 403；Cloudflare；页面含 `Just a moment`、`Enable JavaScript and cookies to continue` | 明确为访问挑战页面，不是普通“没有权限读全文”的证据。RSS 保留用于发现；摘要可尝试 DOI 精确匹配的 OpenAlex／Europe PMC。挑战不自动破解，正式稳定采集需核查出版方许可入口及服务器环境。 |
| EES、Earth's Future、Environmental Science & Technology（3 本） | 同样返回 Cloudflare 403 与挑战文字 | 与上组相同。EES 的 RSS 旧数据问题另行保留，不能只靠 RSS 认定最新覆盖。ES&T 仍为重点期刊，不因技术访问问题静默移除。 |
| One Earth、Joule、The Innovation、ESE、RSER、Global Environmental Change、Energy Economics、The Lancet Planetary Health、RCR、Applied Energy、Building and Environment（11 本） | 无认证 HTTP 403，响应头为 Cloudflare，但没有明确挑战文字 | 具体拒绝规则未知。不能仅凭 403 断言是机构订阅问题、IP 封禁或限流。本机南京大学会话能打开多本目录／文章，证明存在访问环境差异，未证明高校认证是唯一原因。优先落实 Elsevier 授权 API；保留本机辅助路线，不作为服务器保证。 |

本轮未观察到 HTTP 429 或证书验证失败。诊断器仍分别处理限流、认证要求、TLS、网络异常、未知 403、挑战页及未识别响应；后续不能把它们合并成“反爬”。

## 已找到并修复的程序问题

1. **页面加载未完成**：Energy Economics 早先抓到页面标题 `ScienceDirect`、0 个条目。等待正式文章列表后同一卷期读到 26 条，最新卷期和在线优先合计读到 37 条。修复为等待列表或明确失败提示，0 条未识别页面不代表无新文。
2. **错误假定所有刊都有 articles-in-press 导航**：RCR 首页当前只有 Latest issue，没有该导航。原等待条件超时，修复为接受任一已显示的正式目录入口。RCR 读到 20 条，Applied Energy 39 条，Building and Environment 46 条。这些是页面记录数，不是新增量或精选量。
3. **摘要区混入其他段落**：ScienceDirect 的 Highlights、Graphical abstract 与正式摘要都可能使用 `.abstract.author`。改为核对所属标题只能为 Abstract／Summary，并验证 PII 与 DOI；不能使用 SEO 描述或把全部摘要区文本拼在一起。
4. **列表日期与原始上线日期冲突**：文章页核验发现部分 Joule 条目实际在更早月份上线。文章页日期优先，保存来源并转入历史队列，防止把卷期变化误作新发表。
5. **模型返回引文的校验失败**：逐篇验证；一篇不合格时隔离该篇，其余成功结果保留。未通过内容校验与访问失败分开记录，不自动付费重试。

## 独立服务器路线与可选辅助路线

- `workflow.py collect`：独立 Python 程序，出版社 RSS、Nature 目录、ScienceDirect HTML 入口尝试与 Crossref 补充。不依赖 Codex 或 Luna。
- `publisher_collect.py`：无认证 HTTP 出版社目录采集。One Earth 实测被拒绝，保留失败证据。没有关闭 TLS、伪造机构权限或尝试验证码绕过。
- `workflow.py enrich`：只处理标题初筛后保留的文章；Nature 正式摘要优先，OpenAlex 精确 DOI 补充。本轮 16 次尝试取得 4 条摘要。
- `enrich_europepmc.py`：公开摘要接口，严格匹配 DOI。本轮对剩余 12 条已识别 DOI 的候选未取得摘要；不能由此推断原站没有摘要。
- `browser_collect.py` / `browser_abstract.py`：**可选本机辅助**，通过已安装 Tabbit 和用户已有机构会话读取可见页面；不复制 Cookie，不适合作为无条件服务器部署依赖。失败保留页面和回执供诊断。

## 出版社 API 路线需要的条件

Elsevier 官方文档说明：API key 标识应用，**本身不授予内容权限**；具体 API scope 要有核实后的订阅／集成授权。机构 IP 或经批准的 Institutional Token 是文档列出的认证方式。普通云服务器不能因为用户在浏览器已登录南京大学就自动继承权限。可由用户与图书馆核实可申请的 API 使用权及校外服务器使用方式；获准后再实现与验证接入。

依据：[Elsevier API Authentication](https://dev.elsevier.com/tecdoc_api_authentication.html)、[ScienceDirect APIs](https://dev.elsevier.com/sd_apis.html)、[Europe PMC REST API](https://dev.europepmc.org/RestfulWebService)。本轮已查阅，不把文档存在等同于用户已有权限。

## 上线验收仍未完成

在目标服务器逐刊执行相同诊断，检查目录覆盖窗口、分页、在线日期、摘要来源与异常告警。连续运行几期后比较出版社目录差集。未解决的来源明确显示“部分覆盖／需补材料”，不发布“零遗漏”承诺。当前报告中建议的 API 授权和服务器试跑尚未完成。

## 2026-11-12 Pro报告复核与下一步

报告已读取并与当前实现比对；旧30刊诊断继续作为历史证据，当前27刊。Elsevier Abstract/Article Retrieval META_ABS、Institutional Token及第三方处理条件已从官方文档核对。Springer产品页支持摘要字段和Basic计划，但未证明Nature13刊覆盖。Europe PMC代码此前已经使用core，本轮对剩余3个DOI重新串行查询：均HTTP200、没有精确记录；不能推断原文无摘要。

新增publisher_probe.py用于单DOI、单次请求的权限/字段校验，不自动入库、不存摘要、不送AI。凭证接口缺key，尚未实测；官方机制存在不能标为项目已解决。申请步骤与具体条件见 [API访问指南](publisher-api-access.md)，生产收敛方案见 [架构复查](architecture.md)。授权后先验明一篇，再逐刊验证最新/已知摘要样本和目录差集，最后在目标服务器验收。公共原始响应与本机浏览器证据仍留在outputs，不能提交公开库。
