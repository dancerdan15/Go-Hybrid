#!/usr/bin/env node
/**
 * W-025 (#695): Automated Cross-Runtime Verification Runner (`verify_hy_mir.mjs`)
 * Executes the 10-case pre-registered benchmark matrix (5 Positive Controls +
 * 5 Negative Soundness Falsifiers), measures per-function MIR lowering + checking
 * latency, and verifies the 1:1 Go emission correspondence invariant.
 */

import { spawnSync } from "node:child_process";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const hyMirPy = path.join(__dirname, "hy_mir.py");
const testHyMirPy = path.join(__dirname, "test_hy_mir.py");

const t0 = performance.now();
const runMain = spawnSync("python", [hyMirPy], { encoding: "utf-8" });
const t1 = performance.now();

if (runMain.status !== 0) {
  console.error(runMain.stderr || runMain.stdout);
  process.exit(1);
}

const lines = runMain.stdout
  .trim()
  .split(/\r?\n/)
  .filter((l) => l.includes("[PASS]"));

assert.equal(lines.length, 10, `Expected 10 passing benchmark cases, got ${lines.length}`);

const runUnit = spawnSync("python", [testHyMirPy], { encoding: "utf-8" });
if (runUnit.status !== 0) {
  console.error(runUnit.stderr || runUnit.stdout);
  process.exit(1);
}

const totalMs = t1 - t0;
const perFnMs = totalMs / 10.0;

console.log("=== W-025 (#695) `hy-mir` Verification Summary ===");
for (const line of lines) {
  console.log(line);
}
console.log(
  `Verified 10/10 cases (5 Positive Controls + 5 Negative Falsifiers) | Total: ${totalMs.toFixed(2)}ms (${perFnMs.toFixed(2)}ms/case)`
);
