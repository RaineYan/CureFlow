"""Static contract checks for the Electron/backend health bridge."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_main_checks_backend_every_three_seconds() -> None:
    source = _read("desktop/electron/main.cjs")
    assert "setInterval(publishBackendHealth, 3_000)" in source
    assert 'webContents.send("cureflow:backend-health", healthy)' in source
    assert 'ipcMain.handle("cureflow:restart-backend"' in source


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
    assert "重启本地服务" in source
    assert "本地文件检查服务尚未就绪" in source
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
    assert "await waitForHealth(" in source
    assert "CUREFLOW_SMOKE_USER_DATA" in source
    assert "CUREFLOW_SMOKE_USER_DATA" in _read("desktop/electron/main.cjs")
    assert "reserveFreePort()" in source
    assert "CUREFLOW_BACKEND_PORT: String(backendPort)" in source
    assert "http://127.0.0.1:${backendPort}/api/health" in source
    assert "stopElectron(child)" in source


def test_renderer_has_explicit_multi_provider_opt_in_and_kill_switch() -> None:
    source = _read("desktop/src/App.jsx")
    assert 'api("/api/provider-status")' in source
    assert 'api("/api/provider/kill"' in source
    assert "confirmMaxCalls:useAi?6:undefined" in source
    assert 'providerMode!=="offline"' not in source
    assert "confirmLive:useAi" in source
    assert "本次最多 6 次云调用" in source
    assert "密钥通过桌面内部通道进入本机服务" in source
    assert "连接验证成功后才会加密保存" in source
    assert 'onClick={()=>connectSelectedProvider(false)}' in source
    assert 'type="password"' in source
    assert "window.cureflow?.configureProvider" in source
    assert "apiKey:typedKey" in source
    assert "configure(payload)" in source
    assert 'mode:"openai_responses"' in source
    assert 'mode:"qwen_chat"' in source
    assert 'mode:"deepseek_chat"' in source
    assert 'mode:"doubao_chat"' in source
    assert "停用后续云调用" in source
    assert "OPENAI_API_KEY" not in source
    assert "QWEN_API_KEY" not in source
    assert "DEEPSEEK_API_KEY" not in source


def test_lan_web_mode_uses_same_origin_api_and_private_backend_proxy() -> None:
    source = _read("desktop/src/App.jsx")
    package = _read("desktop/package.json")
    vite = _read("desktop/vite.config.mjs")
    styles = _read("desktop/src/brand-theme.css")
    assert 'window.cureflow?.apiBase || import.meta.env.VITE_API_BASE || ""' in source
    assert '"dev:web:lan:full": "node scripts/start-lan.cjs"' in package
    assert '"dev:web:lan":' not in package
    assert '"/api"' in vite
    assert 'target: "http://127.0.0.1:8766"' in vite
    assert "timingSafeEqual" in vite
    assert 'request.headers["x-cureflow-lan-token"]' in vite
    assert "randomBytes(16)" in _read("desktop/scripts/start-lan.cjs")
    assert ".sidebar {" in styles
    assert "position: sticky" in styles
    assert "此访问方式由运行服务的电脑配置模型" in source
    assert "请先输入本次局域网访问码" in vite
    assert "CureFlow 局域网访问" in source


def test_first_run_allows_provider_free_file_check_without_cloud_model() -> None:
    source = _read("desktop/src/App.jsx")
    assert 'useState(false)' in source
    assert 'onClick={()=>setShowLauncher(false)}' in source
    assert 'className="header-provider-action" onClick={()=>setShowLauncher(true)}' in source
    assert 'api("/api/inspect"' in source
    assert 'if(useAi&&!selectedProviderReady){setShowLauncher(true);return;}' in source
    assert 'const runMode=useAi?providerMode:"offline"' in source
    assert 'providerMode:runMode,confirmLive:useAi' in source
    assert 'onClick={()=>runAnalysis(false)}' in source
    assert 'onClick={selectedProviderReady?()=>runAnalysis(true):()=>setShowLauncher(true)}' in source
    assert "开始本地分析（不调用 AI）" in source
    assert "使用 AI 解读（最多 6 次调用）" in source
    assert "局域网访问者不能使用此配置入口" in source
    assert 'model:"doubao-seed-2-0-pro-260215"' in source
    assert 'const modelId=provider==="doubao"?selectedProvider.model:undefined' in source
    assert 'providerModelId' not in source
    assert '火山方舟实际开通的模型或接入点 ID<input' not in source
    assert '{canConfigureProvider?<><div className="key-entry">' in source
    assert '<div className="key-entry">' in source


def test_field_roles_follow_the_selected_question_and_clear_irrelevant_roles() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "goalConfigMemoryRef.current.get(analysisConfigKey(sourceKey,nextGoal))" in source
    assert 'measureColumn:nextGoal==="data_quality"?"":analysisConfig.measureColumn' in source
    assert 'groupColumn:["exploratory_analysis","group_comparison","causal_evaluation"].includes(nextGoal)' in source
    assert 'timeColumn:["exploratory_analysis","trend_forecast","causal_evaluation"].includes(nextGoal)' in source
    assert 'hidden={analysisGoal==="data_quality"}' in source
    assert 'hidden={!["exploratory_analysis","group_comparison","causal_evaluation"].includes(analysisGoal)}' in source
    assert 'hidden={!["exploratory_analysis","trend_forecast","causal_evaluation"].includes(analysisGoal)}' in source
    assert 'hidden={!["exploratory_analysis","data_quality","descriptive_profile","group_comparison","relationship_review","causal_evaluation"].includes(analysisGoal)}' in source
    assert '.analysis-role-picker.contextual' in styles


def test_main_keeps_session_keys_in_memory_and_delegates_opt_in_persistence() -> None:
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
    assert 'ipcMain.handle("cureflow:keys"' in source


def test_existing_environment_key_can_be_session_authorized_without_exposure() -> None:
    main = _read("desktop/electron/main.cjs")
    renderer = _read("desktop/src/App.jsx")
    assert "useExistingEnvironmentKey" in main
    assert 'useExistingEnvironmentKey: true, confirmSession: true' in main
    assert '"X-CureFlow-Desktop-Token": desktopKeyToken' in main
    assert 'sessionProviderEnv[spec.confirmEnv] = "1"' in main
    assert "connectSelectedProvider(true)" in renderer
    assert 'onClick={()=>connectSelectedProvider(true)}' in renderer
    assert 'useEffect(()=>{if(!showLauncher' not in renderer
    assert "apiKeyValueExposed" not in renderer


def test_renderer_discloses_cloud_data_transfer_and_cost_labels() -> None:
    source = _read("desktop/src/App.jsx")
    assert "必要摘要会发送至 {selectedProvider.name}" in source
    assert "store:false" in source
    assert "Provider" in source
    assert "Estimated cost" in source
    assert "未配置报价" in source
    assert "usageType" in source


def test_desktop_picker_supports_csv_and_xlsx_without_exposing_paths() -> None:
    main = _read("desktop/electron/main.cjs")
    preload = _read("desktop/electron/preload.cjs")
    assert 'extensions: ["csv", "xlsx"]' in main
    assert 'return readNativeDataFile(result.filePaths[0])' in main
    native = _read("desktop/electron/data-file.cjs")
    shared = _read("desktop/electron/data-format.mjs")
    assert 'name: path.basename(filePath)' in native
    assert 'import("./data-format.mjs")' in native
    assert 'xlsxBase64: btoa(binary)' in shared
    assert 'ipcMain.handle("cureflow:select-data-file"' in main
    assert "selectDataFile" in preload
    assert 'ipcRenderer.invoke("cureflow:select-data-file")' in preload


def test_renderer_sends_exactly_one_supported_upload_representation() -> None:
    source = _read("desktop/src/App.jsx")
    assert "CSV / XLSX · ≤ 5 MB" in source
    assert 'accept=".csv,.xlsx,' in source
    assert 'const input=xlsxBase64?{xlsxBase64}:{csvText}' in source
    assert "文件先由运行 CureFlow 的电脑检查" in source
    assert "使用 AI 时仅发送必要摘要" in source
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


def test_sidebar_uses_original_cureflow_logo() -> None:
    source = _read("desktop/src/App.jsx")
    assert 'className="brand-mark brand-logo-symbol"><img src="./cureflow-logo.png"' in source
    assert "./cureflow-mark.svg" not in source


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


def test_recovery_uses_only_current_run_in_product_ui() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "价值一眼可见" not in source
    assert "本次分析概览" in source
    assert 'className="recovery-value"' in source
    assert "本次文件修正影响与恢复计划" in source
    assert 'api("/api/recovery-evidence")' not in source
    assert "冻结 EXP-02 历史模拟证据" not in source
    assert "这是尚未执行的计划步骤" in source
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


def test_product_ui_keeps_model_optional_and_hides_deterministic_fixture() -> None:
    source = _read("desktop/src/App.jsx")
    catalog = source[source.index("const providerCatalog"):source.index("const taskViews")]
    assert 'mode:"offline"' not in catalog
    assert "确定性模型" not in source
    assert "按需连接 AI 解读" in source
    assert "本地检查无需连接 AI" in source
    assert 'disabled={!selectedProviderReady}' in source
    assert 'localStorage.getItem("cureflow-provider-mode")' in source
    assert 'visibleProviderMode(providerCatalog,status,providerMode,explicitProviderChoiceRef.current)' in source
    assert "当前启动没有 {selectedProvider.name} 密钥" in source
    assert 'keyStatus?.optedIn' in source
    assert 'saved.length===1)setProviderMode(saved[0].mode)' in source


def test_ui_requires_provider_free_input_inspection_before_analysis() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'api("/api/inspect"' in source
    assert "系统实际读取" in source
    assert "未调用 AI" in source
    assert "切换主数据表" in source
    assert "开始本地分析（不调用 AI）" in source
    assert 'if(!inspection){setError("请先完成数据读取检查")' in source
    assert "读取错误时先切换工作表，不要直接生成报告" in source
    assert ".inspection-panel" in styles
    assert ".column-preview" in styles


def test_changing_question_or_field_roles_only_restores_matching_results() -> None:
    source = _read("desktop/src/App.jsx")
    assert "function invalidateCurrentAnalysis" in source
    assert "function changeAnalysisGoal" in source
    assert "restoreRememberedAnalysis(inspection,filename,nextGoal,nextConfig,providerMode)" in source
    assert "restoreRememberedAnalysis(inspection,filename,analysisGoal,next,providerMode)" in source
    assert "analysisResultKey(sourceKey,analysisGoal,runConfig,runModeIdentity)" in source
    assert "analysisModeIdentity(mode)" in source
    assert "if(allowMemory)restoreRememberedAnalysis" in source
    assert "await inspectUpload(next,result.input?.sheet||null,false)" in source
    assert "changeAnalysisGoal(event.target.value)" in source
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
    assert "只有导出修正版并完成重新分析后，才会显示实际复测结果" in source
    assert "currentRecoveryPlan.source.inputFingerprint" in source
    assert "计划少执行步骤" in source
    assert "demonstrateRecovery" not in source
    assert "recoveryPhase" not in source
    assert 'aria-live="polite"' in source
    assert ".recovery-feedback" in styles
    assert ".recovery-outcome" in styles
    assert ".current-source" in styles
    assert "实际恢复记录" in source


def test_product_ui_has_no_sample_or_timed_recovery_demo() -> None:
    source = _read("desktop/src/App.jsx")
    assert 'api("/api/sample")' not in source
    assert "加载内置示例" not in source
    assert 'api("/api/recovery-evidence")' not in source
    assert "setTimeout" not in source
    assert "demonstrateRecovery" not in source
    assert "assertLiveProviderResult(analyzed,selectedProvider)" in source


def test_switching_provider_never_reuses_a_different_cloud_report() -> None:
    source = _read("desktop/src/App.jsx")
    assert "function changeProviderMode(nextMode)" in source
    assert 'invalidateCurrentAnalysis("分析模型已切换；只有匹配该模型的旧结果才会找回。")' in source
    assert "restoreRememberedAnalysis(inspection,filename,analysisGoal,analysisConfig,nextMode)" in source
    assert "onClick={()=>changeProviderMode(item.mode)}" in source


def test_browser_csv_decodes_utf8_or_gbk_before_inspection() -> None:
    source = _read("desktop/src/App.jsx")
    assert 'await readDataFile(file)' in source
    assert 'onDrop={dropFile}' in source
    assert 'event.target.value=""' in source


def test_upload_notice_discloses_lan_file_transfer_and_https_limit() -> None:
    source = _read("desktop/src/App.jsx")
    assert "文件先由运行 CureFlow 的电脑检查" in source
    assert "局域网模式无 HTTPS，请勿上传敏感文件" in source


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
    assert "没有发现需要修正的数据或断言问题" in source
    assert "这代表什么" in source
    assert "这不代表什么" in source
    assert "返回查看分析与建议" in source
    assert "查看 AI 结论证据" in source
    assert ".recovery-clear-summary" in styles
    assert ".shell:has(.recovery-clear-summary) .recovery-panel.current-run" in styles
    assert ".shell:has(.recovery-clear-summary) .recovery-feedback" in styles


def test_recovery_view_routes_claim_failures_to_claim_review_not_data_repair() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "数据规则未报错，但有" in source
    assert "问题来自断言内容或证据引用，不应修改原始数据" in source
    assert "当前没有自动改写结论" in source
    assert "查看未通过断言" in source
    assert ".claim-recovery-summary" in styles
    assert ".shell:has(.claim-recovery-summary) .recovery-panel.current-run" in styles
    assert ".shell:has(.claim-recovery-summary) .recovery-feedback" in styles


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
    assert "本地工具实际重算" in source
    assert "本地工具实际复用" in source
    assert "本地结果与报告已重新生成；是否调用 AI 以本轮执行记录为准" in source
    assert ".repair-delta" in styles
    assert ".cell-diff" in styles
    assert ".repair-verdict-columns" in styles
    assert ".recovery-outcome.expanded" in styles


def test_ui_exposes_real_local_analysis_tools_and_goal_selection() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "这次要分析什么" in source
    assert '<select value={analysisGoal}' in source
    assert "现在是什么情况？" in source
    assert "哪些因素有关联？" in source
    assert "真实数据分析与修正建议" in source
    assert "本次真正执行" in source
    assert "本次直接复用" in source
    assert "修正版复测只重算受影响工具" in source
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
    assert "当前不估计因果效应" in source
    assert ".method-knowledge" in styles
    assert ".method-card-grid" in styles


def test_workspace_keeps_advanced_analysis_without_redundant_cards() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'className="analysis-entry" aria-label="数据分析入口"' not in source
    assert '<details className="advanced-analysis">' in source
    assert '<details className="field-settings"' in source
    assert '<details className="column-preview-details"' in source
    assert '<details className="sidebar-history"' in source
    assert '<details className="analysis-memory-panel"' not in source
    assert '{result&&<div className="analysis-overview">' in source
    assert ".view-workspace .hero-grid { grid-template-columns: minmax(0, 1fr)" in styles


def test_method_cards_distinguish_retrieval_from_partial_computation() -> None:
    source = _read("desktop/src/App.jsx")
    assert "knowledgeContext.partiallyAppliedMethodCount??0" in source
    assert "关联本次计算（部分）" in source
    assert 'method.applicationStatus==="partial"?"部分关联本次计算":"仅方法参考"' in source
    assert "完整方法已执行" in source


def test_session_analysis_history_lives_in_sidebar_and_opens_workspace() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    sidebar = source.split('<aside className="sidebar">', 1)[1].split("</aside>", 1)[0]
    assert sidebar.index('<nav aria-label="CureFlow 大任务">') < sidebar.index('<details className="sidebar-history"')
    assert "rememberedAnalyses.map(entry=>" in sidebar
    assert "onClick={()=>openRememberedAnalysis(entry)}" in sidebar
    assert 'setRestoredFromMemory(true);setActiveView("workspace");' in source
    assert ".sidebar-history-list {" in styles


def test_report_task_is_plain_language_and_hides_evidence_topology() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'label:"最终结论"' in source
    assert "用人话说清楚" in source
    assert "仍需人工确认的边界" in source
    assert ".view-evidence .content-grid .claims { display: none; }" in styles


def test_ui_exposes_real_unified_impact_paths_without_claiming_execution() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    helper = _read("desktop/src/incident-flow.mjs")
    assert "currentImpactReport=result?.currentImpactReport" in source
    assert "数据、断言和证据的影响路径" in source
    assert "异常证据引用" in source
    assert "计划可复用（尚未实际跳过）" in source
    assert "当前显示的影响与复用仍是计划" in helper
    assert "actualSelectiveExecution" in helper
    assert ".incident-flow-panel" in styles
    assert ".impact-path-panel" in styles


def test_ui_sends_bound_recovery_context_and_shows_actual_reuse() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert "sourceInputFingerprint:recoveryAtStart.beforeFingerprint" in source
    assert "appliedIssueIds:recoveryAtStart.appliedIssueIds" in source
    assert "recoveryExecution:analyzed.recoveryExecution" in source
    assert "实际重算" in source
    assert "实际复用" in source
    assert "本地结果与报告已重新生成；是否调用 AI 以本轮执行记录为准" in source
    assert ".executed-tools article.reused" in styles
    assert ".actual-recovery-detail" in styles


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
    connection = _read("desktop/src/provider-connection.mjs")
    styles = _read("desktop/src/brand-theme.css")
    assert "密钥：已读取，尚未通过连接验证" in source
    assert "密钥：未配置（选中后在下方输入）" in source
    assert "云调用：本次启动已允许" in connection
    assert "云调用：尚未允许（配置密钥时完成）" in connection
    assert "允许并验证已有密钥（1 次调用）" in source
    assert 'apiKey:typedKey' in source
    assert '"/api/provider/probe"' in source
    assert "未发送用户文件" in source
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in styles
    assert ".analysis-role-picker > div { display: flex; grid-column: 1 / -1" in styles


def test_review_dialog_has_exact_cell_paging_and_six_call_click_confirmation() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/brand-theme.css")
    assert 'confirmMaxCalls:useAi?6:undefined' in source
    assert 'confirmLive:useAi' in source
    assert 'setConfirmLive' not in source
    assert 'type="checkbox" checked={confirmLive}' not in source
    assert "本次最多 6 次云调用" in source
    assert 'api("/api/repair/issues"' in source
    assert 'role="dialog" aria-modal="true"' in source
    assert "第 {item.location.row} 行" in source
    assert "{item.location.cell}" in source
    assert "人工复核" in source
    assert ".review-dialog" in styles


def test_workspace_has_one_contextual_next_action_without_losing_task_views() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/workspace-refresh.css")
    assert 'className="task-next-action" aria-label="当前结果的建议操作"' in source
    assert 'className="sidebar-secondary"' in source
    assert 'className="sidebar-settings"' in source
    assert 'taskViews.slice(1).map' in source
    assert ".task-next-action h2" in styles
    assert ".sidebar-secondary button" in styles


def test_review_queue_keeps_reason_visible_before_expanding_actions() -> None:
    source = _read("desktop/src/App.jsx")
    styles = _read("desktop/src/workspace-refresh.css")
    assert '<details className="review-row" key={item.id}>' in source
    assert '<summary className="review-issue-head">' in source
    assert '<small>{explainReviewIssue(item)}</small>' in source
    assert ".review-row > summary" in styles
    assert ".review-row[open]" in styles
