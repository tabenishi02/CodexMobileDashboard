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
  const recentTurns = listElement();
  const dashboardState = { hidden: false, textContent: "" };
  const dashboardContent = { hidden: true };
  const recentState = { hidden: false, textContent: "" };
  const recentContent = { hidden: true };
  const chatState = { hidden: false, textContent: "" };
  const chatContent = { hidden: true };
  const errorsState = { hidden: false, textContent: "" };
  const errorsContent = { hidden: true };
  const decisionsState = { hidden: false, textContent: "" };
  const decisionsContent = { hidden: true };
  const systemState = { hidden: false, textContent: "" };
  const systemContent = { hidden: true };
  const chatMessages = listElement();
  const errorList = listElement();
  const decisionList = listElement();
  const collectorStatus = listElement();
  const serverStatus = listElement();
  const refreshButton = {
    disabled: false,
    listener: null,
    addEventListener(_name, listener) { this.listener = listener; },
  };
  const screens = [
    { dataset: { screen: "dashboard" }, hidden: false },
    { dataset: { screen: "recent" }, hidden: true },
    { dataset: { screen: "chat" }, hidden: true },
    { dataset: { screen: "errors" }, hidden: true },
    { dataset: { screen: "decisions" }, hidden: true },
    { dataset: { screen: "system" }, hidden: true },
  ];
  function screenLink(screenLink) {
    return {
      attributes: {},
      dataset: { screenLink },
      listener: null,
      addEventListener(_name, listener) { this.listener = listener; },
      removeAttribute(name) { delete this.attributes[name]; },
      setAttribute(name, value) { this.attributes[name] = value; },
    };
  }
  const screenLinks = [screenLink("dashboard"), screenLink("recent"), screenLink("chat"), screenLink("errors"), screenLink("decisions"), screenLink("system")];
  const elements = new Map([
    ["missing-workspace", { hidden: true }],
    ["global-status", { textContent: "" }],
    ["refresh-status", { textContent: "" }],
    ["refresh-button", refreshButton],
    ["codex-status", { textContent: "" }],
    ["current-work", { textContent: "" }],
    ["project-name", { textContent: "" }],
    ["project-phase", { textContent: "" }],
    ["latest-summary", { textContent: "" }],
    ["error-summary", { textContent: "" }],
    ["git-summary", { textContent: "" }],
    ["dashboard-generated-at", { textContent: "" }],
    ["next-actions", nextActions],
    ["recent-turns", recentTurns],
    ["chat-messages", chatMessages],
    ["load-previous-messages", { hidden: true, addEventListener() {} }],
    ["error-list", errorList],
    ["decision-list", decisionList],
    ["collector-status", collectorStatus],
    ["server-status", serverStatus],
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
    response(200, {
      collector: {
        codex_status: "idle",
        last_checked_at: "2026-08-30T12:01:00+09:00",
        last_data_change_at: "2026-08-30T12:00:30+09:00",
        last_send_succeeded_at: null,
        status: "ok",
      },
      data_type: "metadata",
    }, '"metadata-v1"'),
    response(200, {
      current_snapshot_id: "snapshot-1",
      last_received_at: "2026-08-30T03:01:30+00:00",
      logging_available: true,
      public_available: true,
      server_version: "CodexMobileDashboard/0.1",
      staging_available: false,
      status: "ok",
      storage_available: true,
      uptime_seconds: 321,
    }, '"health-v1"'),
  ];
  const context = {
    URLSearchParams,
    console,
    document: {
      addEventListener: (name, listener) => { listeners[name] = listener; },
      createElement: () => ({
        children: [],
        textContent: "",
        appendChild(child) { this.children.push(child); },
        querySelectorAll() { return []; },
      }),
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
        if (selector === '[data-state-for="recent"]') {
          return recentState;
        }
        if (selector === '[data-content-for="recent"]') {
          return recentContent;
        }
        if (selector === '[data-state-for="chat"]') { return chatState; }
        if (selector === '[data-content-for="chat"]') { return chatContent; }
        if (selector === '[data-state-for="errors"]') { return errorsState; }
        if (selector === '[data-content-for="errors"]') { return errorsContent; }
        if (selector === '[data-state-for="decisions"]') { return decisionsState; }
        if (selector === '[data-content-for="decisions"]') { return decisionsContent; }
        if (selector === '[data-state-for="system"]') {
          return systemState;
        }
        if (selector === '[data-content-for="system"]') {
          return systemContent;
        }
        return null;
      },
      querySelectorAll: (selector) => {
        if (selector === "[data-screen]") { return screens; }
        if (selector === "[data-screen-link]") { return screenLinks; }
        return [];
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
  const client = context.window.CodexMobileDashboard;
  client.showScreen("system");
  assert.strictEqual(systemState.hidden, false);
  assert.strictEqual(systemState.textContent, "システム情報を読み込んでいます。");
  assert.strictEqual(systemContent.hidden, true);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.strictEqual(client.getWorkspaceId(), "workspace-1");
  assert.strictEqual(appShell.dataset.appState, "ready");
  assert.strictEqual(client.getDocument("dashboard").data_type, "dashboard");
  assert.strictEqual(client.getDocument("dashboard").generated_at, "2026-08-30T12:00:00+09:00");
  assert.strictEqual(elements.get("project-name").textContent, "Codex Mobile Dashboard");
  assert.strictEqual(elements.get("codex-status").textContent, "Codex: 作業中");
  assert.strictEqual(elements.get("current-work").textContent, "作業ステータス表示を実装する");
  assert.strictEqual(elements.get("project-phase").textContent, "Phase: Phase 5");
  client.getDocument("dashboard").project.phase = null;
  client.renderDashboard();
  assert.strictEqual(elements.get("project-phase").textContent, "Phase: 情報なし");
  assert.strictEqual(elements.get("latest-summary").textContent, "ダッシュボード表示を追加");
  client.getDocument("dashboard").latest.summary = "   ";
  client.renderDashboard();
  assert.strictEqual(elements.get("latest-summary").textContent, "データがありません（変更要約）。");
  assert.strictEqual(elements.get("error-summary").textContent, "未解決 2件 / 重大 1件");
  assert.strictEqual(elements.get("git-summary").textContent, "main / 変更ファイル 3件");
  assert.strictEqual(elements.get("dashboard-generated-at").textContent, "2026/08/30 12:00:00 JST");
  assert.strictEqual(elements.get("refresh-status").textContent, "最終更新: 2026/08/30 12:00:00 JST / データが古い可能性があります");
  assert.strictEqual(nextActions.children[0].textContent, "次の作業を確認する");
  assert.strictEqual(nextActions.children[1].textContent, "最近の更新一覧を実装する");
  client.getDocument("dashboard").next_actions = [];
  client.renderDashboard();
  assert.strictEqual(nextActions.children.length, 1);
  assert.strictEqual(nextActions.children[0].textContent, "データがありません（次の作業）。");
  assert.strictEqual(dashboardState.hidden, true);
  assert.strictEqual(dashboardContent.hidden, false);
  assert.strictEqual(systemState.hidden, true);
  assert.strictEqual(systemContent.hidden, false);
  assert.deepStrictEqual(requests.map((request) => request.url), [
    "/data/workspace-1/dashboard.json",
    "/data/workspace-1/metadata.json",
    "/health?workspace_id=workspace-1",
  ]);

  responses.push(response(200, {
    current_turn: {
      assistant_preview: null,
      completed_at: null,
      rolled_back: false,
      started_at: "2026-08-30T12:10:00+09:00",
      status: "in_progress",
      user_preview: "最近の更新一覧を実装する",
    },
    data_type: "recent",
    turns: [{
      assistant_preview: "一覧表示を実装しました。",
      completed_at: "2026-08-30T12:05:00+09:00",
      rolled_back: true,
      started_at: "2026-08-30T12:00:00+09:00",
      status: "completed",
      user_preview: "前の作業を実装する",
    }],
  }, '"recent-v1"'));
  client.showScreen("recent");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(screens[0].hidden, true);
  assert.strictEqual(screens[1].hidden, false);
  assert.strictEqual(screenLinks[1].attributes["aria-current"], "page");
  assert.match(recentTurns.children[0].textContent, /状態: 作業中/);
  assert.match(recentTurns.children[0].textContent, /開始: 2026\/08\/30 12:10:00 JST/);
  assert.match(recentTurns.children[0].textContent, /ユーザー: 最近の更新一覧を実装する/);
  assert.match(recentTurns.children[1].textContent, /状態: 完了/);
  assert.match(recentTurns.children[1].textContent, /完了: 2026\/08\/30 12:05:00 JST/);
  assert.match(recentTurns.children[1].textContent, /ロールバック: あり/);

  responses.push(
    response(200, {
      data_type: "messages",
      pages: [{ path: "messages/pages/page-000001.json" }],
    }, '"messages-v1"'),
    response(200, {
      messages: [{
        content: { text: "詳細メッセージ" },
        created_at: "2026-08-30T03:02:00+00:00",
        role: "assistant",
      }],
    }),
  );
  client.showScreen("chat");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(chatState.hidden, true);
  assert.strictEqual(chatContent.hidden, false);
  assert.match(chatMessages.children[0].children[0].textContent, /日時: 2026\/08\/30 12:02:00 JST/);

  responses.push(response(200, {
    data_type: "errors",
    errors: [{
      last_occurred_at: "2026-08-30T03:03:00+00:00",
      occurrence_count: 1,
      severity: "warning",
      status: "open",
      summary: "テストエラー",
    }],
  }, '"errors-v1"'));
  client.showScreen("errors");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(errorList.children[0].textContent, /発生: 2026\/08\/30 12:03:00 JST/);

  responses.push(response(200, {
    data_type: "decisions",
    decisions: [{
      decided_at: "2026-08-30T03:04:00+00:00",
      description: "表示時だけJSTへ変換する。",
      status: "adopted",
      title: "日時表示方針",
    }],
  }, '"decisions-v1"'));
  client.showScreen("decisions");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(decisionList.children[0].textContent, /決定: 2026\/08\/30 12:04:00 JST/);

  const requestCountBeforeSystem = requests.length;
  client.showScreen("system");
  assert.strictEqual(requests.length, requestCountBeforeSystem);
  assert.strictEqual(screens[1].hidden, true);
  assert.strictEqual(screens[5].hidden, false);
  assert.strictEqual(screenLinks[5].attributes["aria-current"], "page");
  assert.strictEqual(systemState.hidden, true);
  assert.strictEqual(systemContent.hidden, false);
  assert.deepStrictEqual(collectorStatus.children.map((child) => child.textContent), [
    "収集状態", "正常",
    "Codex状態", "待機中",
    "最終確認日時", "2026/08/30 12:01:00 JST",
    "最終データ変更日時", "2026/08/30 12:00:30 JST",
    "前回送信成功日時", "日時未記録",
  ]);
  assert.deepStrictEqual(serverStatus.children.map((child) => child.textContent), [
    "サーバー状態", "正常",
    "サーバーバージョン", "CodexMobileDashboard/0.1",
    "稼働秒数", "321秒",
    "公開領域", "利用可能",
    "受信領域", "利用不可",
    "ログ", "利用可能",
    "保存容量", "利用可能",
    "公開中Snapshot ID", "snapshot-1",
    "最終受信日時", "2026/08/30 12:01:30 JST",
  ]);

  systemState.hidden = false;
  systemState.textContent = "システム情報を読み込んでいます。";
  systemContent.hidden = true;
  responses.push(
    response(304, null, null),
    response(304, null, null),
    response(304, null, null),
  );
  refreshButton.listener();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(systemState.hidden, true);
  assert.strictEqual(systemContent.hidden, false);
  assert.strictEqual(collectorStatus.children[1].textContent, "正常");
  assert.strictEqual(serverStatus.children[1].textContent, "正常");

  responses.push(response(304, null, null));
  const dashboard = await client.fetchDocument("dashboard");
  assert.strictEqual(dashboard.data_type, "dashboard");
  assert.strictEqual(requests.find((request) => request.url === "/data/workspace-1/dashboard.json" && request.options.headers["If-None-Match"]).options.headers["If-None-Match"], '"dashboard-v1"');

  responses.push(response(200, [], '"recent-v1"'));
  await assert.rejects(() => client.fetchDocument("recent"), /json_shape_invalid/);
  assert.strictEqual(client.getDocumentError("recent"), "json_shape_invalid");
  console.log("app dashboard tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
