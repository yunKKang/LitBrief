# LitBrief 部署与日常发布

公开地址：<https://litbrief.sustainable-systems.world/>。2026-11-12 已完成首次部署，首期包含用户人工通过的 7 篇；期次为 2026-11-11 至 2026-11-14，页面注明首期试运行、提前上线和部分来源覆盖。后续自动组期仍按完整四天窗口执行。

## 部署边界

沿用已确认的保守方案：本地／校内负责采集、串行 AI 筛选、核验和期次生成，课题组 Ubuntu 服务器只托管公开静态文件。公网没有工作台、数据库、原始摘要、机构会话或 API key。`publication_mode` 仍为 `manual`；未安装采集、付费 AI、自动发布或自动清理定时任务。网页上线不代表无人值守闭环已启用。

LitBrief 与主站共用现有 Nginx，独立管理以下资源，无需另开应用进程或容器：

| 内容 | 路径 |
| --- | --- |
| 网站根目录 | `/var/www/litbrief/current`，指向独立 release |
| 完整版本 | `/var/www/litbrief/releases/<32位版本编号>/` |
| 校验清单与发布记录 | `/var/www/litbrief/manifests/`、`deployments.jsonl`，均在公开根目录之外 |
| Nginx 配置 | `/etc/nginx/conf.d/litbrief.conf` |
| ACME 验证目录 | `/var/www/litbrief-acme/` |
| 独立证书 | `/etc/letsencrypt/live/litbrief.sustainable-systems.world/` |
| 日志 | `/var/log/nginx/litbrief.access.log`、`litbrief.error.log` |
| 接收与切换脚本 | `/usr/local/lib/litbrief/activate_release.py`，root 持有，发布账户执行时无需 sudo |

`nginx.conf.example` 是已部署配置。日常发布只切换 LitBrief 的 current 链接，不更改主站目录、配置或证书，不需要 reload Nginx。证书使用 webroot 验证，由已有 certbot.timer 续期；`/etc/letsencrypt/renewal-hooks/deploy/litbrief-reload` 仅在 LitBrief 证书续期后检查并 reload Nginx。首次证书有效期至 2027-02-10，后续以在线证书为准。

## 发布更新

先在本地生成已确认期次的完整导出。AI／处理流程见项目 README，此上传工具不会调用模型、审核文章或生成新期次。SSH 主机与私钥保存在本机配置中，不提交凭证。

```powershell
python scripts/workflow.py backup
python scripts/workflow.py export
# 先核对清单；默认不连接服务器。
python scripts/deploy_static.py --host <SSH别名或用户@主机>
# 明确上传并切换；必要时增加 --identity <本机私钥路径>。
python scripts/deploy_static.py --host <SSH别名或用户@主机> --activate
```

上传只接受 `outputs/public/latest.json` 指向的完整 release，核对导出清单、SHA-256、内部链接与公开文件白名单，拒绝链接／junction 和私有文件。SSH 必须通过已知主机验证；不会自动接受未知主机指纹。压缩包通过 SSH 标准输入传送，无远端上传临时文件。

服务器使用排他锁，先验证整包、再写新版本、复核落盘校验值，最后原子切换 current。相同版本和内容可安全重试；有损坏或未完成版本时会拒绝覆盖，重新导出为新版本后重试。失败时先检查命令输出和远端 current，避免把连接中断误判为未发布。上传完成后仍需检查公开 HTTPS、首页、期次与文章详情。

## 回滚与留存

发布输出包含 `release` 和 `previous`。回滚指定已有版本，同样会重新核对其清单：

```powershell
python scripts/deploy_static.py --host <SSH别名或用户@主机> --rollback <32位版本编号> --activate
```

历史 release 暂不自动删除；当前首版展开约几 MB，保留成本很低。每个完整版本都包含所有已发布往期。后续轮转只针对旧构建副本，不删除往期内容，且不使用本地清理清单直接处理服务器。本地保留政策与明确确认的清理入口见项目 README。

若需撤下整个 LitBrief 虚拟主机，只移除该站点配置，先 `nginx -t` 再 reload；主站配置不需要改动。证书、内容和发布记录可保留，避免意外丢失恢复材料。

## 首次上线验收

- 公网 DNS 指向目标服务器，独立 HTTPS 签发成功，证书模拟续期成功。
- 首期 7 篇、归档、详情、CSS 和自托管字体可访问；公开路径没有工作台。Tabbit实测通过桌面搜索／导航／详情、明暗主题保持、手机尺寸触控收放导航；未发现横向溢出或页面控制台错误。
- `.env`、`.git/config`、候选队列、运行页、保留管理、SQLite 和运输清单均返回 404。
- 服务器临时目录中验证了原子切换、损坏包不影响当前版、回滚和相同版本重试；未用测试内容替换生产站点。
- 主站 135 个文件、current 目标、原 Nginx 配置和原证书／续期配置与部署前基线一致。
- 详细本地验收证据位于被 Git 忽略的 `outputs/deployment/`。

## 下一步与预留模板

下一阶段是本地／校内常开处理节点的无人值守调度和受控上传联调，以及两个四天周期的来源覆盖观察。电脑休眠或关机时，本地流程不会运行；公开服务器仍正常提供最后一次发布的内容。自动发布需要显式启用配置并验证预算／恢复边界，不能只因完成域名部署就打开。

`literaturehot-collect.service`、`.timer` 和 `literaturehot-review.service` 是旧校内 Linux 模板，未在公网服务器安装。不能把旧 collect timer 与完整 run-cycle 并行启用。工作台继续只监听回环地址；受限出版社接口、月度周期和疑似误排回流的缺口见架构文档。本次部署没有解决或掩盖这些问题。
