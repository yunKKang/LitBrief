# AI、算力基础设施与数字化环境影响：来源可行性探索

核对日期：2026-11-12。状态：研究与采集入口小样本验证；新增来源尚未启用，也未导入正式候选库或调用付费 AI。正式启用期刊仍为27本。

## 建议

优先验证 **Nature Computational Science 的 AI 环境影响专题 + ACM FAccT 主会正式论文**。会议先选一个，不为凑满两个扩展范围。MLSys 留作第二候选；Nature Machine Intelligence 暂作主题线索来源。该结论基于已找到的具体文献与最新目录，尚不能估计长期命中率。

研究对象是 AI／算力／数字化扩张带来的能源、水、碳、材料、电子废弃物、基础设施投资和退役、供应链责任与分配影响。AI 用于天气预测、污染识别或材料开发本身不足以纳入；单一编译、调度、冷却、芯片和模型提速方案，即使提到节能也排除。

## 直接证据

| 来源 | 已核实的主题证据 | 选源判断 |
|---|---|---|
| Nature Computational Science | [生成式 AI 的电子废弃物挑战](https://www.nature.com/articles/s43588-024-00712-6)，2024年研究论文，分析AI计算基础设施扩张的电子废弃物与管理策略。另有[计算科学可持续原则](https://www.nature.com/articles/s43588-023-00461-y)，2023年 Perspective。 | 期刊补充优先级最高，但只做专题候选；不把所有计算方法和 AI for Science 文章接入。 |
| ACM FAccT | [2026年官方目录](https://facctconference.org/2026/acceptedpapers.html)及其[公开CSV](https://facctconference.org/static/docs/facct2026-final.csv)含AI全生命周期环境足迹综述、数据扩张的环境与社会成本研究。后者由[作者机构](https://researchprofiles.ku.dk/en/publications/how-hyper-datafication-impacts-the-sustainability-costs-in-fronti-2/)确认正式发表于主会论文集（[DOI](https://doi.org/10.1145/3805689.3812393)）。2024年另有[AI部署能耗研究](https://doi.org/10.1145/3630106.3658542)。 | 首选会议试运行候选，关注环境负担与治理；不扩大到所有算法公平、伦理或社会议题。 |
| MLSys | [2022年正式论文](https://proceedings.mlsys.org/paper_files/paper/2022/hash/462211f67c7d858f663355eff93b745e-Abstract.html)分析AI运营与硬件制造的碳足迹。实读[2026年目录](https://proceedings.mlsys.org/paper_files/paper/2026)，列135篇，标题中与功耗有关的条目多为硬件、调度和推理优化。 | 可作为第二候选，但不建议当前整会接入。一次标题扫描不足以断言没有符合主题的论文。 |
| Nature Machine Intelligence | [2025年关于LLM可持续性评估的文章](https://www.nature.com/articles/s42256-025-00979-y)确实相关，但官方明确标为 Comment；[2026年强可持续性讨论](https://www.nature.com/articles/s42256-026-01240-w)位于 News & Comment 栏目。 | 有主题线索，不据这些评论证明研究论文稳定产出；现阶段不整刊加入，不把 Comment 混成原始研究。 |
| IEEE HPCA | [Chasing Carbon](https://ieeexplore.ieee.org/abstract/document/9407142)，2021年正式会议论文，是计算系统环境足迹的具体先例。 | 回答了IEEE架构会议能否产出相关研究：能。但大量论文仍是芯片与架构技术，只适合定向发现，不能由一篇先例推断高命中率。 |
| ACM e-Energy | 找到2026年AI数据中心需求响应研究的[作者公开论文](https://www.bu.edu/peaclab/files/2026/03/FlexDC_Sim_ACM_E_Energy26.pdf)；[官网](https://energy.acm.org/conferences/eenergy/2025/)本轮访问403。 | 方向可能贴近电网和算力负荷，但需要区别工程控制优化与系统影响，正式长文/短文身份也要逐条确认；暂不列首选。 |
| AGU／EGU | [AGU投稿说明](https://www.agu.org/annual-meeting-2025/present/abstracts)说明会议摘要由组织者审核并安排报告；[EGUsphere说明](https://www.egusphere.net/about/about_egusphere.html)区分摘要、报告材料、预印本与后续期刊审稿。 | 可用于发现早期线索，通常不等同于计算机主会完整审稿论文；暂不接入正式精选。 |

会议等级不能由IEEE／ACM组织名推断。本轮没有完成各候选在用户2024目录和当前CCF目录中的逐项等级核对，不把FAccT、MLSys与HPCA笼统标为同一“顶会”等级。正式新增来源前需完成该质量门槛核对；新期刊的分区标签也尚未补核。CCF现行[目录说明](https://www.ccf.org.cn/Academic_Evaluation/By_category/2026-03-31/870181.shtml)明确区分主会Full/Regular paper与Short、Demo、Workshop等形式。

现有白名单仍是研究主线。例如 Nature Sustainability 的[美国AI服务器环境影响与净零路径](https://www.nature.com/articles/s41893-025-01681-y)，以及 Communications Earth & Environment 的[AI快速部署与近期碳预算压力](https://www.nature.com/articles/s43247-026-03746-y)直接涉及系统扩张后果。新会议来源补充特定缺口，不替代现有高质量来源。

## 实际采集验证

- 直接用普通Python HTTP请求取得FAccT 2026官方页面；页面通过公开CSV加载目录。CSV含325条，列为Paper ID、Title、Authors、Abstract，摘要非RSS导读，无需Codex浏览器或高校会话。
- 以环境、碳、水、能源、可持续、数据中心词组进行宽松候选发现，共20条。这个数字包含误命中，只是检索候选，**不是精选数量或有效召回率**。人工读摘要确认其中AI生命周期综述和数据扩张成本研究具有明确的相关性。
- CSV未提供DOI、正式在线日期和长短文轨道字段；需用官方论文集或作者机构记录补齐，不能单靠出现在名单中就发布。2026年的两个示例中，数据扩张成本论文已由作者机构确认正式论文集版本；其他条目仍需逐篇身份核验。
- MLSys 2026公开目录和2022年的文章摘要页能直接取得，服务器采集入口可行。标题中energy/power/environment等词并不保证符合本项目边界。
- Nature部分文章页在搜索工具中遇到cookie授权跳转；本轮使用出版社搜索索引核实元数据及可读的正式摘要，不宣称目标服务器的最新目录和摘要覆盖已验收。出版社访问问题仍遵循既定暂停安排。
- 本地快照位于 `outputs/source-exploration-20261112/`，与正式数据库、公开导出隔离。此验证在本机完成，不等于已在课题组服务器部署。

## 若进入下一轮试验

1. 先对上述两个首选来源做历史样本校准，人工判定阅读价值与类型边界；保留拒绝理由。不要把2024／2026年早已发表的示例标成本期新增。
2. 会议增加独立来源类型与年份、轨道、发表状态、官方ID/DOI；不伪造ISSN塞入现有期刊表。主会正式论文与Workshop、预印本、会议摘要分开，首轮只接正式研究长文和有实质内容的综述。
3. 每四天检查官方目录变化，有新候选才继续处理。会议集中放量时分批串行筛选，不改变四天组期节奏，不形成篇数截断；保留漏抓与缺材料状态。
4. 标题与摘要先做专题候选发现，再用既有模型判断主要研究问题；不批量重抓全部技术论文的全文。仍保持AI串行、high+default、前几期人工审核。
5. 验收以相关性、独特文献增益、正式摘要/身份可得性和审核消耗为准；本轮只证明存在相关论文与可用入口，尚未验证长期覆盖率和稳定收益。
