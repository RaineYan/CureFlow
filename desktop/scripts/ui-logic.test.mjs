import test from 'node:test';
import assert from 'node:assert/strict';
import { readDataFile } from '../src/data-file.mjs';
import { assertLiveProviderResult } from '../src/provider-response.mjs';
import { buildIncidentFlow } from '../src/incident-flow.mjs';
import { fetchLocalService, readServiceResponse, providerConnection, visibleProviderMode } from '../src/provider-connection.mjs';
import { analysisSourceKey, analysisConfigKey, analysisResultKey, rememberAnalysis, recallAnalysis, rememberedForSource, analysisHistoryTitle, analysisFieldRoles } from '../src/analysis-memory.mjs';
import viteConfig from '../vite.config.mjs';

test('zero-call AI reuse requires verified source, matching provider and zero usage', () => {
  const selected = { key: 'doubao', mode: 'doubao_chat' };
  const result = {
    mode: 'DOUBAO_CHAT_LIVE',
    provider: { name: 'doubao', live: true, calledThisRun: false, reused: true },
    aiRecovery: { mode: 'reuse_all', evidenceVerified: true, reusedFindingCount: 1, sourceRunId: 'source-run' },
    recoveryExecution: { sourceRunId: 'source-run' },
    cost: { llmCalls: 0, totalTokens: 0 },
  };
  assert.doesNotThrow(() => assertLiveProviderResult(result, selected));
  for (const mutate of [
    r => { r.aiRecovery.evidenceVerified = false; },
    r => { r.aiRecovery.sourceRunId = 'another-run'; },
    r => { r.provider.calledThisRun = true; },
    r => { r.cost.totalTokens = 100; },
    r => { r.aiRecovery.reusedFindingCount = 0; },
    r => { r.provider.name = 'openai'; },
    r => { r.mode = 'OFFLINE'; },
  ]) {
    const invalid = structuredClone(result);
    mutate(invalid);
    assert.throws(() => assertLiveProviderResult(invalid, selected));
  }
});

test('analysis memory only reuses the same file, sheet, goal, fields, and provider', () => {
  const cache = new Map();
  const source = analysisSourceKey({ inputFingerprint: 'sha-a', selectedSheet: 'Sheet A' }, 'a.xlsx');
  const config = { measureColumn: '收入', groupColumn: '', identifierColumn: '', timeColumn: '' };
  const key = analysisResultKey(source, 'descriptive_profile', config, 'doubao_chat:model-a');
  const entry = { sourceKey: source, goal: 'descriptive_profile', config, mode: 'doubao_chat', modeIdentity: 'doubao_chat:model-a', result: { runId: 'real-run' } };
  rememberAnalysis(cache, key, entry);
  assert.equal(recallAnalysis(cache, source, 'descriptive_profile', config, 'doubao_chat:model-a'), entry);
  assert.equal(recallAnalysis(cache, source, 'descriptive_profile', config, 'doubao_chat:model-b'), null);
  assert.equal(recallAnalysis(cache, source, 'data_quality', config, 'doubao_chat:model-a'), null);
  assert.equal(recallAnalysis(cache, source, 'descriptive_profile', { ...config, measureColumn: '利润' }, 'doubao_chat:model-a'), null);
  assert.equal(recallAnalysis(cache, source, 'descriptive_profile', config, 'openai_responses'), null);
  assert.equal(recallAnalysis(cache, analysisSourceKey({ inputFingerprint: 'sha-a', selectedSheet: 'Sheet B' }, 'a.xlsx'), 'descriptive_profile', config, 'doubao_chat:model-a'), null);
  assert.equal(recallAnalysis(cache, analysisSourceKey({ inputFingerprint: 'sha-b', selectedSheet: 'Sheet A' }, 'a.xlsx'), 'descriptive_profile', config, 'doubao_chat:model-a'), null);
  assert.equal(rememberedForSource(cache, source).length, 1);
});

test('analysis memory keeps only bounded session history and prefers exact model', () => {
  const cache = new Map();
  const source = analysisSourceKey({ inputFingerprint: 'sha-a' }, 'a.csv');
  const config = { measureColumn: '', groupColumn: '', identifierColumn: '', timeColumn: '' };
  const offline = { sourceKey: source, goal: 'data_quality', config, mode: 'offline', result: { runId: 'local' } };
  const live = { ...offline, mode: 'doubao_chat', result: { runId: 'cloud' } };
  rememberAnalysis(cache, analysisResultKey(source, offline.goal, config, offline.mode), offline, 2);
  rememberAnalysis(cache, analysisResultKey(source, live.goal, config, live.mode), live, 2);
  assert.equal(recallAnalysis(cache, source, 'data_quality', config, 'doubao_chat'), live);
  assert.equal(recallAnalysis(cache, source, 'data_quality', config, 'openai_responses'), offline);
  const another = { ...offline, goal: 'descriptive_profile' };
  rememberAnalysis(cache, analysisResultKey(source, another.goal, config, another.mode), another, 2);
  assert.equal(cache.size, 2);
  assert.equal(recallAnalysis(cache, source, 'data_quality', config, 'offline'), null);
  assert.notEqual(analysisConfigKey(source, 'data_quality'), analysisConfigKey(source, 'descriptive_profile'));
});

test('local key-status GET gets same-origin metadata while remote key routes stay blocked', () => {
  const guard = viteConfig.plugins.find(plugin => plugin.name === 'cureflow-lan-access-guard');
  const originalToken = process.env.CUREFLOW_LAN_ACCESS_TOKEN;
  delete process.env.CUREFLOW_LAN_ACCESS_TOKEN;
  let middleware;
  try {
    guard.configureServer({ middlewares: { use: callback => { middleware = callback; } } });
  } finally {
    if (originalToken === undefined) delete process.env.CUREFLOW_LAN_ACCESS_TOKEN;
    else process.env.CUREFLOW_LAN_ACCESS_TOKEN = originalToken;
  }
  const local = { url: '/api/keys/status', method: 'GET', socket: { remoteAddress: '127.0.0.1' }, headers: { host: '127.0.0.1:5173' } };
  let forwarded = false;
  middleware(local, {}, () => { forwarded = true; });
  assert.equal(forwarded, true);
  assert.equal(local.headers.origin, 'http://127.0.0.1:5173');
  const remote = { url: '/api/keys/status', method: 'GET', socket: { remoteAddress: '192.0.2.5' }, headers: { host: '127.0.0.1:5173' } };
  let deniedStatus;
  middleware(remote, { writeHead: status => { deniedStatus = status; }, end: () => {} }, () => assert.fail('remote key status must not proxy'));
  assert.equal(deniedStatus, 403);
  assert.equal(remote.headers.origin, undefined);
});

test('stopped overrides even contradictory ready and allowed provider flags', () => {
  for (const status of [
    { ready: true, apiKeyPresent: true, killSwitchActive: true, blockers: [] },
    { ready: true, apiKeyPresent: true, blockers: ['KILL_SWITCH_ACTIVE'] },
  ]) {
    const state = providerConnection(status);
    assert.equal(state.ready, false);
    assert.equal(state.allowed, false);
    assert.equal(state.label, '已停用');
    assert.match(state.permission, /重启/);
  }
});
test('missing key, missing permission, readiness, and unavailable service are distinct', () => {
  assert.equal(providerConnection({ blockers: ['API_KEY_ENV_MISSING'] }).label, '缺少密钥');
  assert.equal(providerConnection({ apiKeyPresent: true, blockers: ['LOCAL_LIVE_CONFIRMATION_MISSING'] }).label, '等待授权');
  assert.equal(providerConnection({ apiKeyPresent: true, blockers: ['PROVIDER_CONNECTION_UNVERIFIED'] }).label, '待验证');
  const configured = providerConnection({ ready: true, apiKeyPresent: true, blockers: [] });
  assert.equal(configured.ready, true);
  assert.equal(configured.label, '已验证可用');
  assert.equal(providerConnection(null, true).label, '服务未连接');
  assert.equal(providerConnection(null, true).allowed, false);
});
test('startup prefers a verified provider but never turns an expired session into ready', () => {
  const catalog = [{ key: 'openai', mode: 'openai_responses' }, { key: 'doubao', mode: 'doubao_chat' }];
  const live = { openai: { ready: false, blockers: ['API_KEY_ENV_MISSING'] }, doubao: { ready: true, apiKeyPresent: true, blockers: [] } };
  assert.equal(visibleProviderMode(catalog, live, 'openai_responses'), 'doubao_chat');
  assert.equal(visibleProviderMode(catalog, live, 'doubao_chat'), 'doubao_chat');
  assert.equal(visibleProviderMode(catalog, live, 'openai_responses', true), 'openai_responses');
  const expired = { openai: { ready: false, blockers: ['API_KEY_ENV_MISSING'] }, doubao: { ready: false, blockers: ['API_KEY_ENV_MISSING'] } };
  assert.equal(visibleProviderMode(catalog, expired, 'doubao_chat'), 'doubao_chat');
  assert.equal(providerConnection(expired.doubao).ready, false);
});
test('fetch failure is actionable and never retries a configuration request', async () => {
  let calls = 0;
  await assert.rejects(fetchLocalService('/api/provider/configure', { method: 'POST' }, async () => {
    calls++; throw new TypeError('Failed to fetch');
  }), error => error.connectionUnavailable && /本地服务/.test(error.message) && !/Failed to fetch/.test(error.message));
  assert.equal(calls, 1);
});
test('proxy non-JSON response explains service failure instead of exposing parser errors', async () => {
  await assert.rejects(readServiceResponse({ ok: false, status: 502, json: async () => { throw new SyntaxError(); } }),
    error => error.connectionUnavailable && /后端/.test(error.message));
});
test('application denial remains distinct from network failure', async () => {
  await assert.rejects(readServiceResponse({ ok: false, status: 400, json: async () => ({ error: '云调用已停用，请重启服务后重新配置' }) }),
    error => !error.connectionUnavailable && /云调用已停用/.test(error.message));
  assert.deepEqual(await readServiceResponse({ ok: true, json: async () => ({ ready: true }) }), { ready: true });
});

function file(name, bytes) {
  return { name, size: bytes.length, arrayBuffer: async () => Uint8Array.from(bytes).buffer };
}
test('UTF-8 CSV preserves Chinese and clears XLSX representation', async () => {
  const text = '姓名,成绩\n张三,80\n';
  assert.deepEqual(await readDataFile(file('成绩.CSV', Buffer.from(text))), { filename: '成绩.CSV', csvText: text, xlsxBase64: '' });
});
test('GBK CSV decodes without corrupting Chinese', async () => {
  assert.equal((await readDataFile(file('gbk.csv', [0xd6, 0xd0, 0xce, 0xc4]))).csvText, '中文');
});
test('XLSX bytes round trip across chunk boundary', async () => {
  const bytes = Buffer.alloc(70000); for (let i = 0; i < bytes.length; i++) bytes[i] = i % 256;
  const read = await readDataFile(file('table.xlsx', bytes));
  assert.deepEqual(Buffer.from(read.xlsxBase64, 'base64'), bytes);
  assert.equal(read.csvText, '');
});
for (const [name, size, message] of [['old.xls', 10, /旧版 XLS/], ['photo.png', 10, /CSV 或 XLSX/], ['empty.csv', 0, /空文件/], ['large.xlsx', 5242881, /5 MB/]]) {
  test(`reject ${name} before reading`, async () => {
    await assert.rejects(readDataFile({ name, size, arrayBuffer: () => assert.fail('must not read') }), message);
  });
}
test('unreadable files show an actionable error', async () => {
  await assert.rejects(readDataFile({ name: 'locked.csv', size: 1, arrayBuffer: async () => { throw Error('denied'); } }), /下载到本机/);
});
for (const [key, mode] of [['openai', 'openai_responses'], ['qwen', 'qwen_chat'], ['deepseek', 'deepseek_chat'], ['doubao', 'doubao_chat']]) {
  test(`${key} accepts actual backend LIVE mode`, () => {
    assertLiveProviderResult({ mode: `${mode.toUpperCase()}_LIVE`, provider: { live: true, name: key }, cost: { llmCalls: 1 } }, { key, mode });
  });
}
for (const result of [{ provider: { live: false } }, { provider: { live: true }, cost: { llmCalls: 0 } }, { mode: 'QWEN_CHAT_LIVE', provider: { live: true, name: 'qwen' }, cost: { llmCalls: 1 } }]) {
  test(`reject offline, missing calls, or wrong provider: ${JSON.stringify(result)}`, () => {
    assert.throws(() => assertLiveProviderResult(result, { key: 'deepseek', mode: 'deepseek_chat' }));
  });
}

test('incident flow starts empty rather than showing a sample fault', () => {
  const flow = buildIncidentFlow(null);
  assert.equal(flow.steps[0].status, '待开始');
  assert.match(flow.boundary, /不展示预制故障/);
});

test('incident flow separates data clues, downstream plan, and actual execution', () => {
  const flow = buildIncidentFlow({
    input: { filename: 'real.xlsx' },
    currentRecoveryPlan: {
      summary: { issueCount: 3 },
      issues: [{ location: { sheet: '成绩', cell: 'B8' } }],
      impactedTools: [{ key: 'rank_consistency' }],
      reusableTools: [{ key: 'quality_audit' }],
    },
    claims: [{ grounding: { verdict: 'needs_review' } }],
  });
  assert.equal(flow.dataClueCount, 3);
  assert.equal(flow.claimReviewCount, 1);
  assert.match(flow.steps[0].detail, /成绩!B8/);
  assert.match(flow.steps[1].detail, /计划使 1 个已执行工具失效/);
  assert.equal(flow.steps[3].status, '未复测');
  assert.match(flow.boundary, /仍是计划/);
});

test('claim-only risk does not invent a data repair or exact propagation path', () => {
  const flow = buildIncidentFlow({
    claims: [{ grounding: { verdict: 'rejected' } }],
    currentRecoveryPlan: { summary: { issueCount: 0 }, impactedTools: [], reusableTools: [] },
  });
  assert.equal(flow.steps[0].view, 'grounding');
  assert.match(flow.steps[1].detail, /尚无逐句传播定位/);
  assert.match(flow.steps[2].detail, /没有自动改写结论/);
});

test('human-reviewed AI interpretation updates only the report lane', () => {
  const flow = buildIncidentFlow({
    claims: [],
    currentRecoveryPlan: { summary: { issueCount: 0 }, impactedTools: [], reusableTools: [] },
    interpretationReview: { revision: 1, records: [{ decision: 'revise' }] },
  });
  assert.match(flow.steps[0].detail, /1 次模型解读人工复核/);
  assert.equal(flow.steps[1].view, 'workspace');
  assert.match(flow.steps[1].detail, /读者报告/);
  assert.equal(flow.steps[2].status, '已复核');
  assert.equal(flow.steps[3].status, '报告已重建');
  assert.match(flow.steps[3].detail, /未获机器真实性证明/);
  assert.match(flow.boundary, /不重新计算数据/);
});

test('completed recovery reports measured before and after, not planned savings', () => {
  const flow = buildIncidentFlow({ claims: [], currentRecoveryPlan: { summary: { issueCount: 1 } } }, {
    completedRecovery: {
      appliedCount: 2,
      beforeIssueCount: 4,
      afterIssueCount: 1,
      recoveryExecution: {
        actualSelectiveExecution: true,
        executedToolKeys: ['quality_audit'],
        reusedToolKeys: ['rank_consistency', 'relationship_screen'],
      },
    },
  });
  assert.equal(flow.steps[1].status, '已执行');
  assert.match(flow.steps[1].detail, /实际重算 1 个.*复用 2 个/);
  assert.equal(flow.steps[3].status, '已复测');
  assert.match(flow.steps[3].detail, /4 → 1 项/);
  assert.doesNotMatch(flow.steps[3].detail, /节省/);
  assert.match(flow.boundary, /实际执行记录/);
});

test('history titles identify file, sheet, goal and keep user rename through updates', () => {
  const cache=new Map();
  const source=analysisSourceKey({inputFingerprint:'sha-a',selectedSheet:'Sheet A'},'a.xlsx');
  const config={measureColumn:'收入',groupColumn:'',identifierColumn:'',timeColumn:''};
  const key=analysisResultKey(source,'descriptive_profile',config,'offline');
  const entry={sourceKey:source,goal:'descriptive_profile',config,mode:'offline',modeIdentity:'offline',createdAt:'2026-10-09T10:00:00Z',result:{runId:'one',input:{filename:'a.xlsx',sheet:'Sheet A'}}};
  const title=analysisHistoryTitle(entry,[{key:'descriptive_profile',label:'现在是什么情况？'}]);
  assert.match(title,/a\.xlsx · Sheet A · 现在是什么情况？/);
  rememberAnalysis(cache,key,{...entry,title:'销售分析'});
  rememberAnalysis(cache,key,{...entry,result:{...entry.result,runId:'two'}});
  assert.equal(analysisHistoryTitle(cache.get(key),[]),'销售分析');
});

test('analysis request sends only supported field roles while retaining the brief separately', () => {
  const config={measureColumn:'实到',groupColumn:'班级',identifierColumn:'',timeColumn:'',analysisBrief:'比较班级出勤的现实意义'};
  assert.deepEqual(Object.keys(analysisFieldRoles(config)).sort(),['groupColumn','identifierColumn','measureColumn','timeColumn']);
  assert.equal(analysisFieldRoles(config).measureColumn,'实到');
  assert.equal(analysisFieldRoles(config).groupColumn,'班级');
  assert.equal('analysisBrief' in analysisFieldRoles(config),false);
});

test('analysis brief is part of the result cache identity', () => {
  const source=analysisSourceKey({inputFingerprint:'sha-a'},'a.csv');
  const config={measureColumn:'分数',groupColumn:'班级',identifierColumn:'',timeColumn:'',analysisBrief:'关注教学安排'};
  assert.notEqual(analysisResultKey(source,'group_comparison',config,'offline'),analysisResultKey(source,'group_comparison',{...config,analysisBrief:'关注学生差异'},'offline'));
});
