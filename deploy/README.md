# LitBrief · 文献简报：部署准备

站点显示名称为 LitBrief；下列目录、服务名和环境变量沿用 `literaturehot`，与现有配置保持一致。

用户确认沿用课题组网站服务器和主域名。现有项目文档中的主域名为 `sustainable-systems.world`，本项目拟用 `literature.sustainable-systems.world`。服务器地址、DNS 与证书需部署前现场核验。本轮只生成配置，未连接生产服务器或安装服务。

## 目录与访问边界

- `/opt/literaturehot`：程序、配置和本地 `.env`；由独立 `literaturehot` 用户运行，密钥文件权限 0600。
- `/opt/literaturehot/outputs`：SQLite、采集证据、用量与审核数据；不允许 Nginx 访问。
- `/var/www/literaturehot/releases/<版本>/`：复制 `workflow.py export` 生成的完整公开目录，包含 HTML/CSS 和 `fonts/` 字体及许可；不复制项目其他目录。
- `/var/www/literaturehot/current`：指向核验后的发布版本，切换符号链接；保留上个版本用于回滚。
- 审核服务只监听 `127.0.0.1:8766`，通过 SSH 隧道访问，不配置公网反向代理。单人工作台，无公网账户体系。

## 上线顺序

1. 核实服务器当前地址与现有 Nginx 站点；为拟用子域名新增 A/AAAA 记录，只添加实际可用地址。
2. 安装 Python 3.10+、创建独立运行用户和上述目录。项目只使用标准库，无需 Node、Redis 或外部数据库。程序不需要 root。
3. 上传源码与配置；单独安全配置 `.env`，不要从聊天或公开仓库传密钥。首轮可迁移 SQLite 备份及必要采集证据；先确认旧本地服务已停止写入，或者使用内置 backup 命令生成一致性备份。
4. 通过 ACME Webroot 为子域名签发证书；把 `nginx.conf.example` 的域名与路径核对后作为独立虚拟主机安装。执行 `nginx -t` 成功后再 reload，保留原课题组站点。
5. 审核后台用 SSH 本地转发：`ssh -L 8766:127.0.0.1:8766 <已核实服务器>`，浏览器打开 `http://127.0.0.1:8766`。安装 review service 后检查其仍只监听回环地址。
6. 人工核对试刊，导出到独立目录，检查其中没有摘要原文、数据库、日志、密钥。复制到新 releases 目录，核对内容后原子切换 current；验证 HTTPS、文章和往期链接。
7. 验证每天采集后再启用 collect timer。timer 不调用付费 AI、不自动发布；四天期次由固定锚点计算，避免使用 cron 的 `*/4` 导致每月重新计数。

## 当前运行策略

最新配置：`gpt-6-luna / high / default`，fast 已按用户要求关闭。AI 上限5、实际串行1；出版社目录串行，不能将多个定时任务同时启动来绕过进程锁。日常AI流程优先使用 `prefilter.py` → `batch_screen.py --titles-only` → `workflow.py enrich` → `batch_screen.py`，每批显式设token暂停阈值，未审核不公开。

来源问题已有逐刊诊断：见 `docs/source-access.md`。独立HTTP在本机的结果和带高校认证的本机Tabbit结果必须分开记录。目标服务器需重新实测；可复制源码包运行 `python3 scripts/diagnose_sources.py --environment-label 'production VPS, no institution session'`。该命令串行、无密钥，仅探测已配置期刊首页，不自动修改访问策略。`browser_collect.py` 与 `browser_abstract.py` 是Windows本机辅助工具，不加入服务器timer。

每日采集：出版社 RSS 与 Nature 全目录先入库，Crossref 随后补充；滚动回看 14 天，每次使用新缓存目录，跨年按年分段。其他出版社完整目录、服务器反爬与摘要获取仍有待补足，后台明确显示缺口，不能宣称零遗漏。运行超过一天后应检查 systemd 失败状态及来源记录，不能只看 timer 为 active。

AI 批处理必须显式给出 `--max-calls` 与 `--max-tokens`，额度用完保留待处理，不按预算删除候选。token 停止阈值在请求间检查，单个请求可能超过剩余额度。未设置供应商单价，不虚构金额预算。失败或进程中断不会自动再次付费；确认后通过后台重新排队。`worker.lock` 留存时先核实进程，再人工移除已失效锁。

前几期人工审核与发布。稳定后才讨论自动模式及误入率门槛；当前配置仅支持 manual，不能只改配置值就启用无人审核发布。

运行 `python3 scripts/workflow.py backup` 生成 SQLite 一致性备份；安排服务器既有备份体系异机保存。历史期次与条目长期保留，不自动删文献。`status` 报告 outputs 的磁盘占用，达到 10 GiB 提示；清理原始缓存须另行确认，不能删除数据库和已发布期次。
