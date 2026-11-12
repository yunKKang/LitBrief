# LitBrief · 文献简报

面向能源、环境、产业转型、生产网络、供应链和住房研究的定期文献简报。以高质量来源和主题相关性筛选，提供中文短概况、原文链接和按期归档，不按固定篇数凑数。

最终目标是独立运行的agent闭环：自动采集、筛选、证据核验、组期和发布，用户主要针对最终结果反馈；人工逐篇审核只用于当前测试校准。

本项目当前是**本地试运行版本**。已实现采集、候选库、AI筛选与核验、反馈、自动准入及四天期次的本地导出；独立run-cycle入口已提供。真实自动模式仍关闭，远端部署、全来源稳定覆盖和月度期次尚未完成。公开代码不包含本地文献库、原始摘要、API密钥或机构会话。

## 当前边界

- 27本启用期刊：21本重点来源、6本相关列表来源。Joule、The Innovation、EES已退出常规跟踪，历史记录留档。
- 五个研究主题：产业与基础设施绿色转型；全球生产网络与环境责任；国际绿色产品标准；供应链管理与韧性；住房与建成环境。AI／算力扩张的环境影响纳入系统问题，不扩展到所有AI应用。
- 研究论文与实质综述优先。工程／环境工程技术、土壤过程、工艺与单项性能优化不因附带节能减排或LCA而入选。
- 类型预筛 → 标题AI初筛 → 补正式摘要 → 摘要AI判断和概况 → 核验与准入 → 四天期次快照。测试期支持人工审核，自动模式按代码门槛准入。缺摘要与不相关分别记录。
- AI默认使用供应商标识 `gpt-6-luna`、`high`、`service_tier=default`；实际网络串行，允许上限5并发但未启用。付费失败不自动重试或换模型。
- 最新本地快照（2026-11-12）：193条日常候选中7条已人工通过、8条人工排除、156条AI排除、22条类型排除；另375条停用来源记录留档。待补队列变空来自审核后状态变化，不证明出版社问题已解决。克隆仓库不附带这些数据。

## 安装与本地运行

Python 3.10+，正常运行只需标准库。无需Node、数据库服务或字体CDN。

```powershell
# 在自己的克隆目录执行。已有.env时不要覆盖。
Copy-Item .env.example .env
# 本地填写Responses供应商URL和API key；示例URL是不可用占位符。
python scripts/webapp.py --port 8766
```

审核后台：http://127.0.0.1:8766/ ；待审阅读预览：http://127.0.0.1:8766/reading 。干净克隆没有真实文章，显示空状态。审核服务只监听本机，不能直接开放公网。

需要虚构数据验证界面时使用隔离库：

```powershell
python tests/create_ui_fixture.py
python scripts/webapp.py --port 8768 --db outputs/ui-check/library.sqlite3 --public-output outputs/ui-check/public
```

## 日常流程

首次真实运行前请阅读选刊规则和来源访问状态，准备自己的授权接口；以下网络与AI操作不要在不了解范围时自动执行。先用单刊、小批量验证。

```powershell
# 查看状态
python scripts/workflow.py status
# 发现文章：官方RSS和已核验目录优先，Crossref补充；当前覆盖仍部分完成
python scripts/workflow.py collect --only 2398-9629
python scripts/prefilter.py
# 以下两条batch命令会调用付费模型；阈值在请求间检查
python scripts/batch_screen.py --titles-only --group-size 12 --max-tokens 15000
python scripts/workflow.py enrich --max-items 5
python scripts/batch_screen.py --max-tokens 15000
# 人工在后台复审、组期和确认发布后，导出已发布快照
$release = python scripts/workflow.py export | ConvertFrom-Json
python -m http.server 8767 --bind 127.0.0.1 --directory $release.public_directory
```

标题初筛不生成研究结论；短评仅依据正式摘要，并保存逐字支持片段。模型声明相关不等于研究质量已验证。使用预算停止后，未处理候选保留队列，不形成公开篇数限制。

`workflow.py screen --ids ...` 使用同一批处理引擎、每组1篇，并遵守标题先筛和请求恢复约束；`screen_paper.py` 是独立单篇诊断工具，不写候选库，日常不作为调度入口。日常统一使用上面的标题→摘要批处理顺序。后台运行先结束再启动下一阶段，不并行消费队列。来源退出后运行 `python scripts/workflow.py sync-sources`，原始资料和AI结果保留，退出前队列记录在运行日志。

## 反馈保护与故障恢复

人工排除在材料更新后继续有效；人工通过的未发布文章若材料变化，会从草稿移出并进入“材料变化待复审”。旧材料和判断留在私有运行记录，旧发表快照保持不变。只有明确的重新排队操作才撤销原人工决定；这是对用户主动反馈的保护，不代表最终自动闭环要求逐篇人工审批。

批量AI发送前记录请求ID、输入指纹和状态；响应原子落盘后可离线恢复。原始响应、请求输入与用量记录均属于私有材料，不含API key，不导出。超时或无完整响应时不会自动再次付费，未处理调用阻止直接重排。当前恢复命令如下：

```powershell
python scripts/workflow.py status
# 已有完整响应：重新验证、写入未被人工修改的文章，不联网、不收费
python scripts/workflow.py recover-ai --request-id <调用编号>
# 无法恢复且已经核对计费／材料后：记录处置依据，再通过后台明确重新排队
python scripts/workflow.py acknowledge-ai --request-id <调用编号> --note '填写核对结果'
```

第二条处置命令自身不会发请求。新调用恢复记录覆盖本次升级后的批处理及workflow筛选入口；旧日志不能重建当时未保存的响应。匹配现有完整缓存时可离线复用，模型结果仍需重新校验。token阈值保持请求间停止语义，不承诺硬金额上限。

内容确认与导出完成分开记录。导出失败可在期次页“重新导出已确认期次”，或运行 `workflow.py export`；不撤销内容快照，不替换上一完整站点。Windows预览绑定具体release，不需符号链接；生产Nginx仍仅指向核验后的release。后台显示导出状态与路径。进程被强制终止留下容器 `worker.lock` 时，先核实没有导出进程再恢复，不能删除正在使用的锁。

## Agent抽检、概况核验与最终反馈

已实现可复现的排除抽检和概况核验，仍复用同一Responses模型、串行请求、预算停止阈值与离线恢复账本。抽检按标题排除／摘要排除／类型排除分层，保存候选集合、种子、规则哈希、原判断及材料版本；定向难例与随机样本分开。缺摘要项直接标为system／材料不足，不调用模型。

```powershell
# 只准备样本，不联网、不收费；返回batch_id
python scripts/workflow.py prepare-quality --mode audit --seed trial-01 --count 12
python scripts/workflow.py prepare-quality --mode verify --seed trial-01 --count 12
# 下列命令显式调用付费模型，默认最多一组、每组三篇
python scripts/workflow.py check-quality --batch-id <批次编号> --max-tokens 6000 --max-groups 1 --group-size 3
python scripts/workflow.py quality-report --batch-id <批次编号>
```

verify仅处理有正式摘要与概况的最终候选，检查研究对象、方法、情景／实绩、相关／因果、强度／总量、范围、数字和不确定性。新任务的摘要片段必须逐字匹配；结果记录为agent判断，不能视为人工真值或研究质量认证。材料／反馈变化后旧核验标为过期。重复执行已完成批次不再次付费；核验不自动批准、发布或改写原筛选判断。疑似误排的受控回流仍待实现。自动准入和本地流程入口已提供，但当前未启用真实自动发布，远端上传另行部署。

本地后台“来源与运行”提供批次报告；文章详情新增“最终结果反馈（可选）”。用户无需参加初筛，可选择值得读、不相关、相关但本期不优先、内容重复／增量不足、概况有问题。只有“不相关”写为受保护的人工排除；概况有问题会阻止尚未发布稿件继续组期。已发布快照不改写，反馈和英文核验依据均不公开。当前反馈入口是本地后台，未新增公网写接口。

2026-11-12小批量接入验收：3篇概况被agent判为摘要支持，使用5,785 tokens；3条排除抽样中2条缺摘要免调用，另1条被agent判为维持排除，使用2,674 tokens；总计两次串行请求、8,459 tokens。无新文章获批或发布，不由这组样本推算总体误排率。反馈表单已用隔离库在Tabbit中实际提交并重新读取。

## 独立运行入口与自动准入

```powershell
# 默认离线影子检查：不采集、不调用AI、不改审批或期次，只记录准入报告
python scripts/workflow.py run-cycle
# 显式执行网络与付费处理，但不自动批准或发布；各AI阶段共享同一token阈值
python scripts/workflow.py run-cycle --live --max-tokens 15000 --max-groups 1
```

`--max-groups` 是每个AI阶段的组数上限；每组三篇，网络保持串行。`--collect-only <ISSN...>` 仅限制本轮发现请求，已有候选仍全局处理。15,000是示例参数，不是自动启用的预算。阈值在请求间检查，一次请求可能超过剩余额度；出现未知计费，本轮后续付费阶段停止，补材料与合格内容处理可独立继续。

live顺序：离线恢复完整响应→逐刊发现（单源失败记录后继续）→类型预筛→旧规则候选重筛准备→标题筛选→补摘要→摘要筛选→仅对缺少当前核验的最终候选做核验→排除抽样→准入报告。旧待审结果若缺少当前规则哈希会留档并重新筛摘要；不会为其伪造历史调用记录。材料变化后的自动批准须重新核验，明确人工排除始终保护。现有合格人工批准可直接满足准入；这条兼容路径不会把agent判断冒标为人工。

自动候选须同时满足启用来源、日常范围、可核日期、正式摘要、有效主题及DOI、当前规则的摘要筛选调用记录、逐字引文和对当前概况的支持性核验。当前反馈延期／争议、未解决调用、过期证据与不支持的概况阻止该篇进入自动期次，不阻塞其他合格文章。unsupported等问题暂保留，不自动反复生成概况；后续补录或用户反馈可继续处理。

实际自动准入及本地发布需要**同时**满足：`config/runtime.json` 的 `publication_mode` 明确设为 `automatic`，调用带 `--publish`。当前仍为 `manual`；本轮未修改开关、启用定时器或发表真实文章。

```powershell
# 仅在确认启用automatic模式后使用；不带--live时不联网，只处理已有合格结果
python scripts/workflow.py run-cycle --publish
# 独立调度器最终可调用这一条；本仓库未安装或启动该定时任务
python scripts/workflow.py run-cycle --live --publish --max-tokens 15000 --max-groups 1
```

四天窗口完整结束后的下一天才自动组期（11月11日至14日一期，最早15日）。同一期重复运行不追加改写；迟到合格文章进入下一可用期次，空期不发布。自动快照附部分覆盖说明。`approval_actor=agent` 与human分开；agent结果不会写成human_reviewed。导出失败可在随后运行恢复，即使跨到下一期且没有新增合格文章，也会补出之前已确认的快照。

当前入口到“可部署的本地完整release”为止，不含远端上传、DNS／证书配置或Windows／Linux定时器安装；不会依赖桌面Codex会话。实际常开处理节点、预算和远端发布凭证核实后再部署调度。109项离线测试已通过；本轮真实库仅执行影子检查，识别7篇现有人工批准、0次新模型调用，未改变候选状态或发布任何期次。

## 出版社接口与尚未解决的部分

发现和摘要补全分别验证。Crossref存在滞后；官方文章未收录时仍可先建立出版社候选。Nature目录已有实现；部分AAAS、PNAS、ACS、Wiley与ScienceDirect入口受限，稳定服务器覆盖尚未验收。

Scopus/ScienceDirect/Springer接口必须获得实际key与相关权限后测试。不要把高校浏览器登录当作服务器凭证。单篇验证工具默认不联网、不存摘要、不调用AI：

```powershell
python scripts/publisher_probe.py --provider scopus --doi 10.1038/s41893-025-01681-y
# 确认权限后才加 --live；具体步骤见API访问指南
```

ES&T已修复迁移后的官方RSS地址，保存的本地实测两条feed分别解析20和91条题录；这仍不代表完整时间窗覆盖。摘要补全已统一为Nature正式摘要区→OpenAlex→Europe PMC（非Nature跳过首步），仅处理已通过标题初筛的启用来源；每个请求串行、无即时重试。

暂未索引、无摘要字段或临时请求失败最多尝试3轮，间隔1天、再3天；遵守更长的Retry-After。来源拒绝或限流会持久暂停该提供方，身份冲突/解析失败转人工核对；暂停不等于排除文章。`workflow.py status` 按日常、历史、日期待核和停用来源列出队列，几千条历史题录不会自动进入日常AI。

2026-11-12单篇实测：唯一日常待补记录在OpenAlex和Europe PMC均匹配DOI但没有摘要字段，保留待补并安排有限重查；没有调用AI。Elsevier key已确认只提取原Markdown中的密钥，未把全文当key。此前接口实测仍为Scopus授权拒绝、ScienceDirect认证拒绝，校园网下忽略HTTP代理对照相同；Springer尚未取得key。现有可用公共路径优先保留，接口只补剩余缺口，暂不付费Premium。完整申请条件、南京大学联系入口和数据处理范围见文档，不承诺零遗漏。

确认访问条件或材料已修复后，可显式恢复指定队列；此命令仅重置本地暂停，不联网、不调用AI。只恢复文章不会绕过仍生效的提供方暂停：

```powershell
python scripts/workflow.py resume-enrichment --ids <文章编号>
# 已核实提供方恢复可用时，才同时清除对应提供方暂停
python scripts/workflow.py resume-enrichment --ids <文章编号> --providers openalex
```

## 文档与下一步

- [选刊与主题规则](docs/journal-selection.md)：来源名单、明确排除方向和人工校准反馈。
- [架构复查与实施顺序](docs/architecture.md)：最小架构、已发现缺陷、月度方案与停止扩张的条件。
- [API申请与验证](docs/publisher-api-access.md)：如何检查已有key、如何申请、权限与实测命令。
- [来源访问诊断](docs/source-access.md)：历史证据和逐出版社限制。
- [AI环境影响来源探索](docs/ai-environment-sources.md)：FAccT及Nature相关子刊的探索，未自动接入。
- [阅读页设计](docs/design.md)：字号、Paper浅色、课题组海军蓝深色及图标侧栏。
- [部署说明](deploy/README.md)：准备好的配置与尚未完成的生产验收。

月度方案建议先试Applied Energy和Building and Environment：仍发现题录、月底更严格筛选、沿用同库同归档。用户已同意月度机制；首批两刊沿用实施建议，周期配置与月度生成器尚未落地。14天滚动窗口不再使等待处理的文章自动变成历史；历史边界按固定首次监测起点划分。

下一阶段按源码核查后的顺序推进：先保护人工决定并补齐导出／计费不确定状态恢复，再完善小批量排除抽检和概况证据核验；同步观察来源覆盖，随后落实两刊月度筛选。具体缺口、交付物和验收条件见 [下一阶段规划](docs/architecture.md)。第一阶段已实现人工反馈保护、不可变release、调用离线恢复与备份恢复验证；月度周期未实现，独立本地自动流程已提供但真实自动发布尚未启用。稳定版的用户参与集中在最终结果反馈，初筛和日常抽检将由系统承担。

## 数据、Git与云端审查

`outputs/library/library.sqlite3` 是唯一编辑状态；`outputs/` 内的抓取响应、摘要、模型证据、截图、备份都属于本地材料，全部忽略。`outputs/public` 现在是版本容器；仅 `export` 返回的 `public_directory`（`releases/<版本>`）可用于预览／上传。容器内 `latest.json` 只在完整构建和本地链接校验成功后切换，失败版本与旧版本均保留，不自动删除。旧平铺导出不会被覆盖。未批准文章不会因导出而公开。

```powershell
python -m unittest discover -s tests -v
```

自动检查只用临时库、虚构响应与回环HTTP，无外网采集、付费AI或真实发布。云端agent不需要密钥或真实数据库即可审查。源码工作流见 [AGENTS.md](AGENTS.md)。GitHub代码共享与文献站点部署是分开的操作。

`web/fonts/` 提供霞鹜文楷子集与OFL许可；`web/tabler-icons-LICENSE.txt` 对应侧栏图标的MIT许可。字体仅在开发时重新生成才需要fontTools、brotli和原字体；运行服务器不需要这些依赖。项目源码本身尚未选定开源许可证，公开可读不代表另行授予所有用途的许可。

本地目录已统一为LitBrief；既有 `LITERATUREHOT_*` 环境变量和部署服务标识保持兼容。可用 `python scripts/workflow.py backup` 生成SQLite一致性备份。源部署打包脚本排除运行数据与密钥；旧打包产物不会自动更新。
