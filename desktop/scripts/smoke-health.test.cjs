const test = require("node:test");
const assert = require("node:assert/strict");
const { waitForHealth } = require("./smoke-health.cjs");
const healthy = { ok: true, json: async () => ({ ok: true, service: "cureflow-local" }) };

function clock(fetchImpl) {
  let elapsed = 0;
  return { fetchImpl, timeoutMs: 10, intervalMs: 1, now: () => elapsed, sleep: async n => { elapsed += n; } };
}

test("smoke waits through backend startup and malformed responses", async () => {
  let calls = 0;
  await waitForHealth("http://local", { exitCode: null }, clock(async () => {
    calls++;
    if (calls === 1) throw new Error("ECONNREFUSED");
    if (calls === 2) return { ok: true, json: async () => { throw new Error("not JSON"); } };
    return healthy;
  }));
  assert.equal(calls, 3);
});

test("smoke rejects an unrelated service instead of false passing", async () => {
  await assert.rejects(waitForHealth("http://local", { exitCode: null }, clock(async () => ({ ok: true, json: async () => ({ ok: true, service: "other" }) }))), /未在/);
});

test("smoke stops on early Electron exit without polling the port", async () => {
  await assert.rejects(waitForHealth("http://local", { exitCode: 1 }, clock(async () => { assert.fail("must not fetch"); })), /提前退出/);
});
