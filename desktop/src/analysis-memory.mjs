const DEFAULT_LIMIT = 12;

export const emptyAnalysisConfig = () => ({measureColumn:"",groupColumn:"",identifierColumn:"",timeColumn:""});

export function analysisSourceKey(inspection, filename) {
  if (!inspection?.inputFingerprint) return null;
  return JSON.stringify([inspection.inputFingerprint, inspection.selectedSheet || "", filename || ""]);
}

export function analysisConfigKey(sourceKey, goal) {
  return sourceKey ? JSON.stringify([sourceKey, goal]) : null;
}

export function analysisResultKey(sourceKey, goal, config, mode) {
  return sourceKey ? JSON.stringify([sourceKey, goal, config.measureColumn || "", config.groupColumn || "", config.identifierColumn || "", config.timeColumn || "", mode]) : null;
}

export function rememberAnalysis(cache, key, entry, limit = DEFAULT_LIMIT) {
  if (!key) return;
  cache.delete(key);
  cache.set(key, entry);
  while (cache.size > limit) cache.delete(cache.keys().next().value);
}

export function recallAnalysis(cache, sourceKey, goal, config, preferredMode) {
  for (const mode of [preferredMode, "offline"]) {
    const key = analysisResultKey(sourceKey, goal, config, mode);
    if (cache.has(key)) return cache.get(key);
  }
  return null;
}

export function rememberedForSource(cache, sourceKey) {
  return [...cache.values()].filter(entry => entry.sourceKey === sourceKey).reverse();
}
