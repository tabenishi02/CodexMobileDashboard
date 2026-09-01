"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function response(status, body) {
  return {
    headers: { get: () => null },
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

function definitionValues(element) {
  const values = {};
  for (let index = 0; index < element.children.length; index += 2) {
    values[element.children[index].textContent] = element.children[index + 1].textContent;
  }
  return values;
}

async function loadSystem(healthResponse) {
  const listeners = {};
  const dashboardState = { hidden: false, textContent: "" };
  const dashboardContent = { hidden: true };
  const systemState = { hidden: false, textContent: "" };
  const systemContent = { hidden: true };
  const collectorStatus = listElement();
  const serverStatus = listElement();
  const nextActions = listElement();
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
    ["collector-status", collectorStatus],
    ["server-status", serverStatus],
  ]);
  const responses = [
    response(200, {
      codex: { status: "idle" },
      data_type: "dashboard",
      errors: { critical: 0, open: 0 },
      generated_at: "2026-08-30T12:00:00+09:00",
      git: { branch: "main", changed_files: 0 },
      latest: {},
      next_actions: [],
      project: { name: "Project" },
    }),
    response(200, {
      collector: {
        codex_status: "idle",
        last_checked_at: "2026-08-30T12:00:00+09:00",
        last_data_change_at: null,
        last_send_succeeded_at: null,
        status: "ok",
      },
      data_type: "metadata",
    }),
    healthResponse,
  ];
  const context = {
    URLSearchParams,
    console,
    document: {
      addEventListener: (name, listener) => { listeners[name] = listener; },
      createElement: () => ({ textContent: "" }),
      getElementById: (id) => elements.get(id) || null,
      querySelector: (selector) => {
        if (selector === ".app-shell") { return { dataset: {} }; }
        if (selector === '[data-state-for="dashboard"]') { return dashboardState; }
        if (selector === '[data-content-for="dashboard"]') { return dashboardContent; }
        if (selector === '[data-state-for="system"]') { return systemState; }
        if (selector === '[data-content-for="system"]') { return systemContent; }
        return null;
      },
      querySelectorAll: () => [],
    },
    window: {
      fetch: async () => responses.shift(),
      location: { search: "?workspace_id=workspace-1" },
    },
  };

  vm.runInNewContext(fs.readFileSync("client/app.js", "utf8"), context, { filename: "app.js" });
  listeners.DOMContentLoaded();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  context.window.CodexMobileDashboard.showScreen("system");
  return { collectorStatus, serverStatus, systemContent, systemState };
}

function health(currentSnapshotId, lastReceivedAt) {
  return response(200, {
    current_snapshot_id: currentSnapshotId,
    last_received_at: lastReceivedAt,
    logging_available: true,
    public_available: true,
    server_version: "CodexMobileDashboard/0.1",
    staging_available: true,
    status: "ok",
    storage_available: true,
    uptime_seconds: 60,
  });
}

async function run() {
  const published = await loadSystem(health("snapshot-1", "2026-08-30T03:01:30+00:00"));
  const publishedValues = definitionValues(published.serverStatus);
  assert.strictEqual(publishedValues["公開中Snapshot ID"], "snapshot-1");
  assert.strictEqual(publishedValues["最終受信日時"], "2026/08/30 12:01:30 JST");
  assert.strictEqual(published.systemState.hidden, true);
  assert.strictEqual(published.systemContent.hidden, false);

  const nextDay = await loadSystem(health("snapshot-next-day", "2026-08-30T15:01:30+00:00"));
  const nextDayValues = definitionValues(nextDay.serverStatus);
  assert.strictEqual(nextDayValues["公開中Snapshot ID"], "snapshot-next-day");
  assert.strictEqual(nextDayValues["最終受信日時"], "2026/08/31 00:01:30 JST");

  const unpublished = await loadSystem(health(null, null));
  const unpublishedValues = definitionValues(unpublished.serverStatus);
  assert.strictEqual(unpublishedValues["公開中Snapshot ID"], "公開済みデータなし");
  assert.strictEqual(unpublishedValues["最終受信日時"], "日時未記録");
  assert.strictEqual(unpublished.systemState.hidden, true);
  assert.strictEqual(unpublished.systemContent.hidden, false);

  const failed = await loadSystem(response(500, null));
  assert.strictEqual(failed.systemState.hidden, false);
  assert.strictEqual(failed.systemContent.hidden, true);
  assert.strictEqual(
    failed.systemState.textContent,
    "Androidサーバー情報を取得できませんでした（通信失敗または不正な応答）。",
  );
  assert.deepStrictEqual(failed.serverStatus.children, []);
  console.log("system status tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
