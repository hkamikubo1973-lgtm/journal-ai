import assert from "node:assert/strict";
import { readFile, mkdtemp, unlink, rmdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL, fileURLToPath } from "node:url";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const dataUrl = text => `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
const compiled = await mkdtemp(join(tmpdir(), "output-settings-tests-"));
let apiCode, uiCode;
try {
  execFileSync(process.execPath, [fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "bundler",
    "--jsx", "react-jsx", "--skipLibCheck", "--outDir", compiled,
    fileURLToPath(new URL("../src/components/OutputSettings.tsx", import.meta.url))]);
  apiCode = await readFile(join(compiled, "api/outputSettings.js"), "utf8");
  uiCode = await readFile(join(compiled, "components/OutputSettings.js"), "utf8");
} finally {
  for (const file of ["api/outputSettings.js", "components/OutputSettings.js"]) await unlink(join(compiled, file)).catch(() => {});
  for (const directory of ["api", "components"]) await rmdir(join(compiled, directory)).catch(() => {});
  await rmdir(compiled);
}
const apiUrl = dataUrl(apiCode);
const api = await import(apiUrl);
const ui = uiCode
  .replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))
  .replaceAll('"react"', JSON.stringify(pathToFileURL(require.resolve("react")).href))
  .replaceAll('"../api/outputSettings"', JSON.stringify(apiUrl));
const { OutputSettingsForm } = await import(dataUrl(ui));

test("current configured folder is fetched from the settings API", async t => {
  const folder = String.raw`C:\仕訳出力`;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/settings/output");
    assert.equal(options, undefined);
    return Response.json({ csv_export_dir: folder });
  });
  assert.deepEqual(await api.getOutputSettings(), { csv_export_dir: folder });
  const html = renderToStaticMarkup(React.createElement(OutputSettingsForm, {
    value: folder, onChange() {}, onSave() {}, busy: false, error: "", message: "",
  }));
  assert.ok(html.includes(`value="${folder}"`));
  assert.ok(html.includes("設定を保存"));
  assert.ok(html.includes("ブラウザのダウンロード先とは別"));
});

test("save sends only trimmed path and accepts UNC folder", async t => {
  const folder = String.raw`\\192.168.0.210\共有\仕訳システム`;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/settings/output");
    assert.equal(options.method, "PUT");
    assert.deepEqual(JSON.parse(options.body), { csv_export_dir: folder });
    return Response.json({ csv_export_dir: folder });
  });
  assert.deepEqual(await api.saveOutputSettings(`  ${folder}  `), { csv_export_dir: folder });
});

test("save failure displays a safe message without internal paths", async t => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "C:/private/config/settings.json" }, { status: 500 }));
  await assert.rejects(api.saveOutputSettings("C:/output"), error => !error.message.includes("private"));
  const html = renderToStaticMarkup(React.createElement(OutputSettingsForm, {
    value: "C:/output", onChange() {}, onSave() {}, busy: false,
    error: "保存先フォルダを保存できませんでした。", message: "",
  }));
  assert.match(html, /role="alert"/);
  assert.ok(html.includes("保存先フォルダを保存できませんでした"));
});

test("empty path and in-flight save disable the action", () => {
  for (const [value, busy] of [["   ", false], ["C:/output", true]]) {
    const html = renderToStaticMarkup(React.createElement(OutputSettingsForm, {
      value, onChange() {}, onSave() {}, busy, error: "", message: "",
    }));
    assert.match(html, /<button[^>]*disabled/);
  }
});
