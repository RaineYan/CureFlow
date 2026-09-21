"""Static contract checks for the Electron/backend health bridge."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_main_checks_backend_every_thirty_seconds() -> None:
    source = _read("desktop/electron/main.cjs")
    assert "setInterval(publishBackendHealth, 30_000)" in source
    assert 'webContents.send("cureflow:backend-health", healthy)' in source


def test_preload_exposes_health_subscription() -> None:
    source = _read("desktop/electron/preload.cjs")
    assert "onBackendHealth" in source
    assert "removeListener" in source


def test_preload_exposes_session_only_provider_connection() -> None:
    source = _read("desktop/electron/preload.cjs")
    assert "configureProvider" in source
    assert 'ipcRenderer.invoke("cureflow:configure-provider", payload)' in source


def test_renderer_shows_required_outage_message() -> None:
    source = _read("desktop/src/App.jsx")
    assert "本地引擎未响应，请重启" in source
    assert "setBackendHealthy" in source


def test_stderr_threshold_has_friendly_dialog_and_log() -> None:
    source = _read("desktop/electron/main.cjs")
    assert "STDERR_DIALOG_THRESHOLD = 20" in source
    assert "本地服务异常，请查看日志" in source
    assert "cureflow-backend.log" in source


def test_smoke_script_launches_electron_and_checks_health() -> None:
    package = _read("desktop/package.json")
    source = _read("desktop/scripts/smoke-test.cjs")
    assert '"smoke-test": "node scripts/smoke-test.cjs"' in package
    assert 'require("electron")' in source
    assert "await wait(5_000)" in source
    assert "http://127.0.0.1:8766/api/health" in source
    assert "stopElectron(child)" in source


def test_renderer_has_explicit_multi_provider_opt_in_and_kill_switch() -> None:
    source = _read("desktop/src/App.jsx")
    assert 'api("/api/provider-status")' in source
    assert 'api("/api/provider/kill"' in source
    assert "confirmMaxCalls:6" in source
    assert 'providerMode!=="offline"' not in source
    assert "confirmLive:true" in source
    assert "我确认本次最多 6 次调用" in source
    assert "Key 仅通过 Electron 内部通道进入主进程内存" in source
    assert 'type="password"' in source
    assert "window.cureflow?.configureProvider" in source
    assert "{provider:selectedProvider.key,apiKey:providerKey}" in source
    assert "configure(payload)" in source
    assert 'mode:"openai_responses"' in source
    assert 'mode:"qwen_chat"' in source
    assert 'mode:"deepseek_chat"' in source
    assert "停用后续云调用" in source
    assert "OPENAI_API_KEY" not in source
    assert "QWEN_API_KEY" not in source
    assert "DEEPSEEK_API_KEY" not in source


def test_main_keeps_user_entered_keys_in_session_memory_only() -> None:
    source = _read("desktop/electron/main.cjs")
    assert 'ipcMain.handle("cureflow:configure-provider"' in source
    assert "const sessionProviderEnv = Object.create(null)" in source
    assert "...sessionProviderEnv" in source
    assert "delete sessionProviderEnv[spec.keyEnv]" in source
    assert "for (const key of Object.keys(sessionProviderEnv)) delete sessionProviderEnv[key]" in source
    handler_start = source.index('ipcMain.handle("cureflow:configure-provider"')
    handler_end = source.index("async function waitForBackend", handler_start)
    handler = source[handler_start:handler_end]
    assert "writeFile" not in handler
    assert "appendFile" not in handler


def test_existing_environment_key_can_be_session_authorized_without_exposure() -> None:
    main = _read("desktop/electron/main.cjs")
    renderer = _read("desktop/src/App.jsx")
    assert "useExistingEnvironmentKey" in main
    assert "process.env[spec.keyEnv]" in main
    assert 'sessionProviderEnv[spec.confirmEnv] = "1"' in main
    assert "connectSelectedProvider(true)" in renderer
    assert 'selectedProviderBlockers!=="LOCAL_LIVE_CONFIRMATION_MISSING"' in renderer
    assert "apiKeyValueExposed" not in renderer


def test_renderer_discloses_cloud_data_transfer_and_cost_labels() -> None:
    source = _read("desktop/src/App.jsx")
    assert "分析所需指标将发送至 ${selectedProvider.name}" in source
    assert "store:false" in source
    assert "Provider" in source
    assert "Estimated cost" in source
    assert "未配置报价" in source
    assert "usageType" in source


def test_desktop_picker_supports_csv_and_xlsx_without_exposing_paths() -> None:
    main = _read("desktop/electron/main.cjs")
    preload = _read("desktop/electron/preload.cjs")
    assert 'extensions: ["csv", "xlsx"]' in main
    assert 'workbook.toString("base64")' in main
    assert 'ipcMain.handle("cureflow:select-data-file"' in main
    assert "selectDataFile" in preload
    assert 'ipcRenderer.invoke("cureflow:select-data-file")' in preload


def test_renderer_sends_exactly_one_supported_upload_representation() -> None:
    source = _read("desktop/src/App.jsx")
    assert "CSV / XLSX · ≤ 5 MB" in source
    assert 'accept=".csv,.xlsx,' in source
    assert 'const input=xlsxBase64?{xlsxBase64}:{csvText}' in source
    assert "文件先在本机完成结构检查" in source
    assert "确认工作表、表头、字段和样例值后" in source
    assert "const resultGoal=analysisGoals.find" in source
    assert 'resultGoal?.label||result.input?.analysisModeLabel' in source


def test_desktop_uses_cureflow_brand_asset_and_logo_palette() -> None:
    source = _read("desktop/src/App.jsx")
    html = _read("desktop/index.html")
    main = _read("desktop/electron/main.cjs")
    styles = _read("desktop/src/brand-theme.css")
    assert source.count('./cureflow-logo.png') >= 2
    assert 'TRACE · ANALYZE · RECOVER' in source
    assert 'href="./cureflow-logo.png"' in html
    assert '"cureflow-logo.png"' in main
    assert "--brand-blue: #1769ff" in styles


def test_sidebar_switches_between_large_task_views_without_losing_state() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'const [activeView,setActiveView]=useState("workspace")' in source
    assert 'aria-label="CureFlow 大任务"' in source
    assert 'onClick={()=>setActiveView(item.key)}' in source
    for view in ("workspace", "workflow", "grounding", "recovery", "evidence"):
        assert f'key:"{view}"' in source
    assert 'hidden={activeView!=="workspace"}' in source
    assert 'hidden={activeView!=="workflow"}' in source
    assert 'hidden={activeView!=="grounding"}' in source
    assert 'hidden={activeView!=="recovery"}' in source
    assert 'hidden={activeView!=="evidence"}' in source
    assert ".sidebar nav button.active" in styles
    assert "[hidden] { display: none !important; }" in styles


def test_recovery_uses_current_run_and_keeps_mock_as_secondary_history() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "价值一眼可见" not in source
    assert "本次分析概览" in source
    assert 'className="recovery-value"' in source
    assert "本次文件修正影响与恢复计划" in source
    assert "冻结 EXP-02 历史模拟证据（不属于本次文件）" in source
    assert "不是 EXP-02 模拟调用数" in source
    assert ".overview-state" in styles
    assert ".recovery-value" in styles


def test_ui_explains_sufficiency_hallucination_applicability_and_recovery_in_plain_language() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "dataSufficiency" in source
    assert "生成式幻觉风险" in source
    assert 'riskApplicable&&riskValue!=null?`${riskValue}%`:"不适用"' in source
    assert "本次文件发生了什么" in source
    assert "恢复计划唯一对应" in source
    assert "需要重算" in source
    assert "可以复用" in source
    assert "选择依据" in source
    assert "人工复核项不会被自动填值" in source
    assert ".sufficiency-callout" in styles
    assert ".incident-explainer" in styles


def test_ui_discloses_excel_sheet_selection_and_excluded_auxiliary_tables() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "workbookInventory" in source
    assert "Excel 读取范围已确认" in source
    assert "未检测到可信表头" in source
    assert "均未混入主表统计" in source
    assert ".extraction-callout" in styles
    assert "抓取与数据检查" in source
    assert "请优先核对" in source
    assert ".semantic-findings" in styles


def test_product_ui_requires_a_real_provider_and_hides_deterministic_fixture() -> None:
    source = _read("desktop/src/App.jsx")
    catalog = source[source.index("const providerCatalog"):source.index("const taskViews")]
    assert 'mode:"offline"' not in catalog
    assert "确定性模型" not in source
    assert "选择本次 AI 模型" in source
    assert "只使用真实 Provider" in source
    assert 'useState("openai_responses")' in source


def test_ui_requires_provider_free_input_inspection_before_analysis() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'api("/api/inspect"' in source
    assert "系统实际读取" in source
    assert "未调用 AI" in source
    assert "切换主数据表" in source
    assert "确认读取范围并分析" in source
    assert 'if(!inspection){setError("请先完成数据读取检查")' in source
    assert "读取错误时先切换工作表，不要直接生成报告" in source
    assert ".inspection-panel" in styles
    assert ".column-preview" in styles


def test_changing_question_or_field_roles_clears_stale_results() -> None:
    source = _read("desktop/src/App.jsx")
    assert "function invalidateCurrentAnalysis" in source
    assert "function changeAnalysisGoal" in source
    assert "旧结果已清除" in source
    assert "onClick={()=>changeAnalysisGoal(goal.key)}" in source
    assert "关联分析至少需要 2 个含 4 个以上有效数值的字段" in source
    assert "趋势分析需要先指定时间字段" in source


def test_ui_binds_report_to_current_input_and_ignores_stale_async_results() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "inputFingerprint" in source
    assert "分析结果与当前文件不一致，已阻止展示" in source
    assert "analysisRequestRef" in source
    assert "dataRevisionRef" in source
    assert "本报告唯一对应" in source
    assert "输入指纹" in source
    assert ".result-source-binding" in styles


def test_recovery_view_reports_current_file_plan_and_execution_boundary() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "本次文件反馈" in source
    assert "本次恢复计划预演完成" in source
    assert "尚未修改文件或执行恢复" in source
    assert "currentRecoveryPlan.source.inputFingerprint" in source
    assert "计划少执行步骤" in source
    assert "预演只更新这里的状态，不写文件、不调用模型" in source
    assert 'aria-live="polite"' in source
    assert ".recovery-feedback" in styles
    assert ".recovery-outcome" in styles
    assert ".current-source" in styles
    assert ".historical-evidence" in styles


def test_workflow_view_uses_current_run_instead_of_synthetic_benchmark() -> None:
    source = _read("desktop/src/App.jsx")
    assert "本次执行实况" in source
    assert "只来自“{result.input?.filename}”本次运行" in source
    assert "还没有本次执行记录" in source
    assert "不会显示预制 token 或成本" in source
    assert "traceDiff&&" not in source
    assert "fixture tokens" not in source
    assert "FULL RERUN" not in source
    assert "SELECTIVE RECOVERY" not in source


def test_recovery_view_replaces_zero_step_technical_diagram_with_plain_language() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "currentRecoveryPlan?.summary.issueCount===0" in source
    assert "没有发现需要修正的数据错误" in source
    assert "这代表什么" in source
    assert "这不代表什么" in source
    assert "返回查看分析与建议" in source
    assert "查看 AI 结论证据" in source
    assert ".recovery-clear-summary" in styles
    assert ".shell:has(.recovery-clear-summary) .recovery-panel.current-run" in styles
    assert ".shell:has(.recovery-clear-summary) .recovery-feedback" in styles


def test_ui_exposes_review_first_repair_workbench_and_exports_a_copy() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    preload = _read("desktop/electron/preload.cjs")
    main = _read("desktop/electron/main.cjs")
    assert "问题定位与实际修正" in source
    assert "可安全修正" in source
    assert "输入从原始记录核对后的值" in source
    assert "生成并载入修正版复测" in source
    assert "不覆盖原文件" in source
    assert "confirmManualCorrections" in source
    assert "修正版已经载入，但还不能称为“已恢复”" in source
    assert "修正版已经由" in source
    assert "重新分析，不是预演结果" in source
    assert 'repairRequest("/api/repair/export")' in source
    assert "saveDataFile" in preload
    assert "cureflow:save-data-file" in main
    assert ".repair-workbench" in styles
    assert ".repair-list" in styles
    assert ".manual-correction" in styles
    assert ".recovery-verification" in styles


def test_ui_exposes_explicit_analysis_field_roles() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "明确本次字段角色" in source
    assert "measureColumn" in source
    assert "groupColumn" in source
    assert "identifierColumn" in source
    assert "timeColumn" in source
    assert "column.analysisEligible" in source
    assert "没有至少 4 个有效数值的主指标" in source
    assert "需要先指定主指标字段" in source
    assert ".analysis-role-picker" in styles


def test_report_renders_markdown_tables_as_aligned_html_tables() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "function ReportContent" in source
    assert '<ReportContent text={result.report}/>' in source
    assert 'className="report-table"' in source
    assert 'className={/^[-+]?\\d/.test' in source
    assert ".report-table-wrap" in styles
    assert "font-variant-numeric: tabular-nums" in styles
    assert "text-align: right" in styles


def test_repair_workbench_previews_real_before_after_and_expanded_retest() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'api(path' in source
    assert 'repairRequest("/api/repair/preview")' in source
    assert "修正前后预验证" in source
    assert "将要修改的单元格" in source
    assert "预计已解决" in source
    assert "仍然存在" in source
    assert "新出现问题" in source
    assert "修正版已经由" in source
    assert "重新分析，不是预演结果" in source
    assert "分析线索" in source
    assert "证据支持结论" in source
    assert ".repair-delta" in styles
    assert ".cell-diff" in styles
    assert ".repair-verdict-columns" in styles
    assert ".recovery-outcome.expanded" in styles


def test_ui_exposes_real_local_analysis_tools_and_goal_selection() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "这次要解决什么" in source
    assert "现在是什么情况？" in source
    assert "哪些因素有关联？" in source
    assert "真实数据分析与修正建议" in source
    assert "本地工具已执行" in source
    assert "模型不会替代统计计算" in source
    assert "analysisGoal" in source
    assert ".analysis-goal-picker" in styles
    assert ".analysis-toolbox" in styles
    assert ".tool-recommendations" in styles


def test_windows_distribution_bundles_backend_without_provider_credentials() -> None:
    package = _read("desktop/package.json")
    main = _read("desktop/electron/main.cjs")
    build_script = _read("desktop/scripts/build-backend.ps1")
    assert '"dist:win"' in package
    assert '"electron-builder"' in package
    assert '"extraResources"' in package
    assert "process.resourcesPath" in main
    assert "cureflow-backend.exe" in main
    assert "PyInstaller" in build_script
    assert "OPENAI_API_KEY" not in build_script
    assert "QWEN_API_KEY" not in build_script
    assert "DEEPSEEK_API_KEY" not in build_script


def test_ui_exposes_grounded_ai_interpretation_and_privacy_boundary() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "模型解读与下一步" in source
    assert "需要人工复核" in source
    assert "有效证据引用" in source
    assert "建议你接下来这样做" in source
    assert "不发送原始行、样例值及姓名/学号等字段中的具体值" in source
    assert ".ai-interpretation" in styles
    assert ".privacy-strip" in styles


def test_ui_exposes_routed_method_knowledge_and_limits() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "本次分析的方法依据" in source
    assert "论文与权威来源" in source
    assert "禁止误判" in source
    assert "方法用于约束怎么分析，不代替当前文件证据" in source
    assert "数据可靠吗？" in source
    assert "哪些群体有差异？" in source
    assert "趋势怎样，会怎么变？" in source
    assert "措施真的有效吗？" in source
    assert "不回答：普通前后差就是因果" in source
    assert ".method-knowledge" in styles
    assert ".method-card-grid" in styles


def test_report_task_is_plain_language_and_hides_evidence_topology() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'label:"报告结论"' in source
    assert "先用人话讲清楚" in source
    assert "技术证据移到幻觉检测页" in source
    assert ".view-evidence .content-grid .claims { display: none; }" in styles


def test_ui_scale_is_adjustable_persisted_and_applied_to_the_whole_window() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    preload = _read("desktop/electron/preload.cjs")
    assert "uiScaleOptions" in source
    assert 'label:"小"' in source
    assert 'label:"标准"' in source
    assert 'label:"大"' in source
    assert 'label:"特大"' in source
    assert 'localStorage.getItem("cureflow-ui-scale")' in source
    assert 'localStorage.setItem("cureflow-ui-scale"' in source
    assert "<ScaleControl" in source
    assert "setUiScale" in preload
    assert "webFrame.setZoomFactor" in preload
    assert "factor < 0.85" in preload
    assert "factor > 1.35" in preload
    assert ".scale-control" in styles
    assert ".header-controls" in styles


def test_scaled_ui_keeps_field_roles_readable_and_provider_status_actionable() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "密钥：本次启动有效" in source
    assert "密钥：未配置（选中后在下方输入）" in source
    assert "云调用：本次启动已允许" in source
    assert "云调用：尚未允许（配置密钥时完成）" in source
    assert "会同时启用本次启动的云调用许可" in source
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in styles
    assert ".analysis-role-picker > div { display: flex; grid-column: 1 / -1" in styles
