# LitBrief · 文献简报

面向能源、环境、产业转型、生产网络、供应链和住房研究的定期文献简报。以高质量来源和主题相关性筛选，提供中文短概况、原文链接和按期归档，不按固定篇数凑数。

本项目当前是**本地试运行版本**。已实现采集、候选库、AI标题与摘要筛选、人工审核、四天组期及静态导出；真实文献仍待审核。生产部署、全来源稳定覆盖、月度期次和自动发布尚未完成。公开代码不包含本地文献库、原始摘要、API密钥或机构会话。

## 当前边界

- 27本启用期刊：21本重点来源、6本相关列表来源。Joule、The Innovation、EES已退出常规跟踪，历史记录留档。
- 五个研究主题：产业与基础设施绿色转型；全球生产网络与环境责任；国际绿色产品标准；供应链管理与韧性；住房与建成环境。AI／算力扩张的环境影响纳入系统问题，不扩展到所有AI应用。
- 研究论文与实质综述优先。工程／环境工程技术、土壤过程、工艺与单项性能优化不因附带节能减排或LCA而入选。
- 类型预筛 → 标题AI初筛 → 补正式摘要 → 摘要AI判断和概况 → 人工审核 → 四天期次快照。缺摘要与不相关分别记录。
- AI默认使用供应商标识 `gpt-6-luna`、`high`、`service_tier=default`；实际网络串行，允许上限5并发但未启用。付费失败不自动重试或换模型。
- 当前本地快照：193条日常候选中11条待审、3条人工排除、156条AI排除、22条类型排除、1条待补摘要；另375条停用来源记录留档。这些是2026-11-12状态，不是克隆仓库自带的演示数据。

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
python scripts/workflow.py export
python -m http.server 8767 --bind 127.0.0.1 --directory outputs/public
```

标题初筛不生成研究结论；短评仅依据正式摘要，并保存逐字支持片段。模型声明相关不等于研究质量已验证。使用预算停止后，未处理候选保留队列，不形成公开篇数限制。

`workflow.py screen --ids ...` 与 `screen_paper.py` 是显式诊断入口；日常统一使用上面的标题→摘要批处理顺序。后台运行先结束再启动下一阶段，不并行消费队列。来源退出后运行 `python scripts/workflow.py sync-sources`，原始资料和AI结果保留，退出前队列记录在运行日志。

## 出版社接口与尚未解决的部分

发现和摘要补全分别验证。Crossref存在滞后；官方文章未收录时仍可先建立出版社候选。Nature目录已有实现；部分AAAS、PNAS、ACS、Wiley与ScienceDirect入口受限，稳定服务器覆盖尚未验收。

Scopus/ScienceDirect/Springer接口必须获得实际key与相关权限后测试。不要把高校浏览器登录当作服务器凭证。单篇验证工具默认不联网、不存摘要、不调用AI：

```powershell
python scripts/publisher_probe.py --provider scopus --doi 10.1038/s41893-025-01681-y
# 确认权限后才加 --live；具体步骤见API访问指南
```

本轮已核验官方接口机制，Europe PMC剩余3条DOI查询均未命中记录。Elsevier key已实测：Scopus授权拒绝、ScienceDirect认证拒绝，校园网下忽略HTTP代理对照仍相同；Springer尚未取得key。现有可用公共路径优先保留，接口只补剩余缺口，暂不付费Premium。完整申请条件、南京大学联系入口和数据处理范围见文档，不承诺零遗漏。

## 文档与下一步

- [选刊与主题规则](docs/journal-selection.md)：来源名单、明确排除方向和人工校准反馈。
- [架构复查与实施顺序](docs/architecture.md)：最小架构、已发现缺陷、月度方案与停止扩张的条件。
- [API申请与验证](docs/publisher-api-access.md)：如何检查已有key、如何申请、权限与实测命令。
- [来源访问诊断](docs/source-access.md)：历史证据和逐出版社限制。
- [AI环境影响来源探索](docs/ai-environment-sources.md)：FAccT及Nature相关子刊的探索，未自动接入。
- [阅读页设计](docs/design.md)：字号、Paper浅色、课题组海军蓝深色及图标侧栏。
- [部署说明](deploy/README.md)：准备好的配置与尚未完成的生产验收。

月度方案建议先试Applied Energy和Building and Environment：仍发现题录、月底更严格筛选、沿用同库同归档。用户已同意月度机制；首批两刊沿用实施建议，周期配置与月度生成器尚未落地。14天滚动窗口不再使等待处理的文章自动变成历史；历史边界按固定首次监测起点划分。

## 数据、Git与云端审查

`outputs/library/library.sqlite3` 是唯一编辑状态；`outputs/` 内的抓取响应、摘要、模型证据、截图、备份都属于本地材料，全部忽略。`outputs/public` 仅输出已发布快照、CSS、字体与许可。未批准文章不会因导出而公开。

```powershell
python -m unittest discover -s tests -v
```

自动检查只用临时库、虚构响应与回环HTTP，无外网采集、付费AI或真实发布。云端agent不需要密钥或真实数据库即可审查。源码工作流见 [AGENTS.md](AGENTS.md)。GitHub代码共享与文献站点部署是分开的操作。

`web/fonts/` 提供霞鹜文楷子集与OFL许可；`web/tabler-icons-LICENSE.txt` 对应侧栏图标的MIT许可。字体仅在开发时重新生成才需要fontTools、brotli和原字体；运行服务器不需要这些依赖。项目源码本身尚未选定开源许可证，公开可读不代表另行授予所有用途的许可。

本地目录已统一为LitBrief；既有 `LITERATUREHOT_*` 环境变量和部署服务标识保持兼容。可用 `python scripts/workflow.py backup` 生成SQLite一致性备份。源部署打包脚本排除运行数据与密钥；旧打包产物不会自动更新。
