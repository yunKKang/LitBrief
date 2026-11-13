# LitBrief 运行、发布与恢复

公开站点：<https://litbrief.sustainable-systems.world/>。首期7篇人工通过的文献保持原快照与链接。2026-11-12起，现有Ubuntu服务器同时承担私有处理和公开静态托管；二者使用不同目录和权限，课题组主站不变。

## 当前状态

| 部分 | 状态 |
| --- | --- |
| 活动数据库 | `/opt/litbrief/outputs/library/library.sqlite3`，仅服务账户可读写 |
| Windows旧库 | 保留副本；`outputs/library/REPLICA`阻止本地程序再编辑 |
| 私有代码／配置／密钥 | `/opt/litbrief`，独立litbrief账户，无sudo权限 |
| 工作台 | litbrief-review.service，仅监听127.0.0.1:8766 |
| 每日发现 | litbrief-discover.timer，每天07:00 UTC+8，只读RSS，不调用AI、不发布 |
| 周刊／月刊 | 服务和timer已安装，尚未启用；软预算已设为各200万tokens、发布模式manual |
| 公开网站 | `/var/www/litbrief/current` → 独立release，只有静态文件 |

处理服务最多512MiB内存、半个CPU核心；工作台最多256MiB、四分之一CPU核心。真实采集期间观测约66MiB内存。2核2GB服务器足以继续低并发试运行，无需另开1核1GB节点。约10MB迁移包含校验约11秒；这不是长期带宽或可用性保证，后续仍观察真实周期。

## 工作台访问

在Windows终端执行并保持终端打开：

```powershell
powershell -File C:\CODE\LitBrief\deploy\open-workbench.ps1
```

随后访问 <http://127.0.0.1:8766/>。SSH隧道关闭后工作台不可从Windows访问；服务器流程继续运行。没有开放8766公网端口，也不把后台挂到公开子域名。

## 运行与调度

每日任务仅RSS发现，减少滚动遗漏；完整目录和Crossref补充仍由明确的流程执行。周刊窗口周日至周六，计划周日09:00处理上一完整周；月刊计划次月首个周日18:00处理前月，仅Applied Energy、Building and Environment，且等正在启动的周刊服务结束。所有时间显式Asia/Shanghai（UTC+8），不随服务器系统时区漂移。

`scheduled_run.py`要求automatic和正数预算，否则在联网前退出。新迁移验证副本若存在STAGING也会拒绝付费定时运行。当前没有启用付费timer；确认回测和预算后才操作以下入口：

```bash
sudo systemctl enable --now litbrief-weekly.timer litbrief-monthly.timer
```

执行为备份→串行处理→证据准入→不可变期次→完整导出→同机原子切换→HTTPS内容核对。不会依赖Codex桌面、浏览器登录或用户每期点击批准。token阈值按请求间停止，最后一笔可能超过剩余额度；不承诺硬金额封顶。不确定计费停止后续付费阶段，不自动重试。

查看服务和下次触发时间：

```bash
systemctl list-timers 'litbrief-*'
sudo journalctl -u litbrief-discover.service -n 50 --no-pager
sudo -u litbrief sh -c 'cd /opt/litbrief && python3 scripts/workflow.py status'
```

阶段失败写入runs并返回退出码2；覆盖情况单独记录在collect的sources字段。HTTP 200不代表有效正文或完整目录。当前存在Nature挑战页、ACS受限和个别身份冲突，不能用Crossref成功掩盖这些缺口。

## 静态发布与回滚

源码模板在 `deploy/nginx.conf.example`；实际独立配置 `/etc/nginx/conf.d/litbrief.conf`、独立证书、日志、ACME目录均保留。主站配置和证书不需修改。网站内容由litbrief账户持有；源数据库和.env不位于公开路径。

人工维护或开发后，可在有导出数据的处理节点运行 `workflow.py export`，再调用接收器。Windows原数据库已经停用，不应用旧副本导出覆盖线上。

现有外部发布工具仍可传送已验证的公开release，远端通过SSH管理账户降权为litbrief执行：

```powershell
python scripts/deploy_static.py --host ubuntu@43.134.100.87 --identity <私钥路径> --activate
python scripts/deploy_static.py --host ubuntu@43.134.100.87 --identity <私钥路径> --rollback <32位版本编号> --activate
```

发布前后校验公开文件白名单和SHA-256，支持周刊／月刊链接。build.json只作运输元数据，落盘在公开根目录之外的manifests，不作为网页资源。记录commit、源文件树／规则／配置／期次快照哈希、构建时间和篇数。没有Git目录时从私有SOURCE.json取得提交标识，不能把未提交源码冒称为该提交的完整重建。

`current`原子切换，不重启Nginx。旧版保留，不自动删往期。证书由certbot.timer续期，独立LitBrief续期hook检查后reload；此前模拟续期通过。

## 数据恢复与故障处理

已核验6张受保护表在迁移前后相同，5份原始AI响应可定位；保留cutover和日志模式切换前备份。新响应路径相对项目保存，历史运行日志里的旧绝对路径只是历史记录。SQLite使用DELETE回滚日志；已有标准库备份／恢复与内容一致性测试，未迁移数据库产品。

恢复前停掉LitBrief处理／审核服务，保留当前库，再通过SQLite备份副本恢复到独立位置。运行integrity_check、核对审核与期次及响应文件、执行离线影子检查后，才能切回活动位置。不要把旧Windows副本直接覆盖服务器当前库：切换后服务器已有新的采集记录。

`worker.lock`是操作系统锁文件，文件存在不代表进程正在运行。不要删除；进程异常退出后自动释放。身份冲突以runs记录隔离；涉及人工反馈或既有AI证据的记录不自动合并，未解决前不进入筛选或准入。

清理仍须预览并明确确认，未启用自动删除。实际任务失败日志以journal和runs为准；尚未配置对外邮件或消息通知。

## 下一步的必要关口

10月补采与五个周窗口、月度生成代码已就绪，并通过隔离测试；2026-11-13用户已放宽预算，10月真实AI回测已按200万tokens监控线启动。先完成可取得材料的回测，检验保留结果、来源覆盖与月刊质量，再开启付费定时器。当前人工参与集中于最终结果反馈，持续初筛由流程承担。

一次性回测入口（会调用真实模型，不发布）：

```bash
sudo -u litbrief sh -c 'cd /opt/litbrief && python3 scripts/backtest.py --month 2026-10 --max-tokens 2000000'
```

当前后台任务由独立临时单元`litbrief-backtest-202610.service`承载。运行时不要再启动第二份；可用`journalctl`查看进度，检查点位于`outputs/backtests/2026-10/progress.json`。临时单元停止后可能被systemd卸载，不能假定始终可以`systemctl start`重启；维护时重新用相同用户、工作目录与资源限制的`systemd-run`创建，或使用上面的前台入口。续跑读取原检查点，保留累计用量并重新检查各窗口；已完成的付费结果不重复请求。

`evaluated_available_material`只表示当下可处理材料已走完，不能证明无遗漏或摘要全部取得；`evaluated_with_blocked_work`表示仍有未推进工作。日期待核、摘要缺失、概况核验未通过分别保留，不通过伪造上线日或放松核验凑出月刊。两刊日期问题的实测见[来源诊断](../docs/source-access.md#2026-11-13-月度来源日期缺口)。

新增未知用量默认停止。用户明确接受某次未知费用、同意隔离该请求并继续其余文章后，可在回测命令添加`--isolate-unknown-request <请求ID>`；检查点保存具体授权ID，账本仍显示计费未知，受影响文章保持隔离，不自动重试或伪记0费用。再出现新的未知调用仍停止。2026-11-13已对一次涉及2篇摘要的失败取得此项授权；不构成所有未来失败的通用豁免。

新失败日志保存安全的HTTP状态码或transport/invalid_json分类，不记录供应商错误正文、请求头或凭证。早期只记录RuntimeError的失败无法事后还原HTTP状态，不据此猜测限流或账号额度不足。
