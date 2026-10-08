import test from "node:test";
import assert from "node:assert/strict";
import { captureReviewProgress, restoreReviewProgress } from "../src/review-progress.mjs";

const id = "fix_0123456789abcdef";
const result = { runId: "run-1", input: { inputFingerprint: "input-1" } };

test("review progress round trips only on exact run and input", () => {
  const progress = captureReviewProgress(result, [id, id], [], { [id]: "核实后的值" });
  assert.deepEqual(progress.selectedFixIds, [id]);
  assert.equal(progress.manualCorrections[id], "核实后的值");
  assert.deepEqual(restoreReviewProgress({ result, reviewProgress: progress }), progress);
  assert.deepEqual(restoreReviewProgress({ result: { ...result, runId: "run-2" }, reviewProgress: progress }),
    { selectedFixIds: [], reviewedValidIds: [], manualCorrections: {} });
});

test("review progress drops foreign issue identifiers", () => {
  const progress = captureReviewProgress(result, [id, "other"], ["other"], { other: "x" });
  assert.deepEqual(progress.selectedFixIds, [id]);
  assert.deepEqual(progress.reviewedValidIds, []);
  assert.deepEqual(progress.manualCorrections, {});
});
