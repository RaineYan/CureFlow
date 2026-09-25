// Status checks never validate credentials by making a paid provider call.
export async function fetchLocalService(url, options, fetcher = globalThis.fetch) {
  try {
    return await fetcher(url, options);
  } catch {
    throw Object.assign(new Error('无法连接 CureFlow 本地服务。请启动前后端服务，再点击“刷新连接状态”；这不代表模型密钥无效。'), { connectionUnavailable: true });
  }
}

export async function readServiceResponse(response) {
  let data;
  try {
    data = await response.json();
  } catch {
    throw Object.assign(new Error('本地服务未返回有效结果，可能是后端未启动或已中断。请恢复服务后刷新连接状态。'), { connectionUnavailable: true });
  }
  if (!response.ok) throw new Error(data?.error || `本地服务请求失败（${response.status}），请稍后重试。`);
  return data;
}

export function providerConnection(status, unavailable = false) {
  if (!status) return { ready: false, allowed: false, stopped: false,
    label: unavailable ? '服务未连接' : '正在检查',
    permission: unavailable ? '○ 服务未连接，许可状态未知' : '○ 正在检查云调用许可' };
  const blockers = status.blockers || [];
  const stopped = status.killSwitchActive === true || blockers.includes('KILL_SWITCH_ACTIVE');
  const allowed = !stopped && !blockers.includes('LOCAL_LIVE_CONFIRMATION_MISSING');
  const unverified = blockers.includes('PROVIDER_CONNECTION_UNVERIFIED');
  const ready = status.ready === true && !stopped && blockers.length === 0;
  return { ready, allowed, stopped,
    label: stopped ? '已停用' : ready ? '已验证可用' : !status.apiKeyPresent ? '缺少密钥' : unverified ? '待验证' : '等待授权',
    permission: stopped ? '○ 云调用：已主动停用，需重启服务后重新授权' : unverified ? '○ 云调用：需先完成真实连接验证' : allowed ? '✓ 云调用：本次启动已允许' : '○ 云调用：尚未允许（配置密钥时完成）' };
}

export function visibleProviderMode(catalog, status, preferredMode, explicitPreference = false) {
  const preferred = catalog.find(item => item.mode === preferredMode);
  if (explicitPreference && preferred) return preferred.mode;
  const preferredReady = preferred && providerConnection(status?.[preferred.key]).ready;
  if (preferredReady) return preferred.mode;
  const firstReady = catalog.find(item => providerConnection(status?.[item.key]).ready);
  return firstReady?.mode || preferred?.mode || catalog[0]?.mode;
}
