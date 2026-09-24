import assert from "node:assert/strict";
import { readFile, mkdtemp, unlink, rmdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { File } from "node:buffer";
import test from "node:test";

const compiled = await mkdtemp(join(tmpdir(), "journal-import-tests-"));
let outputText;
try {
  execFileSync(process.execPath, [
    fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--skipLibCheck",
    "--outDir", compiled, fileURLToPath(new URL("../src/api/receivable.ts", import.meta.url)),
  ]);
  outputText = await readFile(join(compiled, "api/receivable.js"), "utf8");
} finally {
  for (const file of ["api/receivable.js", "types/receivable.js", "types/journal.js"]) {
    await unlink(join(compiled, file)).catch(() => {});
  }
  for (const directory of ["api", "types"]) await rmdir(join(compiled, directory)).catch(() => {});
  await rmdir(compiled);
}
const api = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const input = {
  file: new File(["opaque workbook bytes"], "billing.xlsx"), invoice_date: "2026-01-15",
  default_account: "未収運賃", department: "営業",
};

test("automatic preview request sends opaque file and conditions only", async (t) => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/receivables/import/preview");
    assert.equal(options.method, "POST");
    assert.equal(options.headers, undefined); // browser supplies multipart boundary
    assert.deepEqual([...options.body.keys()], ["file", "invoice_date", "default_account", "department"]);
    assert.equal(await options.body.get("file").text(), "opaque workbook bytes");
    assert.equal(options.body.get("invoice_date"), "2026-01-15");
    return Response.json({ sheet_name: "プリント用", importable_count: 1 });
  });
  assert.equal((await api.previewReceivableImport({ ...input, normalized_rows: [{ amount: 999 }] })).importable_count, 1);
});

test("execute resends same file and explicit due date without preview rows", async (t) => {
  const bodies = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    bodies.push(options.body);
    return Response.json(url.endsWith("execute") ? { imported_count: 1, message: "done" } : { importable_count: 1 });
  });
  const request = { ...input, payment_due_date: "2026-03-01" };
  await api.previewReceivableImport(request);
  const result = await api.executeReceivableImport(request);
  assert.equal(result.imported_count, 1);
  assert.equal(bodies.length, 2);
  for (const body of bodies) {
    assert.equal(await body.get("file").text(), "opaque workbook bytes");
    assert.equal(body.get("payment_due_date"), "2026-03-01");
    assert.equal(body.has("valid_rows"), false);
  }
});

test("partial import/exclusion results are preserved for UI display", async (t) => {
  const expected = { sheet_name: "x", importable_count: 1, excluded_count: 2, duplicate_count: 1,
    valid_rows: [{ 請求金額: "1200" }], exclusions: [{ source_row: 3, source_row_label: "Excel行", reason: "繰越しが0以下です" }] };
  t.mock.method(globalThis, "fetch", async () => Response.json(expected));
  assert.deepEqual(await api.previewReceivableImport(input), expected);
});

test("import errors reach UI without treating them as success", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "見出し行にコード・得意先名１・繰越しがありません" }, { status: 422 }));
  await assert.rejects(api.previewReceivableImport(input), /見出し行/);
  await assert.rejects(api.executeReceivableImport(input), /見出し行/);
});

test("malformed server errors do not show raw response", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response("PRIVATE_PATH", { status: 500 }));
  await assert.rejects(api.executeReceivableImport(input), (error) => !error.message.includes("PRIVATE_PATH"));
});

test("existing summary/detail reload APIs remain callable after import", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return Response.json({ imported_count: 1 });
  });
  await api.executeReceivableImport(input);
  await api.fetchReceivableSummary();
  await api.fetchReceivableDetail("A社");
  assert.deepEqual(calls, ["/api/receivables/import/execute", "/api/receivables/summary", "/api/receivables/customers/detail?customer_name=A%E7%A4%BE"]);
});

test("existing settlement calls keep JSON payload and routes", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push(url);
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(options.body), { marker: "unchanged" });
    return Response.json({});
  });
  await api.previewReceivableSettlement({ marker: "unchanged" });
  await api.executeReceivableSettlement({ marker: "unchanged" });
  assert.deepEqual(calls, ["/api/receivables/preview-settlement", "/api/receivables/execute-settlement"]);
});
