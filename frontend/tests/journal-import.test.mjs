import assert from "node:assert/strict";
import { readFile, mkdtemp, unlink, rmdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL, fileURLToPath } from "node:url";
import { File } from "node:buffer";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const dataUrl = text => `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
const compiled = await mkdtemp(join(tmpdir(), "past-journal-tests-"));
let apiCode, uiCode;
try {
  execFileSync(process.execPath, [fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "bundler",
    "--jsx", "react-jsx", "--skipLibCheck", "--outDir", compiled,
    fileURLToPath(new URL("../src/components/JournalImport.tsx", import.meta.url))]);
  apiCode = await readFile(join(compiled, "api/journalImport.js"), "utf8");
  uiCode = await readFile(join(compiled, "components/JournalImport.js"), "utf8");
} finally {
  for (const file of ["api/journalImport.js", "components/JournalImport.js"]) await unlink(join(compiled, file)).catch(() => {});
  for (const directory of ["api", "components"]) await rmdir(join(compiled, directory)).catch(() => {});
  await rmdir(compiled);
}
const apiUrl = dataUrl(apiCode);
const api = await import(apiUrl);
const componentCode = uiCode
  .replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))
  .replaceAll('"react"', JSON.stringify(pathToFileURL(require.resolve("react")).href))
  .replaceAll('"../api/journalImport"', JSON.stringify(apiUrl));
const { ImportPreview, default: JournalImport } = await import(dataUrl(componentCode));
const result = { detected_encoding: "cp932", column_count: 45, uploaded_count: 2, new_count: 1,
  duplicate_count: 1, preview_rows: [{ 伝票日付: "20250101", 摘要: "過去仕訳" }], errors: [], imported_count: 0 };
const file = new File(["opaque CSV bytes"], "past.csv");

test("Preview posts original CSV as multipart and preserves backend result", async t => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/journal/import/preview");
    assert.equal(options.method, "POST");
    assert.equal(options.headers, undefined);
    assert.deepEqual([...options.body.keys()], ["file"]);
    assert.equal(await options.body.get("file").text(), "opaque CSV bytes");
    return Response.json(result);
  });
  assert.deepEqual(await api.importJournalCsv(file, "preview"), result);
});

test("Execute resends the same CSV without trusted Preview rows", async t => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    requests.push([url, [...options.body.keys()], await options.body.get("file").text()]);
    return Response.json({ ...result, imported_count: url.endsWith("execute") ? 1 : 0 });
  });
  await api.importJournalCsv(file, "preview");
  assert.equal((await api.importJournalCsv(file, "execute")).imported_count, 1);
  assert.deepEqual(requests, [
    ["/api/journal/import/preview", ["file"], "opaque CSV bytes"],
    ["/api/journal/import/execute", ["file"], "opaque CSV bytes"],
  ]);
});

test("React Preview renders encoding, counts and legacy columns", () => {
  const html = renderToStaticMarkup(React.createElement(ImportPreview, { result, busy: false, onExecute() {} }));
  for (const text of ["cp932", "45", "新規件数", "重複件数", "借方科目名", "貸方科目名", "借方金額", "過去仕訳", "検索DBへ追加"]) assert.ok(html.includes(text));
  assert.ok(!html.includes("disabled"));
});

test("React Execute button disabled for zero new rows, errors or in-flight request", () => {
  for (const props of [
    { result: { ...result, new_count: 0 }, busy: false },
    { result: { ...result, errors: ["45列CSVではありません"] }, busy: false },
    { result, busy: true },
  ]) {
    assert.match(renderToStaticMarkup(React.createElement(ImportPreview, { ...props, onExecute() {} })), /<button[^>]*disabled/);
  }
});

test("React data management starts with CSV selector and no Execute", () => {
  const html = renderToStaticMarkup(React.createElement(JournalImport));
  assert.ok(html.includes("データ管理"));
  assert.ok(html.includes("EPSON仕訳帳CSV取込"));
  assert.match(html, /type="file"/);
  assert.ok(!html.includes("検索DBへ追加"));
});

test("Server internals never displayed by API error handling", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response("secret/path traceback", { status: 500 }));
  await assert.rejects(api.importJournalCsv(file, "execute"), error => !error.message.includes("secret"));
});
