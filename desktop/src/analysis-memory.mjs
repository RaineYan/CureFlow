const DEFAULT_LIMIT = 12;

export const emptyAnalysisConfig = () => ({measureColumn:"",groupColumn:"",identifierColumn:"",timeColumn:"",analysisBrief:""});

export const analysisFieldRoles = config => ({
  measureColumn: config?.measureColumn || "",
  groupColumn: config?.groupColumn || "",
  identifierColumn: config?.identifierColumn || "",
  timeColumn: config?.timeColumn || "",
});

export function analysisSourceKey(inspection, filename) {
  if (!inspection?.inputFingerprint) return null;
  return JSON.stringify([inspection.inputFingerprint, inspection.selectedSheet || "", filename || ""]);
}

export function analysisConfigKey(sourceKey, goal) {
  return sourceKey ? JSON.stringify([sourceKey, goal]) : null;
}

export function analysisResultKey(sourceKey, goal, config, mode) {
  return sourceKey ? JSON.stringify([sourceKey, goal, config.measureColumn || "", config.groupColumn || "", config.identifierColumn || "", config.timeColumn || "", config.analysisBrief || "", mode]) : null;
}

export function rememberAnalysis(cache, key, entry, limit = DEFAULT_LIMIT) {
  if (!key) return;
  const previous=cache.get(key);
  cache.delete(key);
  cache.set(key, previous?{...previous,...entry,title:entry.title??previous.title}:entry);
  while (cache.size > limit) cache.delete(cache.keys().next().value);
}

export function hasCurrentGroupChartData(entry) {
  const tool=entry?.result?.analysisTools?.executedTools?.find(item=>item.key==="group_comparison");
  const comparisons=tool?.payload?.comparisons||[];
  return comparisons.every(item=>Array.isArray(item.groupStats)&&item.groupStats.length===item.groupCount);
}

export function recallAnalysis(cache, sourceKey, goal, config, preferredMode) {
  for (const mode of [preferredMode, "offline"]) {
    const key = analysisResultKey(sourceKey, goal, config, mode);
    if (cache.has(key) && hasCurrentGroupChartData(cache.get(key))) return cache.get(key);
  }
  return null;
}

export function rememberedForSource(cache, sourceKey) {
  return [...cache.values()].filter(entry => entry.sourceKey === sourceKey && hasCurrentGroupChartData(entry)).reverse();
}

export function analysisHistoryTitle(entry, goals) {
  if (typeof entry.title === "string" && entry.title.trim()) return entry.title.trim();
  const filename=entry.result?.input?.filename || "未命名文件";
  const sheet=entry.result?.input?.sheet || "CSV 主表";
  const goal=goals.find(item=>item.key===entry.goal)?.label || entry.goal;
  const date=entry.createdAt ? new Date(entry.createdAt) : null;
  const time=date && !Number.isNaN(date.getTime()) ? ` · ${date.toLocaleString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})}` : "";
  return `${filename} · ${sheet} · ${goal}${time}`;
}
