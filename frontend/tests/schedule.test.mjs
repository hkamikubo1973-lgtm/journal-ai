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
const dataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const compiled = await mkdtemp(join(tmpdir(), "schedule-tests-"));
let apiCode, uiCode, tabsCode;
try {
  execFileSync(process.execPath, [fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "bundler",
    "--jsx", "react-jsx", "--skipLibCheck", "--outDir", compiled,
    fileURLToPath(new URL("../src/api/events.ts", import.meta.url)),
    fileURLToPath(new URL("../src/components/ScheduleWorkspace.tsx", import.meta.url)),
    fileURLToPath(new URL("../src/components/WorkspaceTabs.tsx", import.meta.url))]);
  apiCode = await readFile(join(compiled, "api/events.js"), "utf8");
  uiCode = await readFile(join(compiled, "components/ScheduleWorkspace.js"), "utf8");
  tabsCode = await readFile(join(compiled, "components/WorkspaceTabs.js"), "utf8");
} finally {
  if (!resolve(compiled).startsWith(resolve(tmpdir()) + sep)) throw new Error("unsafe temp cleanup path");
  await rm(compiled, { recursive: true, force: true });
}
const api = await import(dataUrl(apiCode));
const ui = await import(dataUrl(uiCode
  .replaceAll('"../api/events"', JSON.stringify(dataUrl(apiCode)))
  .replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))
  .replaceAll('"react"', JSON.stringify(pathToFileURL(require.resolve("react")).href))));
const { default: WorkspaceTabs } = await import(dataUrl(tabsCode.replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))));

const snapshot = {
  month: "", day: "31", title: "税務申告", memo: "提出書類", notify_days: "5",
  cycle: "monthly", status: "pending", type: "tax", stop: "False", last_executed: "",
};
const event = {
  ...snapshot, index: 2, expected_event: snapshot, effective_status: "pending",
  stopped: false, next_date: "2026-10-31", days_remaining: 3, notification_target: true,
};
const data = { events: [event], notification_events: [event], notification_count: 1 };
const input = { title: "税務申告", cycle: "monthly", month: "", day: "31", notify_days: "5", type: "tax", memo: "提出書類" };
const noop = () => {};
const panel = (change = {}) => renderToStaticMarkup(React.createElement(ui.SchedulePanel, {
  data, pending: null, onAction: noop, onEdit: noop, onConfirm: noop, onNew: noop, ...change,
}));
const editor = (eventValue) => renderToStaticMarkup(React.createElement(ui.ScheduleEditorDialog, {
  event: eventValue, pending: false, onClose: noop, onSave: noop,
}));
const mockFetch = (t, status = 200) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return Response.json(data, { status });
  });
  return calls;
};

test("three workspace tabs are visible", () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active: "journal", onChange: noop }));
  for (const label of ["通常仕訳", "未収消込", "スケジュール"]) assert.match(html, new RegExp(label));
  assert.equal((html.match(/role="tab"/g) ?? []).length, 3);
});
test("schedule selection marks only third tab active", () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active: "schedule", onChange: noop }));
  assert.equal((html.match(/aria-selected="true"/g) ?? []).length, 1);
  assert.match(html, /aria-selected="true"[^>]*>スケジュール/);
});
test("third tab switches to schedule", () => {
  const selected = [];
  const tabs = WorkspaceTabs({ active: "journal", onChange: (workspace) => selected.push(workspace) });
  tabs.props.children[2].props.onClick();
  assert.deepEqual(selected, ["schedule"]);
});
test("zero notifications omit the schedule badge", () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active: "journal", onChange: noop, notificationCount: 0 }));
  assert.doesNotMatch(html, /schedule-tab-badge/);
});
test("one notification displays a small badge", () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active: "journal", onChange: noop, notificationCount: 1 }));
  assert.match(html, /class="schedule-tab-badge"[^>]*>1<\/span>/);
});
test("multiple notifications display backend count", () => {
  const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active: "journal", onChange: noop, notificationCount: 12 }));
  assert.match(html, /通知12件/);
  assert.match(html, />12<\/span>/);
});
for (const active of ["journal", "receivable", "schedule"]) {
  test(`badge is visible while ${active} is active`, () => {
    const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active, onChange: noop, notificationCount: 3 }));
    assert.match(html, /class="schedule-tab-badge"[^>]*>3<\/span>/);
    assert.equal((html.match(/aria-selected="true"/g) ?? []).length, 1);
  });
}
test("new GET result updates badge after a mutation", async (t) => {
  let count = 1;
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    if (options?.method === "POST") {
      count = 4;
      return Response.json({ event: snapshot });
    }
    return Response.json({ ...data, notification_count: count });
  });
  let feed = { data: null, loading: true, error: null };
  const load = api.createScheduleFeed((patch) => { feed = { ...feed, ...patch }; });
  await load();
  const before = feed.data;
  await api.resolveScheduleOperation(() => api.actOnEvent(event, "complete"));
  await load(true);
  const after = feed.data;
  const renderBadge = (response) => renderToStaticMarkup(React.createElement(WorkspaceTabs, {
    active: "schedule", onChange: noop, notificationCount: response.notification_count,
  }));
  assert.match(renderBadge(before), />1<\/span>/);
  assert.match(renderBadge(after), />4<\/span>/);
});
test("initial load and immediate tab switch share one GET", async () => {
  let finish;
  let calls = 0;
  const load = api.createScheduleFeed(() => {}, () => {
    calls += 1;
    return new Promise((resolve) => { finish = resolve; });
  });
  const initial = load();
  const switched = load();
  assert.equal(initial, switched);
  assert.equal(calls, 1);
  finish(data);
  await Promise.all([initial, switched]);
});
test("post-mutation forced GET wins over older in-flight response", async () => {
  const finishes = [];
  let feed = { data: null, loading: true, error: null };
  const load = api.createScheduleFeed((patch) => { feed = { ...feed, ...patch }; }, () =>
    new Promise((resolve) => { finishes.push(resolve); }));
  const older = load();
  const newer = load(true);
  finishes[1]({ ...data, notification_count: 4 });
  await newer;
  finishes[0]({ ...data, notification_count: 1 });
  await older;
  assert.equal(feed.data.notification_count, 4);
  assert.equal(feed.loading, false);
});
test("failed GET clears badge data with safe error", async () => {
  let feed = { data, loading: false, error: null };
  const load = api.createScheduleFeed((patch) => { feed = { ...feed, ...patch }; }, async () => {
    throw new api.EventsApiError(503, "イベント一覧を読み込めません。");
  });
  await assert.rejects(load());
  assert.equal(feed.data, null);
  assert.match(feed.error, /読み込めません/);
  assert.equal(feed.loading, false);
});
test("failed badge GET leaves journal tab available", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "private path" }, { status: 500 }));
  await assert.rejects(api.fetchEvents());
  const html = renderToStaticMarkup(React.createElement(WorkspaceTabs, { active: "journal", onChange: noop }));
  assert.match(html, /通常仕訳/);
  assert.doesNotMatch(html, /schedule-tab-badge/);
});
test("schedule workspace displays shared GET data and a safe load error", () => {
  const html = renderToStaticMarkup(React.createElement(ui.default, {
    data, loading: false, loadError: "イベント一覧を読み込めませんでした。", onRefresh: async () => data,
  }));
  assert.match(html, /通知対象 1件/);
  assert.match(html, /イベント一覧を読み込めませんでした/);
});

test("GET uses schedule endpoint", async (t) => {
  const calls = mockFetch(t);
  assert.deepEqual(await api.fetchEvents(), data);
  assert.deepEqual(calls, [{ url: "/api/events", options: undefined }]);
});
test("create uses POST with only form fields", async (t) => {
  const calls = mockFetch(t);
  await api.createEvent(input);
  assert.equal(calls[0].url, "/api/events");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), input);
});
test("edit sends index and full expected snapshot", async (t) => {
  const calls = mockFetch(t);
  await api.editEvent(event, { ...input, status: "done" });
  assert.equal(calls[0].url, "/api/events/2");
  assert.equal(calls[0].options.method, "PUT");
  assert.deepEqual(JSON.parse(calls[0].options.body), { ...input, status: "done", expected_event: snapshot });
});
for (const action of ["complete", "skip", "stop", "resume"]) {
  test(`${action} sends index and expected snapshot`, async (t) => {
    const calls = mockFetch(t);
    await api.actOnEvent(event, action);
    assert.equal(calls[0].url, `/api/events/2/${action}`);
    assert.equal(calls[0].options.method, "POST");
    assert.deepEqual(JSON.parse(calls[0].options.body), { expected_event: snapshot });
  });
}
test("delete sends index and expected snapshot", async (t) => {
  const calls = mockFetch(t);
  await api.deleteEvent(event);
  assert.equal(calls[0].url, "/api/events/2");
  assert.equal(calls[0].options.method, "DELETE");
  assert.deepEqual(JSON.parse(calls[0].options.body), { expected_event: snapshot });
});
for (const [status, expected] of [[409, "再読み込み"], [422, "入力内容"], [423, "使用中"], [503, "読み込めません"], [500, "処理に失敗"]]) {
  test(`HTTP ${status} error has safe text`, async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "C:/private/events.csv SECRET" }, { status }));
    await assert.rejects(api.fetchEvents(), (error) => error.status === status && error.message.includes(expected) && !error.message.includes("SECRET"));
  });
}
test("network error is safe", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new Error("C:/private/events.csv"); });
  await assert.rejects(api.fetchEvents(), (error) => error.status === 0 && !error.message.includes("private"));
});
test("successful mutation resolves once", async () => {
  let calls = 0;
  const result = await api.resolveScheduleOperation(async () => { calls += 1; });
  assert.equal(result, "success");
  assert.equal(calls, 1);
});
test("stale mutation is classified without retrying", async () => {
  let calls = 0;
  const result = await api.resolveScheduleOperation(async () => {
    calls += 1;
    throw new api.EventsApiError(409, "stale");
  });
  assert.equal(result, "conflict");
  assert.equal(calls, 1);
});
test("non-stale mutation error is preserved", async () => {
  await assert.rejects(api.resolveScheduleOperation(async () => {
    throw new api.EventsApiError(423, "busy");
  }), (error) => error.status === 423);
});
test("empty list has proper message and new button", () => {
  const html = panel({ data: { events: [], notification_events: [], notification_count: 0 } });
  assert.match(html, /登録されているスケジュールはありません/);
  assert.match(html, /＋ 新規登録/);
});
test("notification uses server count, date, days and memo", () => {
  const html = panel();
  assert.match(html, /通知対象 1件/);
  assert.match(html, /2026-10-31/);
  assert.match(html, /あと3日/);
  assert.match(html, /提出書類/);
});
test("same-day notification says 本日", () => {
  assert.equal(ui.daysLabel({ ...event, days_remaining: 0 }), "本日");
});
test("notification shows complete and skip actions", () => {
  assert.match(panel(), /完了/);
  assert.match(panel(), /スキップ/);
});
test("registered list shows edit, stop and delete", () => {
  const html = panel();
  assert.match(html, />編集</);
  assert.match(html, />停止</);
  assert.match(html, />削除</);
});
test("stopped event shows badge and resume", () => {
  const stopped = { ...event, stopped: true };
  const html = panel({ data: { events: [stopped], notification_events: [], notification_count: 0 } });
  assert.match(html, /停止中/);
  assert.match(html, />再開</);
});
test("only pending action displays processing text", () => {
  const html = panel({ pending: "list:2:stop" });
  assert.equal((html.match(/処理中\.\.\./g) ?? []).length, 1);
  assert.match(html, />編集</);
});
test("new modal has required fields and bounds", () => {
  const html = editor(null);
  for (const label of ["イベント名", "周期", "日", "通知日前", "種別", "備考"]) assert.match(html, new RegExp(label));
  assert.match(html, /max="31"/);
  assert.match(html, /max="365"/);
  assert.doesNotMatch(html, />状態</);
});
test("edit modal has status and original values", () => {
  const html = editor(event);
  assert.match(html, /イベントを編集/);
  assert.match(html, /value="税務申告"/);
  assert.match(html, /状態/);
});
test("yearly edit shows month", () => {
  const html = editor({ ...event, cycle: "yearly", month: "2" });
  assert.match(html, /月<input[^>]*max="12"/);
});
test("delete confirmation names event and requires explicit action", () => {
  const html = renderToStaticMarkup(React.createElement(ui.ScheduleConfirmDialog, {
    event, action: "delete", pending: false, onClose: noop, onConfirm: noop,
  }));
  assert.match(html, /role="alertdialog"/);
  assert.match(html, /税務申告/);
  assert.match(html, /削除する/);
  assert.match(html, /キャンセル/);
});
