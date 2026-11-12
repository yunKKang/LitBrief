# LitBrief · 文献简报：部署准备

当前采用用户确认的保守方案：**本地／校内采集、AI筛选和人工审核；课题组服务器只托管公开静态文件。** 服务器不需要出版社key、学校登录、文献数据库或模型API。没有连接生产服务器、修改DNS或安装服务。

课题组主域名为 `sustainable-systems.world`。示例配置暂用 `literature.sustainable-systems.world`；子域名最终拼写、服务器地址、SSH与DNS权限仍待现场核实，示例不表示已经上线。

## 本地／校内处理

源码目录为 `C:/CODE/LitBrief`，数据库、原始摘要、模型记录和备份均在被Git忽略的 `outputs/`。审核服务只监听 `127.0.0.1:8766`，不通过公网反向代理暴露。

日常顺序见项目README：发现题录 → 类型预筛 → 标题AI初筛 → 按需补正式摘要 → 摘要AI → 人工审核 → 四天组期并确认发布 → 静态导出。AI配置为 `gpt-6-luna / high / default`，fast关闭，实际串行1，上限5；出版社请求串行。不能同时启动多个任务消费队列。

```powershell
python scripts/workflow.py status
python scripts/workflow.py backup
# 完成人工审核、组期与发布确认后才有可导出的真实期次
$release = python scripts/workflow.py export | ConvertFrom-Json
python -m http.server 8767 --bind 127.0.0.1 --directory $release.public_directory
```

每次付费筛选显式设定token停止阈值。阈值在请求之间检查，单次请求可能超过剩余额度；没有单价时只报告tokens，不虚构金额。AI失败不自动重试。摘要补全的延时重试与提供方暂停由SQLite持久记录；确认恢复后通过 `resume-enrichment` 显式解除，命令见README。

公开导出只包含已发布快照、短概况、题录链接、样式、字体及其许可。没有批准的文章不会进入公开页。上传前核对预览及文件清单，不能上传整个 `outputs/` 或项目根目录。

## 静态站点上线顺序

1. 核实课题组服务器及现有Nginx站点，选定子域名，添加对应A/AAAA记录；只使用实际可用地址。
2. 在服务器准备 `/var/www/literaturehot/releases/<版本>/` 和ACME验证目录。沿用现有运维账户与最小写入权限，网站目录不得包含 `.env`、SQLite、日志或源码。
3. 核对 `nginx.conf.example` 的域名和路径。首次先提供HTTP的ACME验证入口，签发证书后再安装完整HTTPS配置；未有证书时不能直接启用示例中的TLS块。运行 `nginx -t` 成功后reload，保留原课题组站点配置。
4. 将 `export` 返回的 `public_directory`（`outputs/public/releases/<版本>/`）内容完整复制到服务器新的release目录；不要复制容器根目录或失败版本，包含字体与许可。核对文件完整、实际文章内容和往期链接后，以原子替换符号链接的方式将 `/var/www/literaturehot/current` 指向该release，保留上一版本用于回滚。
5. 验证HTTPS、主题切换、文章/往期链接和手机阅读；核对私有路径无法访问。服务器不安装审核后台、采集timer或付费AI任务。
6. 每四天完成本地审核后重复导出和release切换。公开更新频率与发现频率分开；应每天检查滚动RSS以减少遗漏，发现流程尚需连续两个四天周期验收，不能承诺零遗漏。

上线前仍需真实审核一期、核实域名/服务器/DNS与部署权限。本仓库提供配置和步骤，未执行生产操作。

## 采集与历史保留

来源状态见 [来源访问诊断](../docs/source-access.md)。Nature公开目录和摘要路径保持；ES&T新RSS已单次验证；ScienceDirect等来源仍存在部分覆盖缺口。浏览器工具只作本机人工辅助，服务器运行不依赖Codex或桌面会话。受限API尚未取得权限前不装入定时采集链。

滚动发现窗口为14天，文章处理归属使用固定首次监测起点，不因等待超过14天自动退出队列。月度机制已同意，分刊配置与月度组期仍待实现，不能用定时器替代月度逻辑。

`status` 按日常、历史、日期待核和停用来源报告数量，历史数千条不等于待付费处理量。磁盘达到10 GiB时提示；清理预览与确认执行器已实现，但未启用周期自动删除，已发布期次和去重记录保留。定期使用SQLite一致性备份，之后纳入用户现有备份体系。清理先预览并确认具体对象；活动数据库和公开归档保留，只轮转清单中的过期备份与旧构建副本。

## 预留的校内Linux服务模板

`literaturehot-collect.service`、`.timer` 和 `literaturehot-review.service` 仅为将来获准的校内常开Linux节点预留，**不在外部静态网站服务器安装**。本轮未启用这些模板，也未创建Windows定时任务。

启用前应现场核实采集权限、Python路径、独立用户、`/opt/literaturehot` 工作目录与 `outputs/` 写入权限，测试真实发现入口；审核服务仍只监听回环地址，通过SSH隧道访问。collect timer仅采集并准备草稿，不调用AI、不发布；四天窗口用固定锚点计算。日常主路径仍由同一进程锁保护，残留 `worker.lock` 必须先核实进程再处理。

当前内容确认仍走测试期人工流程；最终将由独立的agent调度完成筛选、核验、组期与发布，用户只需对最终结果提供可选反馈。独立run-cycle与本地自动发布逻辑已实现但未启用，定时器和远端部署尚未安装，不依赖桌面Codex会话。备份恢复已用合成数据库验证，真实生产节点和远端release切换仍需部署验收。

## 独立流程的部署边界

`python scripts/workflow.py run-cycle` 默认为离线影子检查，不联网、不调用模型、不审批或发布。`--live --max-tokens <阈值>` 才执行网络与付费阶段；启用自动模式且传入 `--publish` 后才做本地准入／组期／导出。完整命令及预算语义见README，只有最终生成的release可进入远端部署步骤。

现有collect.service/timer仍是原采集模板，不会自动改为付费闭环。将来在选定的本地／校内常开节点用单一定时任务调用run-cycle，不能再同时启用独立collect／screen任务；现有共享进程锁会拒绝重叠运行。当前未为本机或服务器创建定时任务，也未修改publication_mode。远端站点继续只托管静态release，自动上传和切换需在真实服务器上单独实现与验收。

数据保留清单只作用于本地outputs中的明确对象，不能把本地计划用于远端服务器。新公开release包含往期归档；本地可预览当前＋前2版以外的旧构建文件。远端旧release轮转仍待部署时实现，不因本地清理命令自动删除服务器目录。入口与保护例外见README的数据保留章节。
