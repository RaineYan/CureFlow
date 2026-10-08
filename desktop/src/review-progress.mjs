const ISSUE_ID = /^fix_[0-9a-f]{16}$/;

export function captureReviewProgress(result, selectedFixIds, reviewedValidIds, manualCorrections) {
  if (!result?.runId || !result.input?.inputFingerprint) return null;
  const ids = values => [...new Set(values.filter(value => typeof value === "string" && ISSUE_ID.test(value)))];
  const corrections = Object.fromEntries(Object.entries(manualCorrections || {}).filter(([id, value]) =>
    ISSUE_ID.test(id) && typeof value === "string" && value.length <= 5000));
  return {
    runId: result.runId,
    inputFingerprint: result.input.inputFingerprint,
    selectedFixIds: ids(selectedFixIds || []),
    reviewedValidIds: ids(reviewedValidIds || []),
    manualCorrections: corrections,
  };
}

export function restoreReviewProgress(entry) {
  const empty = { selectedFixIds: [], reviewedValidIds: [], manualCorrections: {} };
  const progress = entry?.reviewProgress;
  if (!progress || progress.runId !== entry.result?.runId
      || progress.inputFingerprint !== entry.result?.input?.inputFingerprint) return empty;
  return captureReviewProgress(entry.result, progress.selectedFixIds,
    progress.reviewedValidIds, progress.manualCorrections) || empty;
}
