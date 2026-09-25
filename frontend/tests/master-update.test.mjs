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
const compiled = await mkdtemp(join(tmpdir(), "master-update-tests-"));
let apiCode, uiCode;
try {
  execFileSync(process.execPath, [fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "bundler",
    "--jsx", "react-jsx", "--skipLibCheck", "--outDir", compiled,
    fileURLToPath(new URL("../src/components/MasterManagement.tsx", import.meta.url))]);
  apiCode = await readFile(join(compiled, "api/masterUpdate.js"), "utf8");
  uiCode = await readFile(join(compiled, "components/MasterManagement.js"), "utf8");
} finally {
  for (const file of ["api/masterUpdate.js", "components/MasterManagement.js"]) await unlink(join(compiled, file)).catch(() => {});
  for (const directory of ["api", "components"]) await rmdir(join(compiled, directory)).catch(() => {});
  await rmdir(compiled);
}
const apiUrl = dataUrl(apiCode);
const api = await import(apiUrl);
const componentCode = uiCode
  .replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))
  .replaceAll('"react"', JSON.stringify(pathToFileURL(require.resolve("react")).href))
  .replaceAll('"../api/masterUpdate"', JSON.stringify(apiUrl));
const { MasterDiff, MasterUpdateButtons, default: MasterManagement } = await import(dataUrl(componentCode));
const result = { kind: "account", current_count: 1, found_count: 2, added_count: 1,
  unchanged_count: 1, conflict_count: 0, added_items: [{ code: "504", name: "法定福利費" }], conflicts: [], applied: false };

test("Preview posts kind only and displays backend diff", async t => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/journal/masters/update-preview");
    assert.deepEqual(JSON.parse(options.body), { kind: "account" });
    return Response.json(result);
  });
  assert.deepEqual(await api.updateMasters("account"), result);
  const html = renderToStaticMarkup(React.createElement(MasterDiff, { result, busy: false, onExecute() {} }));
  for (const label of ["現在", "抽出", "追加", "登録済み", "競合", "504", "法定福利費", "確定更新"]) assert.ok(html.includes(label));
  assert.ok(!html.includes("disabled"));
});

test("Execute does not send trusted Preview items", async t => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/journal/masters/update");
    assert.deepEqual(JSON.parse(options.body), { kind: "sub" });
    return Response.json({ ...result, kind: "sub", applied: true });
  });
  assert.equal((await api.updateMasters("sub", true)).applied, true);
});

test("Execute disabled for conflicts, zero additions, applied result or busy", () => {
  for (const change of [{ conflict_count: 1 }, { added_count: 0 }, { applied: true }]) {
    const html = renderToStaticMarkup(React.createElement(MasterDiff, { result: { ...result, ...change }, busy: false, onExecute() {} }));
    assert.match(html, /<button[^>]*disabled/);
  }
  assert.match(renderToStaticMarkup(React.createElement(MasterDiff, { result, busy: true, onExecute() {} })), /<button[^>]*disabled/);
});

test("Supplementary diff shows sub and relation counts and parent codes", () => {
  const html = renderToStaticMarkup(React.createElement(MasterDiff, { result: {
    ...result, kind: "sub", sub_added_count: 1, relation_added_count: 2,
    relation_added_items: [{ account_code: "114", sub_code: "1", sub_name: "銀行" }],
  }, busy: false, onExecute() {} }));
  for (const label of ["補助追加", "親子関係追加", "親科目", "114", "銀行"]) assert.ok(html.includes(label));
});

test("Manual account form has all notices, categories and payment checkbox", () => {
  const html = renderToStaticMarkup(React.createElement(MasterManagement, { onUpdated: async () => {} }));
  for (const text of ["EPSON側に登録済みの科目だけを追加してください", "journal-aiで追加してもEPSON側には登録されません",
    "分類は検索・確認・AIチェック用の補助情報です", "科目コード", "科目名", "資産", "負債", "純資産", "収益", "費用", "入金科目候補にも追加する"]) assert.ok(html.includes(text));
  assert.match(html, /type="checkbox"/);
});

test("Manual account API sends explicit fields without extra master data", async t => {
  const input = { code: "504", name: "法定福利費", category: "費用", add_to_payment: false };
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/journal/masters/accounts/add");
    assert.deepEqual(JSON.parse(options.body), input);
    return Response.json({ account_added: true, payment_added: false, message: "追加しました" });
  });
  assert.equal((await api.addAccount({ ...input, existing_rows: [] })).account_added, true);
});

test("Ambiguous payment message is safe and surfaced", async t => {
  const message = "同じ名称に複数の科目コードがあるため入金科目候補へ追加できません。";
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: message }, { status: 422 }));
  await assert.rejects(api.addAccount({ code: "504", name: "法定福利費", category: "費用", add_to_payment: true }), { message });
});

test("Unexpected server exception and paths are not displayed", async t => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "secret/path traceback" }, { status: 500 }));
  await assert.rejects(api.updateMasters("account", true), error => !error.message.includes("secret"));
});

for (const [kind, label] of [["account", "科目"], ["department", "部門"], ["sub", "補助"]]) {
  test(`${kind} button sends exactly one target and renders only its Preview`, async t => {
    const calls = [];
    t.mock.method(globalThis, "fetch", async (url, options) => {
      calls.push([url, JSON.parse(options.body)]);
      return Response.json({ ...result, kind });
    });
    let pending;
    const buttons = MasterUpdateButtons({ pendingMasterTarget: null, busy: false,
      onSelect: target => { pending = api.updateMasters(target); } }).props.children;
    buttons.find(button => button.key === kind).props.onClick();
    const response = await pending;
    assert.deepEqual(calls, [["/api/journal/masters/update-preview", { kind }]]);
    const html = renderToStaticMarkup(React.createElement(MasterDiff, { result: response, busy: false, onExecute() {} }));
    assert.ok(html.includes(`<h4>${label}マスター差分</h4>`));
    assert.equal((html.match(/<h4>/g) || []).length, 1);
  });

  test(`${kind} alone shows pending; other targets suppress requests without pressed styling`, () => {
    const calls = [];
    const buttons = MasterUpdateButtons({ pendingMasterTarget: kind, busy: true,
      onSelect: target => calls.push(target) }).props.children;
    for (const button of buttons) {
      const active = button.key === kind;
      assert.equal(button.props.disabled, active);
      assert.equal(button.props["aria-busy"], active);
      assert.equal(button.props["aria-disabled"], true);
      assert.equal(Boolean(button.props.className), active);
      assert.equal(button.props["aria-pressed"], undefined);
      assert.equal(renderToStaticMarkup(button).includes("処理中"), active);
      button.props.onClick();
    }
    assert.deepEqual(calls, []);
  });
}
