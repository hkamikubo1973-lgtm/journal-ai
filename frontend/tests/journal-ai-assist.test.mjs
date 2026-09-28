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
const compiled = await mkdtemp(join(tmpdir(), "journal-ai-assist-tests-"));
let apiCode, controllerCode, componentCode;
try {
  execFileSync(process.execPath, [fileURLToPath(new URL("../node_modules/typescript/bin/tsc", import.meta.url)),
    "--ignoreConfig", "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "bundler",
    "--jsx", "react-jsx", "--skipLibCheck", "--outDir", compiled,
    fileURLToPath(new URL("../src/api/journalAiAssist.ts", import.meta.url)),
    fileURLToPath(new URL("../src/journalAiAssistController.ts", import.meta.url)),
    fileURLToPath(new URL("../src/components/JournalAiAssist.tsx", import.meta.url))]);
  apiCode = await readFile(join(compiled, "api/journalAiAssist.js"), "utf8");
  controllerCode = await readFile(join(compiled, "journalAiAssistController.js"), "utf8");
  componentCode = await readFile(join(compiled, "components/JournalAiAssist.js"), "utf8");
} finally {
  if (!resolve(compiled).startsWith(resolve(tmpdir()) + sep)) throw new Error("unsafe temp cleanup path");
  await rm(compiled, { recursive: true, force: true });
}
const api = await import(dataUrl(apiCode));
const controllerUrl = dataUrl(controllerCode.replaceAll('"./api/journalAiAssist"', JSON.stringify(dataUrl(apiCode))));
const state = await import(controllerUrl);
const ui = await import(dataUrl(componentCode
  .replaceAll('"../journalAiAssistController"', JSON.stringify(controllerUrl))
  .replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve("react/jsx-runtime")).href))
  .replaceAll('"react"', JSON.stringify(pathToFileURL(require.resolve("react")).href))));

const request = { keyword: "会議費", department: null, amount: null, limit: 5 };
const queued = { job_id: "job-1", state: "QUEUED" };
const running = { job_id: "job-1", state: "RUNNING" };
const completed = { job_id: "job-1", state: "COMPLETED", content: "候補1\n理由：会議費\n確認：内容" };
const tick = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

function fakeTimer() {
  const tasks = new Map();
  let nextId = 1;
  return {
    tasks,
    set(callback, delay) { const id = nextId++; tasks.set(id, { callback, delay }); return id; },
    clear(id) { tasks.delete(id); },
    async run() {
      assert.equal(tasks.size, 1);
      const [id, task] = tasks.entries().next().value;
      tasks.delete(id);
      task.callback();
      await tick();
    },
  };
}

function controller({ submit = async () => queued, get = async () => completed } = {}) {
  const timer = fakeTimer();
  const changes = [];
  const instance = new state.JournalAiAssistController((view) => changes.push(view), submit, get, timer);
  return { instance, timer, changes };
}

const render = (view = state.initialJournalAiAssistView, available = true) => renderToStaticMarkup(
  React.createElement(ui.JournalAiAssistView, { view, available, onOpen: () => {}, onClose: () => {} }));

test("AI button is disabled without a current candidate", () => {
  assert.match(render(state.initialJournalAiAssistView, false), /AI補助<\/button>/);
  assert.match(render(state.initialJournalAiAssistView, false), /disabled=""/);
  assert.doesNotMatch(render(), /disabled=""/);
});

test("AI is available only for the search conditions that produced the visible candidates", () => {
  assert.equal(state.matchesJournalAiSearch(request, request), true);
  assert.equal(state.matchesJournalAiSearch(request, null), false);
  for (const patch of [{ keyword: "旅費" }, { department: "営業" }, { amount: 2000 }, { limit: 10 }]) {
    assert.equal(state.matchesJournalAiSearch({ ...request, ...patch }, request), false);
  }
});

test("POST sends only current search conditions and receives job ID", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return Response.json(queued);
  });
  assert.deepEqual(await api.submitJournalAiAssist(request), queued);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/journal/ai-assist");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), request);
  for (const forbidden of ["profile", "execution_mode", "model", "max_tokens", "prompt", "candidates"])
    assert.equal(forbidden in JSON.parse(calls[0].options.body), false);
});

test("GET uses returned job ID and rejects a different ID", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => { calls.push(url); return Response.json(completed); });
  assert.deepEqual(await api.getJournalAiAssist("job-1"), completed);
  assert.deepEqual(calls, ["/api/journal/ai-assist/job-1"]);
  await assert.rejects(api.getJournalAiAssist("other"), /取得できません/);
});

for (const [status, message] of [[503, "接続できません"], [504, "タイムアウト"], [502, "取得できません"], [500, "取得できません"]]) {
  test(`HTTP ${status} shows only a safe message`, async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "C:/private/trace" }, { status }));
    await assert.rejects(api.getJournalAiAssist("job-1"), (error) =>
      error.message.includes(message) && !error.message.includes("private"));
  });
}

test("network and malformed response errors never reveal raw bodies", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new Error("C:/private/host"); });
  await assert.rejects(api.submitJournalAiAssist(request), (error) =>
    error.message.includes("接続できません") && !error.message.includes("private"));
  t.mock.restoreAll();
  t.mock.method(globalThis, "fetch", async () => Response.json({ job_id: "job-1", state: "UNKNOWN", secret: "private" }));
  await assert.rejects(api.getJournalAiAssist("job-1"), (error) =>
    error.message.includes("取得できません") && !error.message.includes("private"));
});

test("double click during POST creates only one Job", async () => {
  let finish;
  let posts = 0;
  const { instance } = controller({ submit: () => { posts += 1; return new Promise((resolve) => { finish = resolve; }); } });
  const first = instance.open(request);
  const second = instance.open(request);
  assert.equal(posts, 1);
  assert.equal(instance.snapshot().submitting, true);
  finish(queued);
  await Promise.all([first, second]);
  assert.equal(instance.snapshot().jobId, "job-1");
  assert.equal(instance.snapshot().state, "QUEUED");
});

test("POST failure is shown safely and never retried automatically", async () => {
  let posts = 0;
  const { instance, timer } = controller({ submit: async () => {
    posts += 1;
    throw new api.JournalAiAssistApiError("AI補助の応答がタイムアウトしました。");
  } });
  await instance.open(request);
  assert.equal(posts, 1);
  assert.match(instance.snapshot().error, /タイムアウト/);
  assert.equal(timer.tasks.size, 0);
  await instance.open(request);
  assert.equal(posts, 1);
});

test("polling follows QUEUED and RUNNING, then stops at COMPLETED", async () => {
  const responses = [running, completed];
  const { instance, timer } = controller({ get: async () => responses.shift() });
  await instance.open(request);
  assert.equal(timer.tasks.size, 1);
  assert.equal([...timer.tasks.values()][0].delay, 1500);
  await timer.run();
  assert.equal(instance.snapshot().state, "RUNNING");
  assert.equal(timer.tasks.size, 1);
  await timer.run();
  assert.equal(instance.snapshot().state, "COMPLETED");
  assert.equal(instance.snapshot().content, completed.content);
  assert.equal(timer.tasks.size, 0);
});

test("FAILED and GET failure stop polling with safe errors", async () => {
  const failed = controller({ get: async () => ({ job_id: "job-1", state: "FAILED" }) });
  await failed.instance.open(request);
  await failed.timer.run();
  assert.equal(failed.instance.snapshot().state, "FAILED");
  assert.match(failed.instance.snapshot().error, /失敗/);
  assert.equal(failed.timer.tasks.size, 0);
  const timeout = controller({ get: async () => { throw new api.JournalAiAssistApiError("AI補助の応答がタイムアウトしました。"); } });
  await timeout.instance.open(request);
  await timeout.timer.run();
  assert.match(timeout.instance.snapshot().error, /タイムアウト/);
  assert.equal(timeout.timer.tasks.size, 0);
});

test("closing stops polling and reopening the same Job never repeats POST", async () => {
  let posts = 0;
  let gets = 0;
  const { instance, timer } = controller({
    submit: async () => { posts += 1; return queued; },
    get: async () => { gets += 1; return completed; },
  });
  await instance.open(request);
  instance.close();
  assert.equal(timer.tasks.size, 0);
  await instance.open(request);
  assert.equal(posts, 1);
  assert.equal([...timer.tasks.values()][0].delay, 0);
  await timer.run();
  assert.equal(gets, 1);
  assert.equal(instance.snapshot().content, completed.content);
});

test("dispose on search change or tab exit ignores a late POST", async () => {
  let finish;
  const { instance, timer, changes } = controller({ submit: () => new Promise((resolve) => { finish = resolve; }) });
  const pending = instance.open(request);
  const before = changes.length;
  instance.dispose();
  finish(queued);
  await pending;
  assert.equal(changes.length, before);
  assert.equal(timer.tasks.size, 0);
});

test("closing or unmounting ignores an old in-flight GET", async () => {
  let finish;
  const { instance, timer } = controller({ get: () => new Promise((resolve) => { finish = resolve; }) });
  await instance.open(request);
  await timer.run();
  instance.close();
  finish(completed);
  await tick();
  assert.equal(instance.snapshot().content, null);
  instance.dispose();
});

test("panel displays status, safe plain text, and a close label without touching candidates", () => {
  const queuedHtml = render({ ...state.initialJournalAiAssistView, open: true, jobId: "job-1", state: "QUEUED" });
  assert.match(queuedHtml, /AI補助を準備しています/);
  assert.match(queuedHtml, /aria-label="AI補助を閉じる"/);
  assert.match(queuedHtml, /disabled=""/);
  const runningHtml = render({ ...state.initialJournalAiAssistView, open: true, jobId: "job-1", state: "RUNNING" });
  assert.match(runningHtml, /AIが候補を整理しています/);
  const content = "候補1 <script>alert(1)</script>\n最終判断は人";
  const doneHtml = render({ ...state.initialJournalAiAssistView, open: true, jobId: "job-1", state: "COMPLETED", content });
  assert.match(doneHtml, /候補1 &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(doneHtml, /<script>/);
  assert.match(doneHtml, /最終判断は人/);
});
