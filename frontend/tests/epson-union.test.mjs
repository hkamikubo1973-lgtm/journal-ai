import assert from "node:assert/strict";
import { readFile, mkdtemp, unlink, rmdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
const compiled = await mkdtemp(join(tmpdir(), "journal-epson-tests-"));
let outputText;
try {
  execFileSync(process.execPath, [
    fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--skipLibCheck",
    "--outDir", compiled, fileURLToPath(new URL("../src/api/journal.ts", import.meta.url)),
  ]);
  outputText = await readFile(join(compiled, "api/journal.js"), "utf8");
} finally {
  for (const directory of ["api", "types"]) {
    await unlink(join(compiled, directory, "journal.js")).catch(() => {});
    await rmdir(join(compiled, directory)).catch(() => {});
  }
  await rmdir(compiled);
}
const api = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const searched = {
  source_type: "searched_journal", registration_id: "existing-backend-id",
  prepared_journal: { amount: 500 }, epson_base_row: { 摘要: "searched" },
};
const receivable = {
  source_type: "receivable_settlement", prepared_journal: { amount: 1000 },
  provenance: {
    settlement_id: "settlement", receipt_ref: "a".repeat(64), row_index: 0,
    row_count: 1, settlement_row_id: "b".repeat(64),
  },
  epson_capability: { status: "needs_template" },
};
const searchedPayload = {
  registration_id: searched.registration_id,
  prepared_journal: searched.prepared_journal,
  epson_base_row: searched.epson_base_row,
};
const receivablePayload = {
  source_type: "receivable_settlement", provenance: receivable.provenance,
};

for (const [name, cart, expected] of [
  ["searched", [searched], [searchedPayload]],
  ["receivable", [receivable], [receivablePayload]],
  ["mixed", [receivable, searched, receivable], [receivablePayload, searchedPayload, receivablePayload]],
]) {
  for (const save of [false, true]) {
    test(`${name} ${save ? "save" : "download"}: one ordered request, unchanged cart and existing result UX`, async (t) => {
      const before = structuredClone(cart);
      const calls = [];
      const saveResult = {
        ok: true, csv_saved: true, db_registered: true, already_registered: false,
        partial_failure: false, filename: "epson_existing.csv", save_path: "configured destination",
        appended_count: cart.length, message: "EPSON CSVを保存しました。検索DBへ登録しました。",
      };
      t.mock.method(globalThis, "fetch", async (url, options) => {
        calls.push([url, options]);
        return save ? Response.json(saveResult) : new Response("csv-content", {
          headers: { "Content-Disposition": 'attachment; filename="epson_existing.csv"' },
        });
      });
      const request = api.buildEpsonExportRequest(cart);
      const result = await (save ? api.saveEpsonCsv : api.downloadEpsonCsv)(request);
      assert.equal(calls.length, 1);
      assert.equal(calls[0][0], `/api/journal/${save ? "save" : "export"}-epson-csv`);
      assert.equal(calls[0][1].method, "POST");
      assert.deepEqual(JSON.parse(calls[0][1].body), { items: expected });
      if (save) assert.deepEqual(result, saveResult);
      else {
        assert.equal(result.filename, "epson_existing.csv");
        assert.equal(await result.blob.text(), "csv-content");
      }
      assert.deepEqual(cart, before);
    });
  }
}

test("receivable allowlist excludes display fields, forged EPSON data and extra provenance fields", () => {
  const untrusted = {
    ...receivable, registration_id: "forged", epson_base_row: { tax: "forged" },
    epson_preview_row: {}, template: {}, tax: "forged", 形式: "forged", 資金区分: "forged",
    provenance: { ...receivable.provenance, private_path: "private" },
  };
  assert.deepEqual(api.buildEpsonExportRequest([untrusted]), { items: [receivablePayload] });
  assert.equal(untrusted.epson_capability.status, "needs_template");
});

for (const [code, message] of Object.entries({
  template_not_found: "EPSON変換に使える過去仕訳が見つかりません",
  template_ambiguous: "過去仕訳に複数のEPSON設定があり自動決定できません",
  template_invalid: "EPSON変換用の過去仕訳を利用できません",
  receipt_invalid: "未収消込データを確認できません",
  provenance_mismatch: "未収消込データを確認できません",
  master_validation_failed: "現在のマスターと仕訳内容が一致しません",
  prepared_journal_invalid: "仕訳内容をEPSON出力用に準備できません",
})) {
  test(`${code}: safe user message in download and save`, async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({
      detail: { code, message: "C:/private/internal exception", hash: "a".repeat(64) },
    }, { status: 422 }));
    for (const send of [api.downloadEpsonCsv, api.saveEpsonCsv]) {
      await assert.rejects(send(api.buildEpsonExportRequest([receivable, searched])), (error) => {
        assert.ok(error.message.endsWith(message));
        assert.doesNotMatch(error.message, /private|exception|aaaa/);
        return true;
      });
    }
  });
}

test("unknown, malformed, validation-array and internal responses never expose raw details", async (t) => {
  for (const body of [
    "<html>internal exception C:/private</html>",
    JSON.stringify({ detail: { code: "unknown", message: "private" } }),
    JSON.stringify({ detail: [{ input: receivable.provenance }] }),
    JSON.stringify({ detail: "内部例外 C:/private aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
  ]) {
    t.mock.method(globalThis, "fetch", async () => new Response(body, { status: 500 }));
    for (const cart of [[searched], [receivable], [searched, receivable]]) {
      for (const send of [api.downloadEpsonCsv, api.saveEpsonCsv]) {
        await assert.rejects(send(api.buildEpsonExportRequest(cart)), (error) => {
          assert.doesNotMatch(error.message, /private|exception|aaaa|html|settlement/);
          return true;
        });
      }
    }
  }
});

test("searched registration-integrity message remains visible", async (t) => {
  const message = "1件目のregistration_idが内容と一致しません。";
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: message }, { status: 422 }));
  await assert.rejects(api.downloadEpsonCsv(api.buildEpsonExportRequest([searched])), { message: `EPSON CSVダウンロードエラー: ${message}` });
});

test("B2-K string receipt/readiness errors use safe status messages", async (t) => {
  for (const status of [404, 409, 503, 423]) {
    t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "private" }, { status }));
    await assert.rejects(api.saveEpsonCsv(api.buildEpsonExportRequest([receivable])), (error) => {
      assert.doesNotMatch(error.message, /private/);
      assert.match(error.message, status === 423 ? /ほかの処理が使用中/ : /未収消込データを確認できません/);
      return true;
    });
  }
});
