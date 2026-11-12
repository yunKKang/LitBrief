# 出版社 API：检查、申请与验证

核对日期：2026-11-12。范围为当前27刊，只需题录与正式摘要。Elsevier key已配置并完成小样本请求，当前仍被API认证/授权拒绝；Springer key尚未取得。以下是官方机制与可执行步骤，不保证个人注册就获得学校权益。

## 先检查已有账号

### Elsevier

1. 打开 [API key管理入口](https://dev.elsevier.com/apikey/manage)，用你已有的Elsevier账号登录；通常是你用于Scopus／ScienceDirect的个人账号。该入口由[官方首页](https://dev.elsevier.com/)的“I want an API Key”链接进入。登录页面需本人操作。
2. 看是否已有应用与key。没有则按门户申请；若显示需要资格或授权核实，使用[官方支持入口](https://dev.elsevier.com/support.html)转交机构/API团队。不要把浏览器订阅状态当作API已开通。
3. 即使有key，也需核对 **Scopus Abstract Retrieval / META_ABS** 和 **ScienceDirect Article Retrieval / META_ABS** 两个产品权限。只看到Scopus Search权限不代表能取摘要。
4. 在图书馆认可的机构网络和目标服务器各做一次相同DOI测试；先测已知有摘要的文章，避免把“新文尚未索引”误作权限失败。校外服务器如无机构IP，需要询问 **Institutional Token**，它由支持团队提供，与API key配套。

[官方认证说明](https://dev.elsevier.com/tecdoc_api_authentication.html)明确key标识应用，新增key默认没有产品数据访问权；权限依机构合同、客户/合作伙伴或获批集成权益启用。[官方FAQ](https://dev.elsevier.com/support.html)说明校外可询问Institutional Token，代理访问API不受支持。邮箱地址、个人学校身份或校内IP本身都不能证明scope已获批。

申请时准备：姓名和机构身份、课题负责人/课题组、应用名称LitBrief、27刊清单、仅元数据和摘要、低并发与缓存、实际服务器地区/IP、公开站点用途、第三方模型推理与数据保存方式。若表单要求项目URL可填写本项目GitHub，网站域名仍在规划中应如实注明未上线；不得虚填已有部署或授权。不预设免费、额度或审批时长。

### Springer Nature

1. 打开 [Developer Portal](https://dev.springernature.com/)，登录已有开发者账号；如无账号，从Register注册并按页面要求验证邮箱。
2. 在账号的订阅/应用/API key页面查看是否已有 **Meta API** 凭证。门户登录后布局本轮未验证，具体按钮以页面为准。
3. 没有时，从 [Meta API产品页](https://datasolutions.springernature.com/products/meta-data/)的“Access Free API”选择Basic计划；Premium另行询价，当前不需要先申请全文或高配额服务。
4. 以真实Nature系列DOI测试，记录是否有正式摘要。Basic免费提供有限请求的标准书目元数据；产品支持摘要字段，不等于免费计划或13刊每篇都会返回摘要，实际字段、配额与条款需在账号和返回中确认。

官方公开页面没有给出“必须教授身份/必须学校管理员才能注册Basic”的条件，不应凭空增加。仍需账号、邮箱验证、接受相应条款和应用资料；需要订阅内容或扩大使用范围时由供应商核实。公共或机构可访问摘要也不自动等于允许复制原摘要到公开网站。

## 南京大学联系入口

[图书馆当前联系我们页](https://lib.nju.edu.cn/gytsg/lxwm.htm)列出的咨询邮箱为 **tsgzxb@nju.edu.cn**，电话 **025-89683242**。可请其转电子资源/API授权相关负责人。历史[Scopus条目](https://lib.nju.edu.cn/info/1013/1603.htm)标为已购买，但发布时间为2019年；需要确认当前合同，而非用旧条目证明2026年API权益。

以下文本可由用户修改后发送；本项目未代发邮件或提交申请：

> 我们是南京大学课题组，拟开发非商业科研文献简报LitBrief，限定27本高质量期刊，仅增量获取题名、DOI、期刊、首次在线日期和正式摘要，不批量获取全文。使用低并发、缓存和DOI去重。
>
> 请协助确认当前Scopus Abstract Retrieval API和ScienceDirect Article Retrieval API的META_ABS权限、额度，以及能否申请用于课题组校外Linux服务器的Institutional Token。若校外方式不适用，请协助确认获准校内运行环境和出版社认可IP。
>
> 数据拟用于标题与摘要筛选、中文概况生成，并在公开课题组网站展示题录、原文链接和简短分析，不公开原始摘要。计划调用第三方模型API，因此还需明确原始摘要的存储、校外托管、第三方推理和分析结果公开的允许范围及保留期限。我们会补充实际供应商、服务器和预计请求量资料；拟每四天发布一期，部分低频来源月度处理。
>
> 若学校已有相关API授权或WoS API Expanded，请告知申请负责人和具体流程。

只申请实际需要的能力，不把Scopus FULL视图写成全文权限。Elsevier当前[ScienceDirect TDM条款](https://dev.elsevier.com/tdm_service.html)明确第三方处理/托管需书面许可，所以这些用途应一次说明；该条款针对其适用数据与合同，不能据此泛化所有来源的法律条件。

## Key配置与本地命令

在项目本地 `.env` 追加空模板中的相应变量，不覆盖已有模型配置，不把key发到聊天、GitHub或前端：

```dotenv
LITERATUREHOT_ELSEVIER_API_KEY=
LITERATUREHOT_ELSEVIER_INSTTOKEN=
LITERATUREHOT_SPRINGER_API_KEY=
```

`publisher_probe.py` 默认只检查配置；只有 `--live` 才对指定DOI发一次请求。没有key不会发请求；不重试、不调用AI、不保存原始摘要或请求头、不输出完整带key URL，拒绝重定向到其他端点。

```powershell
# 先从本地后台确认一篇真实且已有正式摘要的文献编号
python scripts/publisher_probe.py --provider scopus --paper-id 1826
# scope核实后，显式单篇测试
python scripts/publisher_probe.py --provider scopus --paper-id 1826 --live
python scripts/publisher_probe.py --provider sciencedirect --paper-id 3332 --live
python scripts/publisher_probe.py --provider springer --paper-id 2193 --live
# 也可用 --doi 代替 --paper-id，适用于干净源码或目标服务器
```

这些编号属于当前本地库，干净GitHub克隆没有真实数据库，需提供实际DOI。不要以一篇成功推算全刊覆盖；ScienceDirect不能拿Nature论文当作成功样本。工具只报告DOI是否匹配、摘要字符数与哈希，不把API数据送给第三方模型。

| 结果 | 下一步 |
|---|---|
| credential_missing | 先在本地配置该接口的key；当前已观测到此状态 |
| ready_for_explicit_probe | 只是凭证存在，尚未验证权限 |
| authentication_failed | 核对key有效性、请求身份；401不自动证明学校未订阅 |
| not_entitled | 服务明确返回权限不足；申请scope，不盲目重试 |
| forbidden_unknown / challenge_page | 保存分类并核查具体访问机制，不把403一概当订阅不足 |
| rate_limited | 按服务要求延后，检查额度；不提高并发 |
| record_not_found | 此次没有该DOI记录，不证明原文无摘要；换已知收录样本或稍后补录 |
| abstract_missing | DOI匹配但没返回摘要，检查视图和来源字段覆盖 |
| content_mismatch / parse_failed | 不入库、不送AI，检查身份或解析器 |
| abstract_available | 此DOI正式摘要字段可读；接着测其他刊/日期和目标服务器 |

## 已执行的核对

- 官方Elsevier认证、摘要/文章接口、校外访问和当前TDM条款已打开核对；Springer产品页的Basic计划与支持字段已核对。开发者门户部分文档需前端渲染，未假称看到了登录后的完整表单。
- 初始缺key检查后，用户提供Elsevier key，已配置在忽略的.env并进行6次串行诊断请求。系统HTTP代理路径与显式直连结果一致：Scopus 401/AUTHORIZATION_ERROR；ScienceDirect 403/AUTHENTICATION_ERROR。未取得摘要，不能单据此判为付费问题或key无效；Springer仍待申请。
- 后续本地比对确认配置值是原Markdown内唯一的32位密钥候选，未将文档全文作为key，未打印或提交凭证。没有新的权限证据，因此没有反复重测失败接口。
- Europe PMC以core串行查询当前剩余3个DOI，均为HTTP200、record_not_found；结果在本地outputs/access-diagnostics，不含原文摘要。
- 解析和安全检查用虚构响应验证；不代表Scopus／ScienceDirect／Springer已经真实联通。

## 哪些期刊受益，是否值得申请或付费

以当前27刊名单为准，三个产品要分开：

| 产品 | 本项目对应范围 | 当前投入判断 |
|---|---|---|
| ScienceDirect Article Retrieval | One Earth、Environmental Science & Ecotechnology、Renewable and Sustainable Energy Reviews、Global Environmental Change、Energy Economics、The Lancet Planetary Health、Resources, Conservation and Recycling、Applied Energy、Building and Environment（9刊） | 仅针对已证实的公开摘要获取缺口；先核实已有key权限，不购买全文套餐。 |
| Scopus Abstract Retrieval | 跨出版社：可能覆盖上述9刊、13本Nature刊，以及Science、Science Advances、PNAS、Earth's Future、ES&T中已被Scopus收录且带摘要的文章 | 潜在补缺范围最广，但尚未逐刊验证尤其新刊和最新文。已有公共路径不迁走，校外Token不再作为前提。 |
| Springer Nature Meta API | Nature、Nature Communications、Nature Sustainability、Nature Energy、Nature Climate Change、Nature Cities、Nature Water、Nature Geoscience、Nature Reviews Earth & Environment、Nature Reviews Clean Technology、Communications Earth & Environment、Nature Food、Nature Health（13刊） | 已有公开目录与摘要路径，当前增量收益较低；免费Basic可申请验证，暂不付费Premium。 |

Springer [官方产品页](https://datasolutions.springernature.com/products/meta-data/)明确有免费Basic和需询价的Premium；免费计划提供有限请求的标准元数据，摘要字段与13刊覆盖仍需实测。注册或申请API不等于必须付费，作者发表OA的APC也不是读者或API使用费。

Elsevier [Scopus产品页](https://dev.elsevier.com/sc_apis.html)说明非商业学术用途可在政策与额度条件下免费使用；实际产品scope、机构权益和用途仍需核实。不要把接口授权申请自动理解为个人购买数据库。公开网站与第三方AI用途应如实向馆员说明，优先争取现有权益。

OA文章通常可直接阅读全文，很多非OA文章也公开标题和摘要；公共内容不保证自动请求一定通过网站防护。现有路径能读就继续用，API的价值是减少实际缺口和维护成本，并非所有期刊的必需许可证。

当前优先级：公共路径→确实缺失的题录/摘要→已有免费/机构权益接口→仍不可行时人工少量补充或降频/停用。付费前必须明确新增补齐的合格论文数量、授权用途和维护成本，不因期刊品牌或“可能覆盖”先付钱。

校园网对照命令：`python scripts/publisher_probe.py --provider scopus --paper-id 1826 --live --direct`。`--direct`仅忽略本次urllib HTTP代理，不修改系统代理/路由或TUN，因此不能独立证明最终请求出口被出版社识别为机构IP。没有新授权或网络证据时不反复重试当前失败。
