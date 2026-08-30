"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function response(status, body, etag) {
  return {
    headers: { get: (name) => name === "ETag" ? etag || null : null },
    json: async () => body,
    ok: status >= 200 && status < 300,
    status,
  };
}

function listElement() {
  return {
    children: [],
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
  };
}

async function run() {
  const listeners = {};
  const nextActions = listElement();
  const dashboardState = { hidden: false, textContent: "" };
  const dashboardContent = { hidden: true };
  const elements = new Map([
    ["missing-workspace", { hidden: true }],
    ["global-status", { textContent: "" }],
    ["refresh-status", { textContent: "" }],
    ["refresh-button", { disabled: false }],
    ["codex-status", { textContent: "" }],
    ["current-work", { textContent: "" }],
    ["project-name", { textContent: "" }],
    ["project-phase", { textContent: "" }],
    ["latest-summary", { textContent: "" }],
    ["error-summary", { textContent: "" }],
    ["git-summary", { textContent: "" }],
    ["dashboard-generated-at", { textContent: "" }],
    ["next-actions", nextActions],
  ]);
  const appShell = { dataset: {} };
  const requests = [];
  const responses = [
    response(200, {
      codex: { current_work: "作業ステータス表示を実装する", status: "working" },
      data_type: "dashboard",
      errors: { critical: 1, open: 2 },
      generated_at: "2026-08-30T12:00:00+09:00",
      git: { branch: "main", changed_files: 3 },
      latest: { summary: "ダッシュボード表示を追加" },
      next_actions: [
        { text: "次の作業を確認する" },
        { text: "最近の更新一覧を実装する" },
      ],
      project: { name: "Codex Mobile Dashboard", phase: "Phase 5" },
    }, '"dashboard-v1"'),
    response(200, { data_type: "metadata" }, '"metadata-v1"'),
    response(200, { status: "ok" }, '"health-v1"'),
  ];
  const context = {
    URLSearchParams,
    console,
    document: {
      addEventListener: (name, listener) => { listeners[name] = listener; },
      createElement: () => ({ textContent: "" }),
      getElementById: (id) => elements.get(id) || null,
      querySelector: (selector) => {
        if (selector === ".app-shell") {
          return appShell;
        }
        if (selector === '[data-state-for="dashboard"]') {
          return dashboardState;
        }
        if (selector === '[data-content-for="dashboard"]') {
          return dashboardContent;
        }
        return null;
      },
    },
    window: {
      fetch: async (url, options) => {
        requests.push({ options, url });
        return responses.shift();
      },
      location: { search: "?workspace_id=workspace-1" },
    },
  };
  vm.runInNewContext(fs.readFileSync("client/app.js", "utf8"), context, { filename: "app.js" });
  listeners.DOMContentLoaded();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const client = context.window.CodexMobileDashboard;
  assert.strictEqual(client.getWorkspaceId(), "workspace-1");
  assert.strictEqual(appShell.dataset.appState, "ready");
  assert.strictEqual(client.getDocument("dashboard").data_type, "dashboard");
  assert.strictEqual(elements.get("project-name").textContent, "Codex Mobile Dashboard");
  assert.strictEqual(elements.get("codex-status").textContent, "Codex: 作業中");
  assert.strictEqual(elements.get("current-work").textContent, "作業ステータス表示を実装する");
  assert.strictEqual(elements.get("project-phase").textContent, "Phase: Phase 5");
  assert.strictEqual(elements.get("latest-summary").textContent, "ダッシュボード表示を追加");
  assert.strictEqual(elements.get("error-summary").textContent, "未解決 2件 / 重大 1件");
  assert.strictEqual(elements.get("git-summary").textContent, "main / 変更ファイル 3件");
  assert.strictEqual(elements.get("dashboard-generated-at").textContent, "2026-08-30T12:00:00+09:00");
  assert.strictEqual(elements.get("refresh-status").textContent, "最終更新: 2026-08-30T12:00:00+09:00");
  assert.strictEqual(nextActions.children[0].textContent, "次の作業を確認する");
  assert.strictEqual(nextActions.children[1].textContent, "最近の更新一覧を実装する");
  assert.strictEqual(dashboardState.hidden, true);
  assert.strictEqual(dashboardContent.hidden, false);
  assert.deepStrictEqual(requests.map((request) => request.url), [
    "/data/workspace-1/dashboard.json",
    "/data/workspace-1/metadata.json",
    "/health?workspace_id=workspace-1",
  ]);

  responses.push(response(304, null, null));
  const dashboard = await client.fetchDocument("dashboard");
  assert.strictEqual(dashboard.data_type, "dashboard");
  assert.strictEqual(requests[3].options.headers["If-None-Match"], '"dashboard-v1"');

  responses.push(response(200, [], '"recent-v1"'));
  await assert.rejects(() => client.fetchDocument("recent"), /json_shape_invalid/);
  assert.strictEqual(client.getDocumentError("recent"), "json_shape_invalid");
  console.log("app dashboard tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
