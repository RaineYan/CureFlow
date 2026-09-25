"""Curated, citable method knowledge for evidence-grounded tabular analysis.

This module deliberately stores method summaries and bibliographic metadata,
not article text.  Method cards guide *how* a verified local result may be
interpreted; they are never evidence that a fact is true of an uploaded file.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


DOMAIN_LABELS = {
    "general": "通用统计与数据质量",
    "education": "学习与教育",
    "industrial": "工业与设备",
    "business": "商业与运营",
    "economics": "经济与政策",
}

SOURCES: dict[str, dict[str, Any]] = {
    "nist_eda": {
        "title": "NIST/SEMATECH e-Handbook of Statistical Methods: Exploratory Data Analysis",
        "organization": "NIST",
        "year": 2012,
        "url": "https://www.itl.nist.gov/div898/handbook/toolaids/pff/E-Handbook.pdf",
    },
    "nist_boxplot": {
        "title": "NIST/SEMATECH e-Handbook: Box Plot and Outlier Fences",
        "organization": "NIST",
        "year": 2012,
        "url": "https://itl.nist.gov/div898/handbook/eda/section3/boxplot.htm",
    },
    "oecd_quality": {
        "title": "OECD Recommendation on Good Statistical Practice",
        "organization": "OECD",
        "year": 2015,
        "url": "https://legalinstruments.oecd.org/public/doc/331/body-text.en.html",
    },
    "rubin_missing": {
        "title": "Inference and Missing Data",
        "organization": "Biometrika",
        "year": 1976,
        "url": "https://academic.oup.com/biomet/article-abstract/63/3/581/270932",
    },
    "missing_planning": {
        "title": "Assumptions and analysis planning in studies with missing data",
        "organization": "International Journal of Epidemiology",
        "year": 2023,
        "url": "https://academic.oup.com/ije/article/52/4/1268/7034967",
    },
    "conformal": {
        "title": "A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification",
        "organization": "Angelopoulos and Bates",
        "year": 2023,
        "url": "https://arxiv.org/abs/2107.07511",
    },
    "world_bank_ie": {
        "title": "Impact Evaluation in Practice",
        "organization": "World Bank",
        "year": 2016,
        "url": "https://www.worldbank.org/en/programs/sief-trust-fund/publication/impact-evaluation-in-practice",
    },
    "fpp3": {
        "title": "Forecasting: Principles and Practice (3rd ed.)",
        "organization": "OTexts",
        "year": 2021,
        "url": "https://otexts.com/fpp3/",
    },
    "edm_state": {
        "title": "The State of Educational Data Mining in 2009: A Review and Future Visions",
        "organization": "Journal of Educational Data Mining",
        "year": 2009,
        "url": "https://jedm.educationaldatamining.org/index.php/JEDM/article/view/8",
    },
    "learning_analytics": {
        "title": "Handbook of Learning Analytics (2nd ed.)",
        "organization": "Society for Learning Analytics Research",
        "year": 2022,
        "url": "https://www.solaresearch.org/publications/hla-22/",
    },
    "edu_counterfactual": {
        "title": "When Probabilities Are Not Enough: The Need for Causal and Counterfactual Reasoning in Education",
        "organization": "Journal of Educational Data Mining",
        "year": 2023,
        "url": "https://jedm.educationaldatamining.org/index.php/JEDM/article/view/593",
    },
    "nist_manufacturing": {
        "title": "Measurement Science Roadmap for Prognostics and Health Management for Smart Manufacturing Systems",
        "organization": "NIST",
        "year": 2015,
        "url": "https://nvlpubs.nist.gov/nistpubs/ams/NIST.AMS.100-13.pdf",
    },
    "pdm_survey": {
        "title": "Machine Learning and Deep Learning for Predictive Maintenance: A Review",
        "organization": "Carvalho et al.",
        "year": 2020,
        "url": "https://arxiv.org/abs/2010.03207",
    },
    "sensor_anomaly": {
        "title": "Anomaly Detection for Industrial Control Systems Using Process Mining",
        "organization": "Survey literature index",
        "year": 2020,
        "url": "https://arxiv.org/abs/2010.14946",
    },
    "ab_trust": {
        "title": "Trustworthy Online Controlled Experiments: Five Puzzling Outcomes Explained",
        "organization": "Microsoft Research",
        "year": 2012,
        "url": "https://www.microsoft.com/en-us/research/publication/trustworthy-analysis-of-online-a-b-tests-pitfalls-challenges-and-solutions/",
    },
    "ab_checklists": {
        "title": "Three Key Checklists and Remedies for Trustworthy Analysis of Online Controlled Experiments at Scale",
        "organization": "Microsoft Research",
        "year": 2019,
        "url": "https://www.microsoft.com/en-us/research/publication/three-key-checklists-and-remedies-for-trustworthy-analysis-of-online-controlled-experiments-at-scale/",
    },
    "cuped": {
        "title": "Deep Dive into Variance Reduction (CUPED)",
        "organization": "Microsoft Experimentation Platform",
        "year": 2020,
        "url": "https://www.microsoft.com/en-us/research/group/experimentation-platform-exp/articles/deep-dive-into-variance-reduction/",
    },
    "did": {
        "title": "Difference-in-Differences Estimators of Intertemporal Treatment Effects",
        "organization": "NBER",
        "year": 2022,
        "url": "https://www.nber.org/papers/w29873",
    },
    "synthetic_control": {
        "title": "Synthetic Controls with Staggered Adoption",
        "organization": "Journal of the Royal Statistical Society Series B",
        "year": 2022,
        "url": "https://academic.oup.com/jrsssb/article/84/2/351/7056152",
    },
    "event_study": {
        "title": "Event Studies with a Continuous Treatment",
        "organization": "NBER",
        "year": 2024,
        "url": "https://www.nber.org/papers/w32118",
    },
    "imf_nowcast": {
        "title": "Nowcasting Economic Growth with Machine Learning and Satellite Data",
        "organization": "International Monetary Fund",
        "year": 2026,
        "url": "https://www.elibrary.imf.org/view/journals/001/2026/020/001.2026.issue-020-en.xml",
    },
    "flare": {
        "title": "FLARE: A Benchmark for Spreadsheet Reasoning and Manipulation",
        "organization": "arXiv",
        "year": 2025,
        "url": "https://arxiv.org/abs/2506.17330",
    },
    "provenance_factcheck": {
        "title": "Provenance-based Fact Checking for Retrieval-Augmented Generation",
        "organization": "EMNLP Industry Track",
        "year": 2024,
        "url": "https://aclanthology.org/2024.emnlp-industry.97/",
    },
}

# Second-wave references broaden task coverage beyond the initial domain
# skeleton.  Only metadata and our short method summaries are stored.
SOURCES.update({
    "asa_pvalues": {"title": "ASA Statement on Statistical Significance and P-Values", "organization": "American Statistical Association", "year": 2016, "url": "https://doi.org/10.1080/00031305.2016.1154108"},
    "benjamini_hochberg": {"title": "Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing", "organization": "Journal of the Royal Statistical Society Series B", "year": 1995, "url": "https://doi.org/10.1111/j.2517-6161.1995.tb02031.x"},
    "bootstrap": {"title": "Bootstrap Methods: Another Look at the Jackknife", "organization": "Annals of Statistics", "year": 1979, "url": "https://doi.org/10.1214/aos/1176344552"},
    "shap": {"title": "A Unified Approach to Interpreting Model Predictions", "organization": "NeurIPS", "year": 2017, "url": "https://arxiv.org/abs/1705.07874"},
    "change_point": {"title": "Selective Review of Offline Change Point Detection Methods", "organization": "Signal Processing", "year": 2020, "url": "https://arxiv.org/abs/1801.00718"},
    "concept_drift": {"title": "Learning under Concept Drift: A Review", "organization": "IEEE Transactions on Knowledge and Data Engineering", "year": 2019, "url": "https://arxiv.org/abs/2004.05785"},
    "oecd_education": {"title": "OECD Handbook for Internationally Comparative Education Statistics", "organization": "OECD", "year": 2004, "url": "https://www.oecd.org/en/publications/oecd-handbook-for-internationally-comparative-education-statistics_9789264104112-en.html"},
    "edu_explainable": {"title": "Explainable Early Prediction of Academic Performance", "organization": "Journal of Educational Data Mining", "year": 2024, "url": "https://jedm.educationaldatamining.org/index.php/JEDM/article/view/770"},
    "nist_engineering": {"title": "NIST/SEMATECH Engineering Statistics Handbook", "organization": "NIST", "year": 2025, "url": "https://www.nist.gov/programs-projects/nistsematech-engineering-statistics-handbook"},
    "nist_doe": {"title": "What is Experimental Design?", "organization": "NIST/SEMATECH", "year": 2025, "url": "https://itl.nist.gov/div898/handbook/pri/section1/pri11.htm"},
    "nist_measurement": {"title": "Measurement Process Characterization", "organization": "NIST/SEMATECH", "year": 2025, "url": "https://itl.nist.gov/div898/handbook/mpc/mpc_d.htm"},
    "microsoft_metric_pitfalls": {"title": "Twelve Common Metric Interpretation Pitfalls in Online Controlled Experiments", "organization": "Microsoft Research", "year": 2017, "url": "https://www.microsoft.com/en-us/research/wp-content/uploads/2020/08/2017-08-KDDMetricInterpretationPitfalls.pdf"},
    "microsoft_experiment_monitoring": {"title": "Patterns of Trustworthy Experimentation: During-Experiment Stage", "organization": "Microsoft Research", "year": 2020, "url": "https://www.microsoft.com/en-us/research/?p=720145"},
    "microsoft_post_experiment": {"title": "Patterns of Trustworthy Experimentation: Post-Experiment Stage", "organization": "Microsoft Research", "year": 2021, "url": "https://www.microsoft.com/en-us/research/articles/patterns-of-trustworthy-experimentation-post-experiment-stage"},
    "rdd_guide": {"title": "Regression Discontinuity Designs: A Guide to Practice", "organization": "Journal of Econometrics", "year": 2008, "url": "https://www.nber.org/papers/w13039"},
    "causal_econometrics": {"title": "Empirical Strategies in Economics: Illuminating the Path from Cause to Effect", "organization": "NBER", "year": 2019, "url": "https://www.nber.org/papers/w26104"},
    "llm_inference_hallucination": {"title": "Sources of Hallucination by Large Language Models on Inference Tasks", "organization": "Findings of EMNLP", "year": 2023, "url": "https://aclanthology.org/2023.findings-emnlp.182/"},
})


def _card(
    card_id: str, domain: str, title: str, use_when: str, method: str,
    requirements: list[str], blocked_claims: list[str], checks: list[str],
    sources: list[str], *, min_rows: int = 1,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "domain": domain,
        "title": title,
        "useWhen": use_when,
        "method": method,
        "minimumRequirements": requirements,
        "blockedClaims": blocked_claims,
        "requiredChecks": checks,
        "sourceIds": sources,
        "minRows": min_rows,
    }


METHOD_CARDS = (
    _card("quality_dimensions", "general", "统计质量六维检查", "任何数据集进入分析前", "按相关性、准确性、可信度、及时性、可比性和可解释性分开评价。", ["明确数据口径与采集范围"], ["完整率高就等于数据真实", "单一质量分数替代逐维检查"], ["字段定义", "缺失与重复", "时间覆盖", "跨表口径"], ["oecd_quality"]),
    _card("missing_mechanism", "general", "缺失机制分层", "字段存在缺失值", "先区分缺失位置与模式，再讨论 MCAR、MAR、MNAR；未满足可忽略条件时不自动插补。", ["保留缺失位置", "至少一个可比较字段"], ["默认把缺失当作零", "未验证即宣称随机缺失"], ["分组缺失率", "时间缺失率", "缺失与已观测字段关系"], ["rubin_missing", "missing_planning"], min_rows=4),
    _card("eda_before_model", "general", "建模前探索性分析", "未知表格或新任务", "先看分布、尺度、结构、异常线索和数据生成过程，再选择模型。", ["字段类型可识别"], ["只凭均值判断整体", "先选模型再寻找解释"], ["分布", "尺度", "分组", "时间", "异常位置"], ["nist_eda"], min_rows=4),
    _card("outlier_review", "general", "异常值审慎处理", "连续数值列具有单一、可比较的分布基线时", "先判断样本量、离散度、偏斜、分组和时间结构；1.5×IQR 仅作探索提示，修正队列只纳入经适用性检查后越过 3×IQR 的极端值，仍须核对来源。", ["至少 30 个有效数值", "原始位置可定位", "分组和时间背景已检查"], ["IQR 标记等于错误", "跳过 IQR 就代表数据正常", "自动删除所有极端值"], ["录入错误", "单位变化", "真实稀有事件", "敏感性分析"], ["nist_eda", "nist_boxplot", "fpp3"], min_rows=30),
    _card("uncertainty_bounds", "general", "不确定性与覆盖率", "输出预测区间或风险阈值", "优先报告区间、覆盖假设和失效条件；具备校准集时可考虑保序预测。", ["独立校准数据或可辩护的交换性假设"], ["点预测等于确定事实", "未校准就承诺覆盖率"], ["校准集隔离", "覆盖率", "分布漂移"], ["conformal"], min_rows=20),
    _card("causal_gate", "general", "因果结论准入门槛", "用户询问影响、效果或原因", "先明确处理、结果、反事实和识别策略；描述性相关只能形成假设。", ["处理与结果定义", "可辩护识别策略"], ["相关性写成因果", "分组均值差直接写成效果"], ["混杂", "选择偏差", "平行趋势或随机化", "稳健性"], ["world_bank_ie"]),

    _card("learning_profile", "education", "学习表现画像", "成绩、排名、课程或学习行为数据", "组合分布、分组差异、纵向变化和数据完整性，不用单次成绩替代学习能力。", ["明确评价口径", "可识别学生或群体但不向云端发送身份值"], ["排名等于能力", "一次测评代表长期表现"], ["成绩尺度", "课程可比性", "群体样本量", "时间窗口"], ["edm_state", "learning_analytics"], min_rows=4),
    _card("student_early_warning", "education", "学业预警预测", "需要识别可能需要支持的学生或群体", "把预警作为资源分配线索，报告误报漏报与时效，不自动生成惩罚性决定。", ["有明确预测时间点", "结果标签晚于输入特征", "独立验证集"], ["风险分数等于必然失败", "用敏感属性直接做处罚"], ["时间泄漏", "类别不平衡", "分群误差", "可干预性"], ["learning_analytics", "edm_state"], min_rows=30),
    _card("education_intervention", "education", "教育干预效果评估", "比较教学方案、辅导或资源投入", "预测与干预效果分开；只有具备随机化或可信准实验设计时才估计效果。", ["清晰干预与对照", "干预前数据", "结果时间窗"], ["预测准确就证明干预有效", "干预后分组差等于因果效果"], ["基线平衡", "流失", "溢出效应", "异质性"], ["edu_counterfactual", "world_bank_ie"], min_rows=30),
    _card("education_fairness", "education", "教育分析公平性审计", "模型或规则影响不同学生群体", "分别报告覆盖、误差和缺失；小群体结论降级，并保留人工申诉与复核。", ["合法且必要的群体字段", "足够群体样本"], ["总体准确率代表每个群体", "小样本差异必然真实"], ["各群体样本量", "误报漏报", "缺失差异", "决策用途"], ["learning_analytics"], min_rows=30),

    _card("sensor_integrity", "industrial", "传感器与测量完整性", "设备、传感器、工艺参数数据", "先识别掉线、恒值、漂移、时间错位和单位变化，再做故障判断。", ["时间戳", "设备或测点标识", "单位信息"], ["极端读数必然是设备故障", "缺测自动视为正常"], ["采样间隔", "校准记录", "传感器漂移", "维护窗口"], ["nist_manufacturing"], min_rows=20),
    _card("industrial_anomaly", "industrial", "工业异常检测", "监测连续工况或批次", "用工况分层的基线识别偏离，阈值需同时评估误报和漏报。", ["足够正常工况样本", "工况或批次上下文"], ["异常分数等于故障类型", "单阈值适合所有工况"], ["误报率", "漏报率", "概念漂移", "事件级聚合"], ["sensor_anomaly", "nist_manufacturing"], min_rows=30),
    _card("predictive_maintenance", "industrial", "预测性维护与剩余寿命", "存在故障、维修或退化记录", "区分异常检测、故障诊断和剩余寿命预测，按设备和时间切分验证。", ["维护/故障标签", "时间序列", "设备历史"], ["异常检测准确率等于剩余寿命准确", "随机行切分代表跨设备泛化"], ["标签延迟", "删失", "设备间差异", "提前量"], ["pdm_survey", "nist_manufacturing"], min_rows=50),
    _card("root_cause_gate", "industrial", "根因分析准入", "需要解释故障原因", "相关测点只能缩小排查范围；根因需结合过程拓扑、时序先后、干预或工程验证。", ["过程拓扑或工艺知识", "事件时间顺序"], ["最高相关变量就是根因", "模型解释值就是物理机理"], ["共同原因", "反馈回路", "维护记录", "工程复核"], ["nist_manufacturing"], min_rows=20),

    _card("business_kpi", "business", "经营指标分解", "收入、成本、用户、订单或转化数据", "先统一口径，再按时间、渠道、产品和用户群分解总量、率与结构变化。", ["指标定义", "统计周期", "分母口径"], ["汇总增长代表所有分群增长", "比率变化自动归因于某措施"], ["分子分母", "结构变化", "重复用户", "同期可比"], ["oecd_quality", "fpp3"], min_rows=4),
    _card("online_experiment", "business", "在线实验可信度检查", "A/B 测试或功能实验", "先检查随机化、样本比例、曝光、指标方向和多重检验，再解释效果。", ["随机分组标识", "预先定义指标", "实验时间窗"], ["显著即重要", "未通过样本比例检查仍发布效果"], ["SRM", "新奇效应", "多重比较", "护栏指标"], ["ab_trust", "ab_checklists"], min_rows=30),
    _card("cuped_variance", "business", "CUPED 方差缩减", "随机实验且存在实验前协变量", "只用实验前且不受处理影响的协变量降低方差，并报告原始与调整结果。", ["真实随机实验", "实验前协变量", "稳定口径"], ["事后选择协变量保证显著", "把调整结果伪装成原始结果"], ["协变量时点", "方差缩减", "偏差", "可复现公式"], ["cuped"], min_rows=30),
    _card("business_forecast", "business", "经营时间序列预测", "销售、需求、库存或流量按时间记录", "明确预测目标与范围，建立朴素基线，按时间回测并报告区间。", ["连续时间索引", "足够历史", "预测窗口"], ["随机拆分验证时间序列", "单点预测作为承诺"], ["季节性", "节假日", "结构突变", "回测误差"], ["fpp3"], min_rows=20),
    _card("cohort_funnel", "business", "队列与漏斗诊断", "用户生命周期、转化或留存数据", "保持同一队列定义与观察窗，逐层报告分母和流失，不跨期混合成熟度不同的队列。", ["用户/事件标识", "步骤或状态", "事件时间"], ["不同观察窗留存率直接比较", "漏斗相关性等于流失原因"], ["去重规则", "观察窗", "步骤顺序", "渠道结构"], ["ab_checklists"], min_rows=20),

    _card("economic_trend", "economics", "经济时间序列分解", "GDP、价格、就业、贸易或地区指标", "区分趋势、季节性、周期、名义/实际和频率变化，比较时保留修订版本。", ["时间字段", "指标单位与价格口径"], ["名义增长等于实际增长", "季节波动等于趋势反转"], ["季调", "通胀调整", "频率", "数据修订"], ["fpp3", "oecd_quality"], min_rows=12),
    _card("difference_in_differences", "economics", "双重差分与事件研究", "政策在不同地区或群体分期实施", "明确处理时点与比较组，检验处理前动态；异质和错位实施使用适当估计量。", ["面板或重复横截面", "处理时点", "未处理比较组", "充分处理前时期"], ["处理前趋势图平就证明平行趋势", "传统双向固定效应总是无偏"], ["预趋势", "提前反应", "异质效应", "聚类标准误"], ["did", "event_study"], min_rows=30),
    _card("synthetic_control", "economics", "合成控制", "少数单位受到政策或冲击且有长期处理前数据", "用供体池加权拟合处理前轨迹，并用安慰剂和敏感性分析评估差距。", ["长处理前序列", "可辩护供体池", "明确干预时点"], ["拟合好就证明因果", "受污染供体仍可使用"], ["处理前拟合", "供体权重", "安慰剂", "溢出"], ["synthetic_control"], min_rows=30),
    _card("economic_impact", "economics", "政策与项目影响评估", "需要回答某政策或项目是否改变结果", "依据分配机制选择随机试验、工具变量、断点、匹配或双重差分，并公开识别假设。", ["明确反事实", "处理分配机制", "结果指标"], ["前后变化自动归因政策", "控制变量越多因果越可靠"], ["选择偏差", "溢出", "依从性", "外部效度"], ["world_bank_ie"], min_rows=30),
    _card("economic_nowcast", "economics", "经济实时预测", "官方统计发布滞后且有高频代理变量", "把高频、卫星或搜索代理与正式指标发布日期对齐，采用伪实时回测避免未来信息泄漏。", ["历史发布日", "高频代理", "目标修订版本"], ["同期拟合等于实时预测能力", "代理相关性等于经济机制"], ["发布日期泄漏", "修订偏差", "结构变化", "区间"], ["imf_nowcast"], min_rows=30),

    _card("llm_spreadsheet_guard", "general", "大模型表格推理防幻觉", "让生成式模型解释表格结果", "复杂计算交给可验证工具；模型只解释带引用的聚合结果，输出必须通过结构与证据白名单。", ["工具结果", "稳定证据引用", "输出校验"], ["模型自行心算复杂统计", "无引用结论进入正式报告"], ["数字一致性", "引用存在性", "范围扩张", "人工复核"], ["flare", "provenance_factcheck"]),
)

METHOD_CARDS += (
    _card("sampling_bias", "general", "抽样与代表性检查", "需要把样本结论推广到总体", "核对抽样框、覆盖范围、非响应和权重；无代表性证据时只描述样本。", ["明确目标总体", "可解释抽样或覆盖过程"], ["样本平均值自动代表总体", "大样本可以消除系统性选择偏差"], ["覆盖偏差", "非响应", "权重", "有效样本量"], ["oecd_quality"]),
    _card("multiple_testing", "general", "多重检验与发现率控制", "同时扫描大量字段、群体或指标", "记录检验总数并控制错误发现率；探索结果与确认结果分开。", ["预先定义检验族", "保留全部检验结果"], ["挑出最小 p 值作为确定发现", "重复尝试直到显著"], ["检验数量", "FDR", "探索/确认分离", "效应量"], ["benjamini_hochberg", "asa_pvalues"], min_rows=20),
    _card("effect_size", "general", "效应量与实际意义", "比较群体、阶段或模型", "同时报告差异大小、区间和业务阈值，不用显著性替代实际意义。", ["可比口径", "样本量与变异信息"], ["p<0.05 就代表效果大", "不显著就证明没有差异"], ["绝对差", "相对差", "区间", "最小重要差异"], ["asa_pvalues", "bootstrap"], min_rows=8),
    _card("bootstrap_uncertainty", "general", "Bootstrap 不确定性评估", "解析公式困难但样本可重抽", "按数据依赖结构重抽并报告区间；时间、群组数据不能按独立行盲目重抽。", ["可辩护重抽单位", "足够样本"], ["任意数据都可逐行独立 bootstrap", "bootstrap 区间自动消除偏差"], ["重抽单位", "重复次数", "偏差", "区间稳定性"], ["bootstrap"], min_rows=20),
    _card("validation_leakage", "general", "验证切分与信息泄漏", "建立预测、预警或评分模型", "按部署场景切分训练与验证；时间数据按时间，个体重复数据按个体或群组隔离。", ["明确预测时点", "独立验证数据"], ["随机行拆分总是可靠", "训练集成绩代表部署表现"], ["目标泄漏", "时间泄漏", "个体泄漏", "外部验证"], ["flare", "fpp3"], min_rows=30),
    _card("model_explanation", "general", "模型解释值边界", "使用特征重要度、SHAP 或局部解释", "解释模型在当前数据上的预测贡献，不把解释值写成现实世界因果机制。", ["已验证模型", "明确背景分布"], ["SHAP 值等于因果效应", "全局重要度适用于每个个体"], ["相关特征", "背景样本", "稳定性", "局部/全局差异"], ["shap"], min_rows=30),

    _card("education_longitudinal", "education", "纵向学习轨迹", "同一学生或群体有多期测量", "保持学生、课程、量尺和时间窗口可比，区分个体内变化与群体构成变化。", ["稳定标识", "至少两个时间点", "可比量尺"], ["两次分数差等于真实成长", "群体均值变化代表每个学生变化"], ["重复测量", "回归均值", "流失", "量尺等值"], ["learning_analytics", "oecd_education"], min_rows=20),
    _card("education_item_quality", "education", "试题与量表项目质量", "题目级得分或问卷条目", "检查难度、区分度、缺答和内部一致性；不同版本需先做量尺可比性。", ["题目级响应", "明确计分规则"], ["总分可靠就证明每道题有效", "内部一致性等于内容效度"], ["项目难度", "区分度", "缺答", "版本差异"], ["oecd_education"], min_rows=30),
    _card("learning_engagement_sequence", "education", "学习行为与参与序列", "日志包含访问、作业、互动或出勤时间", "按学习阶段构造行为序列，防止用结果发生后的行为预测结果。", ["事件时间", "学习者匿名标识", "结果时间点"], ["点击多就等于学得好", "结果后的行为可作为早期预警特征"], ["时间泄漏", "平台可见性", "参与口径", "缺失日志"], ["learning_analytics", "edu_explainable"], min_rows=30),
    _card("education_indicator_comparability", "education", "教育指标口径可比性", "跨学校、专业、地区或年份比较", "统一学生、教师、支出、课程和入学口径，再比较比率与趋势。", ["指标定义", "分母口径", "时间范围"], ["名称相同就可直接比较", "生均指标不受学生结构影响"], ["定义变化", "覆盖范围", "价格调整", "层级结构"], ["oecd_education"]),
    _card("education_support_targeting", "education", "支持资源定向评估", "将辅导、资助或课程资源分配给特定群体", "把需求识别、资源分配和效果评估分开，并审计不同群体的覆盖与结果。", ["资源规则", "需求指标", "后续结果"], ["高风险学生必然最受益", "模型排序可直接替代教师判断"], ["覆盖率", "可干预性", "群体公平", "反馈循环"], ["edu_counterfactual", "learning_analytics"], min_rows=30),

    _card("spc_control_charts", "industrial", "统计过程控制图", "连续监测稳定生产过程", "按数据类型和抽样结构选择控制图，先区分普通原因与特殊原因波动。", ["稳定采样方案", "时间顺序", "合理子组"], ["超规格等于失控", "控制限等于规格限"], ["控制限", "自相关", "规则触发", "过程变更点"], ["nist_engineering", "nist_measurement"], min_rows=20),
    _card("process_capability", "industrial", "过程能力分析", "需要判断稳定过程是否满足规格", "只有过程稳定且量测系统可信时计算能力；同时报告分布假设和长期/短期差异。", ["上下规格限", "稳定过程", "可靠量测"], ["Cp/Cpk 高就证明所有产品合格", "失控过程仍可解释能力指数"], ["稳定性", "分布", "规格来源", "量测误差"], ["nist_engineering"], min_rows=30),
    _card("measurement_system", "industrial", "量测系统与 Gage R&R", "多操作员、多设备或重复测量", "把零件差异、重复性和再现性分开，先确认测量误差再分析过程。", ["重复测量", "操作员/仪器标识", "参考件"], ["仪器分辨率高就等于测量可靠", "过程波动全部来自产品"], ["偏倚", "重复性", "再现性", "稳定性"], ["nist_measurement"], min_rows=20),
    _card("industrial_change_point", "industrial", "工况变化点检测", "时间序列可能发生均值、方差或机制突变", "先定义可检测变化，再结合维护、换线和环境记录验证变化点。", ["有序时间序列", "足够前后窗口"], ["算法变化点必然是故障时刻", "多个变化点可任意挑选"], ["惩罚参数", "最小区间", "季节性", "事件记录"], ["change_point"], min_rows=30),
    _card("reliability_survival", "industrial", "可靠性与寿命数据", "设备存在失效时间、运行时长或删失", "使用寿命/生存分析保留未失效设备信息，区分日历时间和运行时间。", ["起始时间", "失效定义", "删失标识"], ["未失效设备应删除", "平均寿命可替代完整寿命分布"], ["右删失", "竞争风险", "批次", "使用强度"], ["nist_engineering", "pdm_survey"], min_rows=20),
    _card("industrial_doe", "industrial", "工业试验设计 DOE", "可控制工艺因素并希望优化响应", "预先定义因素、水平、随机化、区组和响应；筛选后再做响应面与确认试验。", ["可控因素", "明确响应", "安全实验范围"], ["观察数据相关性等于 DOE 因果结果", "一次最优组合无需确认试验"], ["随机化", "交互作用", "残差", "确认运行"], ["nist_doe", "nist_engineering"], min_rows=8),
    _card("industrial_drift", "industrial", "模型与工况漂移", "部署后的数据分布或关系随时间改变", "分别监测输入漂移、标签漂移和性能漂移，设置再训练与人工介入条件。", ["基准窗口", "当前窗口", "延迟标签或代理指标"], ["输入分布变了就必然性能下降", "定期重训总能修复漂移"], ["漂移类型", "窗口", "阈值", "性能反馈"], ["concept_drift"], min_rows=50),

    _card("unit_economics", "business", "单位经济与贡献利润", "评估客户、订单、产品或渠道价值", "统一收入、变动成本、获客成本和生命周期口径，避免总量增长掩盖单位亏损。", ["收入和成本口径", "分析单位", "时间窗口"], ["收入增长等于盈利改善", "平均客户价值适用于所有渠道"], ["贡献利润", "CAC", "回收期", "分群结构"], ["oecd_quality"]),
    _card("churn_survival", "business", "流失与留存生存分析", "客户观察窗不同或尚未流失", "使用队列与生存方法处理删失，统一激活、活跃和流失定义。", ["客户匿名标识", "起始时间", "流失规则"], ["未流失客户应从分析删除", "不同成熟度队列直接比较"], ["删失", "观察窗", "回流", "竞争事件"], ["fpp3"], min_rows=30),
    _card("customer_segmentation", "business", "客户分群可用性", "希望识别行为或价值相近的客户", "标准化特征、验证稳定性和可行动性；分群标签是分析工具，不是天然人群。", ["可解释特征", "足够样本", "业务可行动性"], ["聚类数量等于真实客群数量", "群体标签可推断个体动机"], ["尺度", "稳定性", "时间漂移", "可行动性"], ["nist_eda"], min_rows=50),
    _card("metric_guardrails", "business", "北极星与护栏指标", "产品实验或经营决策涉及多目标", "区分总体目标、局部诊断、数据质量和护栏指标，预先定义不可接受的退化。", ["明确决策目标", "预先定义指标层级"], ["北极星上涨就可忽略护栏退化", "事后挑选成功指标"], ["OEC", "护栏", "局部指标", "数据质量"], ["microsoft_experiment_monitoring", "microsoft_post_experiment"]),
    _card("ratio_metric", "business", "比率指标分母审计", "分析转化率、客单价、点击率等比率", "同时检查分子、分母、随机化单位和分析单位；比率不能按简单均值随意聚合。", ["分子分母", "分析单位", "聚合规则"], ["两个比率可直接平均", "分母变化不影响比率解释"], ["分母漂移", "零分母", "加权", "聚合偏差"], ["microsoft_metric_pitfalls"], min_rows=8),
    _card("pricing_elasticity", "business", "价格与需求弹性", "价格、销量和促销随时间或群体变化", "控制季节、渠道、库存和促销混杂；观察数据先报告关联弹性，因果弹性需实验或识别策略。", ["价格", "数量", "时间/市场", "关键促销因素"], ["价格销量负相关就是需求曲线", "历史弹性可无条件外推"], ["内生性", "缺货", "促销", "竞争变化"], ["causal_econometrics", "fpp3"], min_rows=30),
    _card("demand_inventory", "business", "需求预测与库存决策", "补货、容量或库存需要未来需求", "分别评估预测误差与库存成本，按产品层级、提前期和间歇需求选择方法。", ["时间序列", "产品标识", "提前期", "缺货记录"], ["销量等于真实需求", "最低预测误差自动带来最低库存成本"], ["缺货截断", "间歇需求", "层级一致性", "服务水平"], ["fpp3"], min_rows=30),

    _card("panel_fixed_effects", "economics", "面板数据与固定效应", "同一地区、企业或个体跨期观察", "区分单位内与单位间变化，核对时间变化混杂和标准误聚类层级。", ["稳定单位标识", "多个时期"], ["加固定效应就自动得到因果", "时间不变因素是唯一混杂"], ["组内变异", "共同冲击", "聚类标准误", "动态反馈"], ["causal_econometrics"], min_rows=30),
    _card("regression_discontinuity", "economics", "回归断点设计", "处理由明确阈值规则决定", "在阈值附近估计局部效应，检查操纵、带宽、函数形式和协变量连续性。", ["运行变量", "明确阈值", "阈值附近样本"], ["阈值两侧均值差自动可信", "局部效果可直接推广到总体"], ["密度操纵", "带宽", "协变量连续", "安慰剂阈值"], ["rdd_guide", "world_bank_ie"], min_rows=50),
    _card("instrumental_variables", "economics", "工具变量识别", "处理存在未观测混杂但有可信外生工具", "分别论证相关性、排除限制和单调性；解释通常是局部平均处理效应。", ["工具变量", "处理变量", "结果变量", "识别论证"], ["相关变量都能当工具", "IV 结果自动适用于所有人"], ["弱工具", "排除限制", "单调性", "局部外部效度"], ["causal_econometrics", "world_bank_ie"], min_rows=50),
    _card("price_volume_index", "economics", "价格、数量与指数口径", "比较名义金额、实际产出或综合价格指数", "拆分价格与数量效应，记录基期、权重和链式/定基方法。", ["价格与数量或平减指数", "基期定义"], ["名义增长等于实际增长", "不同基期指数可直接相减"], ["基期", "权重更新", "质量变化", "可加总性"], ["oecd_quality"]),
    _card("distributional_effects", "economics", "分布效应与异质性", "平均效果可能掩盖地区、收入或群体差异", "预先定义异质性维度，报告各组样本与区间，并控制多重比较。", ["合法群体字段", "足够组内样本"], ["平均效果代表所有群体", "事后显著子组就是政策目标群体"], ["预设分组", "FDR", "组内样本", "外部效度"], ["world_bank_ie", "benjamini_hochberg"], min_rows=30),
)


DOMAIN_TOKENS = {
    "education": ("学号", "学生", "成绩", "分数", "得分", "排名", "班级", "课程", "学期", "考试", "绩点"),
    "industrial": ("设备", "传感", "温度", "压力", "振动", "电流", "故障", "维修", "工序", "批次", "良率", "产线", "测点"),
    "business": ("收入", "营收", "成本", "利润", "订单", "销售", "用户", "客户", "转化", "留存", "渠道", "库存", "revenue", "cost"),
    "economics": ("gdp", "cpi", "ppi", "失业", "就业", "通胀", "进出口", "贸易", "政策", "地区", "省份", "人口", "汇率", "利率"),
}

GOAL_DOMAINS = {
    "score_audit": "education",
    "business_review": "business",
    "industrial_review": "industrial",
    "economic_review": "economics",
}

TASK_CARD_PRIORITY = {
    "data_quality": ["quality_dimensions", "missing_mechanism", "outlier_review", "sampling_bias", "sensor_integrity", "measurement_system", "education_indicator_comparability", "ratio_metric"],
    "descriptive_profile": ["eda_before_model", "effect_size", "bootstrap_uncertainty", "learning_profile", "business_kpi", "economic_trend", "spc_control_charts"],
    "group_comparison": ["effect_size", "multiple_testing", "bootstrap_uncertainty", "education_fairness", "education_indicator_comparability", "distributional_effects"],
    "relationship_review": ["causal_gate", "model_explanation", "root_cause_gate", "business_kpi", "pricing_elasticity", "panel_fixed_effects"],
    "trend_forecast": ["business_forecast", "industrial_change_point", "industrial_drift", "education_longitudinal", "economic_nowcast", "validation_leakage", "uncertainty_bounds"],
    "causal_evaluation": ["causal_gate", "online_experiment", "education_intervention", "industrial_doe", "economic_impact", "difference_in_differences", "regression_discontinuity", "instrumental_variables"],
}


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


_CORE_METHOD_IDS = (
    "quality_dimensions", "missing_mechanism", "eda_before_model",
    "outlier_review", "causal_gate", "llm_spreadsheet_guard",
)

# A local calculation can cover only part of a broader literature card.  These
# notes deliberately do not call the entire card or its cited paper "executed".
_PARTIAL_APPLICATION_NOTES = {
    "quality_dimensions": "已检查缺失、重复与分类频数；其他统计质量维度仍需核对口径和来源。",
    "eda_before_model": "已计算数值分布；尚未建立或验证预测模型。",
    "outlier_review": "已按适用性条件运行 IQR 探索；极端值仍不是已确认错误。",
    "effect_size": "已计算分组均值差；尚未给出不确定性区间或业务重要性阈值。",
}


def select_method_pack(
    columns: Iterable[Any], *, goal: str = "auto", row_count: int = 0,
    analysis_config: dict[str, Any] | None = None, max_cards: int = 9,
    analysis_tools: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a compact, deterministic method pack for one uploaded table."""
    column_names = [str(column) for column in columns]
    joined = "|".join(_normalize(column) for column in column_names)
    scores = {
        domain: sum(1 for token in tokens if token in joined)
        for domain, tokens in DOMAIN_TOKENS.items()
    }
    explicit = GOAL_DOMAINS.get(goal)
    reasons = ["所有任务均启用通用统计、数据质量与防幻觉方法"]
    if explicit:
        scores[explicit] += 20
        reasons.append(f"用户分析目标明确选择“{DOMAIN_LABELS[explicit]}”")

    ranked = sorted(scores, key=lambda item: (-scores[item], item))
    selected_domains = [domain for domain in ranked if scores[domain] > 0][:2]
    if selected_domains:
        matched = "、".join(DOMAIN_LABELS[item] for item in selected_domains)
        reasons.append(f"字段名称与分析目标匹配：{matched}")
    else:
        reasons.append("未识别到可靠领域信号，仅启用通用方法；不会猜测业务背景")

    # All general cards must be eligible: task priorities previously named
    # seven cards that the six-card allowlist silently made unreachable.
    candidates = [
        card for card in METHOD_CARDS
        if card["domain"] == "general" or card["domain"] in selected_domains
    ]
    priority = TASK_CARD_PRIORITY.get(goal, [])
    if priority:
        order = {card_id: index for index, card_id in enumerate(priority)}
        candidates.sort(key=lambda card: (
            0 if card["id"] in order else 1 if card["id"] in _CORE_METHOD_IDS else 2 if card["domain"] != "general" else 3,
            order.get(card["id"], len(order)),
        ))
        reasons.append(f"按本次问题类型“{goal}”优先检索 {len(priority)} 类方法")
    else:
        candidates.sort(key=lambda card: (
            0 if card["id"] in _CORE_METHOD_IDS else 1 if card["domain"] != "general" else 2,
        ))
    candidates = candidates[:max_cards]

    config = analysis_config or {}
    actual_decisions = {
        card_id: decision
        for decision in (analysis_tools or {}).get("analysisProcess", {}).get("methodDecisions", [])
        for card_id in decision.get("methodCardIds", [])
        if isinstance(card_id, str)
    }
    actual_evidence_refs = {
        tool["evidenceRef"]
        for tool in (analysis_tools or {}).get("executedTools", [])
        if tool.get("status") == "success"
        and isinstance(tool.get("evidenceRef"), str) and bool(tool["evidenceRef"])
    }
    selected_cards: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for card in candidates:
        min_rows = int(card.get("minRows", 1))
        if row_count < min_rows:
            readiness = "insufficient_rows"
            readiness_note = f"当前 {row_count} 行，低于该方法的保守起始条件 {min_rows} 行"
        elif any(term in card["id"] for term in ("forecast", "trend", "did", "nowcast")) and not config.get("timeColumn"):
            readiness = "needs_configuration"
            readiness_note = "需先指定时间字段并核对时间口径"
        else:
            readiness = "method_reference"
            readiness_note = "可用于约束解读；是否正式执行仍取决于全部前提条件"
        source_ids.update(card["sourceIds"])
        public_card = {
            key: value for key, value in card.items() if key != "minRows"
        }
        public_card["methodCore"] = card["method"]
        public_card["method"] = (
            f"适用：{card['useWhen']}｜做法：{card['method']}｜"
            f"最低条件：{'；'.join(card['minimumRequirements'])}｜"
            f"必须核对：{'；'.join(card['requiredChecks'])}"
        )
        decision = actual_decisions.get(card["id"])
        applied = (
            readiness == "method_reference" and decision is not None
            and decision.get("status") == "executed"
            and decision.get("evidenceRef") in actual_evidence_refs
        )
        public_card.update({
            "readiness": readiness, "readinessNote": readiness_note,
            "applicationStatus": "partial" if applied else "reference_only",
            "applicationNote": _PARTIAL_APPLICATION_NOTES[card["id"]] if applied else "仅作方法参考；本次没有该卡对应的已验证计算结果。",
            "executionEvidenceRefs": [decision["evidenceRef"]] if applied else [],
        })
        selected_cards.append(public_card)

    return {
        "version": "cureflow_method_knowledge_v1",
        "purpose": "方法约束与分析路线选择，不作为当前文件事实证据",
        "detectedDomains": ["general", *selected_domains],
        "domainLabels": [DOMAIN_LABELS["general"], *[DOMAIN_LABELS[item] for item in selected_domains]],
        "routingReasons": reasons,
        "libraryMethodCount": len(METHOD_CARDS),
        "librarySourceCount": len(SOURCES),
        "selectedMethodCount": len(selected_cards),
        "partiallyAppliedMethodCount": sum(card["applicationStatus"] == "partial" for card in selected_cards),
        "methods": selected_cards,
        "sources": [dict(id=source_id, **SOURCES[source_id]) for source_id in sorted(source_ids)],
        "guardrails": [
            "方法文献只能约束分析方式，不能证明上传数据中的任何事实。",
            "不满足最低条件的方法必须标记为不可执行或需要配置。",
            "相关、预测、异常检测与因果结论必须明确区分。",
            "生成式模型不得替代本地统计计算，也不得使用未出现的证据引用。",
        ],
    }


def provider_safe_method_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Return the prompt projection; URLs stay in the UI, not the model prompt."""
    return {
        "purpose": pack["purpose"],
        "detectedDomains": pack["detectedDomains"],
        "routingReasons": pack["routingReasons"],
        "methods": [
            {key: method[key] for key in (
                "id", "domain", "title", "useWhen",
                "minimumRequirements", "blockedClaims", "requiredChecks",
                "readiness", "readinessNote", "applicationStatus",
                "applicationNote", "executionEvidenceRefs",
            )} | {"method": method.get("methodCore", method["method"])}
            for method in pack["methods"]
        ],
        "guardrails": pack["guardrails"],
    }
