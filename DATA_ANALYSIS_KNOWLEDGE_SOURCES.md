# CureFlow 数据分析知识层：来源与使用边界

更新日期：2026-09-19  
状态：首批可用知识索引（方法摘要与元数据，不保存论文全文）

## 产品中的用法

CureFlow 不把论文全文拼进提示词，也不让大模型凭论文常识替代用户数据。当前采用三层结构：

1. **本地工具层**计算缺失、重复、分布、异常线索、分组差异、相关与时间变化，并生成证据引用。
2. **方法知识层**按字段名和分析目标检索方法卡，写明适用场景、最低前提、必查项目和禁止结论。
3. **生成式解读层**只解释本地汇总；输出必须通过 JSON 结构和证据引用白名单，并标记需人工复核。

方法文献回答“应该怎样分析”；上传文件的证据回答“这个文件里发生了什么”。两者不可互换。

## 通用统计、数据质量与因果推断

| 来源 | 可用于 CureFlow 的能力 | 主要边界 |
|---|---|---|
| [NIST/SEMATECH e-Handbook](https://www.itl.nist.gov/div898/handbook/toolaids/pff/E-Handbook.pdf) | 探索性分析、分布与异常线索 | 异常标记不自动等于错误 |
| [OECD Good Statistical Practice](https://legalinstruments.oecd.org/public/doc/331/body-text.en.html) | 相关性、准确性、可信度、及时性、可比性、可解释性 | 不能用单一完整率替代整体质量 |
| [Rubin: Inference and Missing Data](https://academic.oup.com/biomet/article-abstract/63/3/581/270932) | 缺失机制与可忽略条件 | 未验证时不能默认随机缺失 |
| [Planning studies with missing data](https://academic.oup.com/ije/article/52/4/1268/7034967) | 多变量缺失规划与敏感性分析 | MAR/MNAR 假设需明确说明 |
| [Conformal Prediction](https://arxiv.org/abs/2107.07511) | 预测区间、覆盖率、校准集意识 | 未校准不能承诺覆盖率 |
| [World Bank: Impact Evaluation in Practice](https://www.worldbank.org/en/programs/sief-trust-fund/publication/impact-evaluation-in-practice) | RCT、IV、RDD、DiD、匹配的识别思路 | 相关、前后差或分组差不自动构成因果 |

## 学习与教育数据

| 来源 | 可用于 CureFlow 的能力 | 主要边界 |
|---|---|---|
| [State of Educational Data Mining](https://jedm.educationaldatamining.org/index.php/JEDM/article/view/8) | 教育数据挖掘任务分类、学习表现与行为分析 | 预测任务与解释任务必须分开 |
| [Handbook of Learning Analytics](https://www.solaresearch.org/publications/hla-22/) | 学习测量、预测、网络与干预框架 | 身份值不发送给云端模型 |
| [When Probabilities Are Not Enough](https://jedm.educationaldatamining.org/index.php/JEDM/article/view/593) | 可解释、反事实和理论驱动的因果分析 | 风险分数不等于干预有效性 |
| [Explainable early performance prediction](https://jedm.educationaldatamining.org/index.php/JEDM/article/view/770) | 早期预警和可解释预测 | 需检查时间泄漏、误报漏报与群体差异 |

知识卡覆盖：成绩与排名核验、学习表现画像、学业预警、教育干预效果、公平性审计。

## 工业、设备与预测性维护

| 来源 | 可用于 CureFlow 的能力 | 主要边界 |
|---|---|---|
| [NIST: Prognostics and Health Management](https://nvlpubs.nist.gov/nistpubs/ams/NIST.AMS.100-13.pdf) | 监测、诊断、预测、数据融合、误报漏报 | 传感器异常不自动等于设备故障 |
| [Predictive Maintenance Review](https://arxiv.org/abs/2010.03207) | 异常检测、根因线索、故障诊断、剩余寿命 | 不同任务的指标不能混用 |
| [Sensor anomaly detection review](https://arxiv.org/abs/2010.14946) | 传感器异常与过程上下文 | 单一阈值通常不能跨工况复用 |
| [Predictive Maintenance Systems and Purposes](https://arxiv.org/abs/1912.07383) | 维护任务、数据与部署目的对齐 | 随机行切分可能夸大跨设备泛化 |

知识卡覆盖：传感器完整性、工况分层异常、预测性维护/剩余寿命、根因结论准入。

## 商业、运营与实验

| 来源 | 可用于 CureFlow 的能力 | 主要边界 |
|---|---|---|
| [Microsoft: Trustworthy A/B Tests](https://www.microsoft.com/en-us/research/publication/trustworthy-analysis-of-online-a-b-tests-pitfalls-challenges-and-solutions/) | A/B 测试陷阱与可信度检查 | 显著不等于业务重要，SRM 未通过不能发布效果 |
| [Three Key Checklists for OCE](https://www.microsoft.com/en-us/research/publication/three-key-checklists-and-remedies-for-trustworthy-analysis-of-online-controlled-experiments-at-scale/) | 实验设计、执行与分析清单 | 检查曝光、随机化、护栏指标和多重比较 |
| [Microsoft: CUPED](https://www.microsoft.com/en-us/research/group/experimentation-platform-exp/articles/deep-dive-into-variance-reduction/) | 使用实验前协变量降低随机实验方差 | 不能使用受处理影响的事后变量 |
| [Forecasting: Principles and Practice](https://otexts.com/fpp3/) | 时间序列任务、基线、回测、区间、异常与缺失 | 时间序列不能随机拆分；异常值不盲目替换 |

知识卡覆盖：经营 KPI 分解、实验可信度、CUPED、时间序列预测、队列与漏斗。

## 经济与政策分析

| 来源 | 可用于 CureFlow 的能力 | 主要边界 |
|---|---|---|
| [NBER: Modern Difference-in-Differences](https://www.nber.org/papers/w29873) | 异质处理、非二元处理与动态效应 | 传统双向固定效应并非所有错位实施场景都可靠 |
| [Synthetic Controls with Staggered Adoption](https://academic.oup.com/jrsssb/article/84/2/351/7056152) | 少数处理单位、供体池与处理前拟合 | 需要安慰剂、溢出和供体污染检查 |
| [NBER: Event Studies with Continuous Treatment](https://www.nber.org/papers/w32118) | 连续处理强度的事件研究 | 需明确处理强度和识别假设 |
| [IMF: Nowcasting Growth with ML and Satellite Data](https://www.elibrary.imf.org/view/journals/001/2026/020/001.2026.issue-020-en.xml) | 高频代理、卫星数据与实时预测 | 按真实发布日期回测，避免未来信息泄漏 |

知识卡覆盖：经济趋势、双重差分/事件研究、合成控制、影响评估、实时预测。

## 大模型表格分析与幻觉风险

| 来源 | 对 CureFlow 的直接启示 |
|---|---|
| [FLARE spreadsheet benchmark](https://arxiv.org/abs/2506.17330) | 复杂多步表格任务会产生貌似合理但错误的结果，统计计算必须交给工具。 |
| [QRData: statistical and causal reasoning](https://openreview.net/pdf?id=DCJmsgsNTR) | 数据统计与因果推理不能只靠语言能力，需要执行环境、任务分层和可复核答案。 |
| [Provenance-based Fact Checking for RAG](https://aclanthology.org/2024.emnlp-industry.97/) | 每条生成结论应回到具体证据，并能定位失败来源。 |
| [Citation Faithfulness in RAG](https://aclanthology.org/2024.inlg-main.35/) | 有引用不等于引用支持结论，仍要检查蕴含与范围扩张。 |
| [Retrieval Augmentation Reduces Hallucination](https://aclanthology.org/2021.findings-emnlp.320.pdf) | 检索能降低部分幻觉，但不能替代输出校验、工具计算和人工复核。 |

CureFlow 因此采用：原始行不发云端、聚合结果带稳定引用、引用白名单、数字/范围审计、因果语言门禁，以及只报告本次运行的观测风险而不伪造总体幻觉率。

## 当前范围与后续扩展

当前知识库包含 **55 张方法卡、39 条论文或权威方法来源**，覆盖通用、教育、工业、商业和经济五个领域。入口不再把“领域”和“分析任务”混成一组，而按六类真实问题选择：数据质量、描述画像、群体比较、关系探索、趋势预测和因果评估；领域再由字段证据路由。每张入选卡都包含适用场景、做法、最低条件、必须核对项、禁止结论、当前可用状态和来源。

每次运行仍只检索少量相关方法，避免提示词膨胀和领域污染。下一步适合增加医疗与临床（单独高风险边界）、空间数据、文本/问卷量表，并把更多方法卡连接到可执行的本地统计工具。
