import assert from "node:assert/strict";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL, fileURLToPath } from "node:url";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const dataUrl = (text) => `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
const compiled = await mkdtemp(join(tmpdir(), "receivable-cleanup-tests-"));
let apiCode, uiCode;
try {
  execFileSync(process.execPath, [fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "bundler",
    "--jsx", "react-jsx", "--skipLibCheck", "--outDir", compiled,
    fileURLToPath(new URL("../src/api/receivable.ts", import.meta.url)),
    fileURLToPath(new URL("../src/components/receivable/ReceivableCleanup.tsx", import.meta.url))]);
  apiCode = await readFile(join(compiled, "api/receivable.js"), "utf8");
  uiCode = await readFile(join(compiled, "components/receivable/ReceivableCleanup.js"), "utf8");
} finally {
  if (!resolve(compiled).startsWith(resolve(tmpdir()) + sep)) throw new Error("unsafe temp cleanup path");
  await rm(compiled, { recursive: true, force: true });
}
const api = await import(dataUrl(apiCode));
const ui = uiCode.replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href));
const { default: ReceivableCleanup, selectedCustomerAfterRefresh } = await import(dataUrl(ui));

const baseProps = {
  summary: { current_count: 4, cleanup_target_count: 2, remaining_count: 2 },
  loading: false, executing: false, disabled: false, error: null, success: null, onExecute() {},
};

test("cleanup summary displays the target count and scope", () => {
  const html = renderToStaticMarkup(React.createElement(ReceivableCleanup, baseProps));
  assert.match(html, /未収台帳の整理/);
  assert.match(html, /完了済みまたは残高0の未収が 2件/);
  assert.match(html, /消込履歴と検索DBは削除されません/);
});

test("zero target and in-flight cleanup disable the action", () => {
  for (const change of [{ summary: { ...baseProps.summary, cleanup_target_count: 0 } }, { executing: true }]) {
    const html = renderToStaticMarkup(React.createElement(ReceivableCleanup, { ...baseProps, ...change }));
    assert.match(html, /<button[^>]*disabled/);
  }
});

test("cleanup request has no client-supplied rows or counts and reload APIs remain available", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return Response.json(baseProps.summary);
  });
  await api.fetchReceivableCleanupSummary();
  await api.executeReceivableCleanup();
  await api.fetchReceivableSummary();
  await api.fetchReceivableDetail("A社");
  assert.deepEqual(calls.map(({ url }) => url), [
    "/api/receivables/cleanup-summary", "/api/receivables/cleanup",
    "/api/receivables/summary", "/api/receivables/customers/detail?customer_name=A%E7%A4%BE",
  ]);
  assert.equal(calls[0].options, undefined);
  assert.deepEqual(calls[1].options, { method: "POST" });
});

test("removed selected customer is cleared after refresh", () => {
  const summary = { customers: [{ customer_name: "B社" }] };
  assert.equal(selectedCustomerAfterRefresh("A社", summary), null);
  assert.equal(selectedCustomerAfterRefresh("B社", summary), "B社");
});

test("success and failure messages are safe", async (t) => {
  const html = renderToStaticMarkup(React.createElement(ReceivableCleanup, {
    ...baseProps, success: "未収台帳を整理しました（2件）", error: "未収台帳を確認できませんでした。",
  }));
  assert.match(html, /未収台帳を整理しました（2件）/);
  assert.match(html, /role="alert"/);
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "C:/private/current.csv PRIVATE_HASH" }, { status: 500 }));
  await assert.rejects(api.executeReceivableCleanup(), (error) =>
    !error.message.includes("private") && !error.message.includes("PRIVATE_HASH"));
});
