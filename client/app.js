(() => {
  "use strict";

  const APP_STATE = {
    DEGRADED: "degraded",
    LOADING: "loading",
    MISSING_WORKSPACE: "missing-workspace",
    READY: "ready",
  };
  const DOCUMENT_PATHS = {
    dashboard: "dashboard.json",
    decisions: "decisions.json",
    errors: "errors.json",
    files: "files.json",
    messages: "messages.json",
    metadata: "metadata.json",
    recent: "recent.json",
  };
  const INITIAL_DOCUMENTS = ["dashboard", "metadata", "health"];
  const SCREEN_NAMES = new Set(["dashboard", "recent", "chat", "errors", "decisions", "files", "system", "more"]);
  const state = {
    cache: new Map(),
    documents: new Map(),
    errors: new Map(),
    workspaceId: null,
  };

  function createClientError(code) {
    const error = new Error(code);
    error.code = code;
    return error;
  }

  function getElement(id) {
    const element = document.getElementById(id);
    if (!element) {
      throw createClientError("required_element_missing");
    }
    return element;
  }

  function setText(id, value) {
    getElement(id).textContent = typeof value === "string" && value ? value : "—";
  }

  function readWorkspaceId() {
    const workspaceId = new URLSearchParams(window.location.search).get("workspace_id");
    return workspaceId && workspaceId.trim() ? workspaceId.trim() : null;
  }

  function documentUrl(documentName) {
    if (!state.workspaceId) {
      throw createClientError("workspace_id_missing");
    }
    if (documentName === "health") {
      return "/health?workspace_id=" + encodeURIComponent(state.workspaceId);
    }
    const relativePath = DOCUMENT_PATHS[documentName];
    if (!relativePath) {
      throw createClientError("document_name_invalid");
    }
    return "/data/" + encodeURIComponent(state.workspaceId) + "/" + relativePath;
  }

  async function fetchDocumentInternal(documentName) {
    const cached = state.cache.get(documentName);
    const headers = { Accept: "application/json" };
    if (cached && cached.etag) {
      headers["If-None-Match"] = cached.etag;
    }

    let response;
    try {
      response = await window.fetch(documentUrl(documentName), { headers });
    } catch (_error) {
      throw createClientError("network_error");
    }

    if (response.status === 304) {
      if (!cached) {
        throw createClientError("cache_missing_for_not_modified");
      }
      state.documents.set(documentName, cached.data);
      state.errors.delete(documentName);
      return cached.data;
    }
    if (!response.ok) {
      throw createClientError("http_error");
    }

    let data;
    try {
      data = await response.json();
    } catch (_error) {
      throw createClientError("json_invalid");
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw createClientError("json_shape_invalid");
    }

    state.cache.set(documentName, {
      data,
      etag: response.headers.get("ETag"),
    });
    state.documents.set(documentName, data);
    state.errors.delete(documentName);
    return data;
  }

  async function fetchDocument(documentName) {
    try {
      return await fetchDocumentInternal(documentName);
    } catch (error) {
      state.errors.set(documentName, error.code || "request_failed");
      throw error;
    }
  }

  async function fetchInitialDocuments() {
    const results = await Promise.allSettled(INITIAL_DOCUMENTS.map(fetchDocument));
    return results.map((result, index) => ({
      documentName: INITIAL_DOCUMENTS[index],
      result,
    }));
  }

  function getDocument(documentName) {
    return state.documents.get(documentName) || null;
  }

  function getDocumentError(documentName) {
    return state.errors.get(documentName) || null;
  }

  const CODEX_STATUS_LABELS = {
    idle: "待機中",
    stopped: "停止",
    unknown: "状態不明",
    working: "作業中",
  };

  function formatCodexStatus(codex) {
    if (!codex || typeof codex !== "object") {
      return CODEX_STATUS_LABELS.unknown;
    }
    return CODEX_STATUS_LABELS[codex.status] || CODEX_STATUS_LABELS.unknown;
  }

  function formatCurrentWork(codex) {
    if (!codex || typeof codex !== "object" || typeof codex.current_work !== "string" || !codex.current_work) {
      return "現在の作業はありません。";
    }
    return codex.current_work;
  }

  function formatTimestamp(value) {
    return typeof value === "string" && value ? value : "日時不明";
  }

  function formatLastUpdatedStatus() {
    const dashboard = getDocument("dashboard");
    return "最終更新: " + formatTimestamp(dashboard && dashboard.generated_at);
  }

  function formatGitSummary(git) {
    if (!git || typeof git !== "object") {
      return "Git情報を取得できませんでした。";
    }
    const branch = typeof git.branch === "string" && git.branch ? git.branch : "ブランチ不明";
    const changedFiles = Number.isInteger(git.changed_files) ? git.changed_files : "—";
    return branch + " / 変更ファイル " + changedFiles + "件";
  }

  function formatErrorSummary(errors) {
    if (!errors || typeof errors !== "object") {
      return "エラー情報を取得できませんでした。";
    }
    const open = Number.isInteger(errors.open) ? errors.open : "—";
    const critical = Number.isInteger(errors.critical) ? errors.critical : "—";
    return "未解決 " + open + "件 / 重大 " + critical + "件";
  }

  function renderNextActions(nextActions) {
    const container = getElement("next-actions");
    container.replaceChildren();
    if (!Array.isArray(nextActions) || !nextActions.length) {
      const item = document.createElement("li");
      item.textContent = "次の作業はありません。";
      container.appendChild(item);
      return;
    }
    for (const action of nextActions) {
      const item = document.createElement("li");
      item.textContent = action && typeof action.text === "string" && action.text
        ? action.text
        : "内容を取得できませんでした。";
      container.appendChild(item);
    }
  }

  const TURN_STATUS_LABELS = {
    completed: "完了",
    failed: "失敗",
    incomplete: "中断",
    in_progress: "作業中",
  };

  function formatTurnStatus(status) {
    return TURN_STATUS_LABELS[status] || "状態不明";
  }

  function formatTurn(turn) {
    const value = turn && typeof turn === "object" ? turn : {};
    const completedAt = value.completed_at ? formatTimestamp(value.completed_at) : "未完了";
    const userPreview = typeof value.user_preview === "string" && value.user_preview ? value.user_preview : "内容を取得できませんでした。";
    const assistantPreview = typeof value.assistant_preview === "string" && value.assistant_preview
      ? value.assistant_preview
      : "応答プレビューはありません。";
    const rolledBack = value.rolled_back === true ? "あり" : "なし";
    return "状態: " + formatTurnStatus(value.status)
      + " / 開始: " + formatTimestamp(value.started_at)
      + " / 完了: " + completedAt
      + " / ユーザー: " + userPreview
      + " / Codex: " + assistantPreview
      + " / ロールバック: " + rolledBack;
  }

  function renderRecent() {
    const recent = getDocument("recent");
    const screenState = document.querySelector('[data-state-for="recent"]');
    const content = document.querySelector('[data-content-for="recent"]');
    if (!screenState || !content) {
      throw createClientError("recent_elements_missing");
    }
    if (!recent) {
      content.hidden = true;
      screenState.hidden = false;
      screenState.textContent = "最近の更新を取得できませんでした。";
      return;
    }

    const turns = [];
    if (recent.current_turn && typeof recent.current_turn === "object") {
      turns.push(recent.current_turn);
    }
    if (Array.isArray(recent.turns)) {
      turns.push(...recent.turns);
    }

    const container = getElement("recent-turns");
    container.replaceChildren();
    if (!turns.length) {
      const item = document.createElement("li");
      item.textContent = "最近の更新はありません。";
      container.appendChild(item);
    } else {
      for (const turn of turns) {
        const item = document.createElement("li");
        item.textContent = formatTurn(turn);
        container.appendChild(item);
      }
    }
    screenState.hidden = true;
    content.hidden = false;
  }

  async function loadRecent() {
    const screenState = document.querySelector('[data-state-for="recent"]');
    const content = document.querySelector('[data-content-for="recent"]');
    screenState.hidden = false;
    screenState.textContent = "最近の更新を読み込んでいます。";
    content.hidden = true;
    try {
      await fetchDocument("recent");
      renderRecent();
    } catch (_error) {
      renderRecent();
    }
  }

  function messagePageUrl(path) {
    if (typeof path !== "string" || !/^messages\/pages\/[^/]+\.json$/.test(path)) {
      throw createClientError("message_page_path_invalid");
    }
    return "/data/" + encodeURIComponent(state.workspaceId) + "/" + path.split("/").map(encodeURIComponent).join("/");
  }

  async function fetchMessagePage(path) {
    let response;
    try { response = await window.fetch(messagePageUrl(path), { headers: { Accept: "application/json" } }); } catch (_error) { throw createClientError("network_error"); }
    if (!response.ok) { throw createClientError("http_error"); }
    const data = await response.json();
    if (!data || typeof data !== "object" || !Array.isArray(data.messages)) { throw createClientError("json_shape_invalid"); }
    return data;
  }

  function messageText(message) {
    const content = message && message.content;
    if (content && Array.isArray(content.blocks)) {
      return content.blocks.map((block) => block && typeof block.text === "string" ? block.text : "").join("\n");
    }
    return content && typeof content.text === "string" ? content.text : "内容を取得できませんでした。";
  }

  const ROLE_LABELS = {
    assistant: "Codex",
    developer: "開発者指示",
    system: "システム",
    tool: "ツール",
    user: "あなた",
  };

  function formatMessage(message) {
    const label = message && ROLE_LABELS[message.role] ? ROLE_LABELS[message.role] : "不明な発言者";
    return label + ": " + messageText(message);
  }
  function renderChat(pages) {
    const stateElement = document.querySelector('[data-state-for="chat"]');
    const content = document.querySelector('[data-content-for="chat"]');
    const container = getElement("chat-messages");
    const previous = getElement("load-previous-messages");
    container.replaceChildren();
    const messages = pages.flatMap((page) => Array.isArray(page.messages) ? page.messages : []);
    if (!messages.length) {
      const item = document.createElement("li"); item.textContent = "表示するメッセージはありません。"; container.appendChild(item);
    } else {
      for (const message of messages) {
        const item = document.createElement("li");
        const text = formatMessage(message);
        if (message && message.display_mode === "collapsed") {
          const details = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = "長文メッセージを表示";
          const body = document.createElement("pre");
          body.textContent = text;
          details.appendChild(summary); details.appendChild(body); item.appendChild(details);
        } else { item.textContent = text; }
        container.appendChild(item);
      }
    }
    const index = getDocument("messages");
    previous.hidden = !index || !Array.isArray(index.pages) || pages.length >= index.pages.length;
    stateElement.hidden = true; content.hidden = false;
  }

  async function loadChat(previous) {
    const stateElement = document.querySelector('[data-state-for="chat"]');
    const content = document.querySelector('[data-content-for="chat"]');
    stateElement.hidden = false; stateElement.textContent = "チャットを読み込んでいます。"; content.hidden = true;
    try {
      if (!previous || !state.chatPages) {
        const index = await fetchDocument("messages");
        state.chatPages = { index, pages: [] };
      }
      const entries = state.chatPages.index.pages;
      const entry = entries[entries.length - state.chatPages.pages.length - 1];
      if (entry) { state.chatPages.pages.unshift(await fetchMessagePage(entry.path)); }
      renderChat(state.chatPages.pages);
    } catch (_error) { stateElement.textContent = "チャットを取得できませんでした。"; content.hidden = true; }
  }
  function formatErrorItem(error) {
    const value = error && typeof error === "object" ? error : {};
    const severity = typeof value.severity === "string" && value.severity ? value.severity : "不明";
    const status = typeof value.status === "string" && value.status ? value.status : "不明";
    const summary = typeof value.summary === "string" && value.summary ? value.summary : "内容を取得できませんでした。";
    const occurredAt = value.last_occurred_at ? formatTimestamp(value.last_occurred_at) : "日時不明";
    const count = Number.isInteger(value.occurrence_count) ? value.occurrence_count : "—";
    const preview = typeof value.details_preview === "string" && value.details_preview ? " / 詳細: " + value.details_preview : "";
    const details = value.detail_storage === "inline" && typeof value.details === "string" && value.details ? " / 詳細全文: " + value.details : "";
    return "重要度: " + severity + " / 状態: " + status + " / 発生: " + occurredAt + " / 回数: " + count + " / " + summary + preview + details;
  }

  function renderErrors() {
    const errors = getDocument("errors");
    const stateElement = document.querySelector('[data-state-for="errors"]');
    const content = document.querySelector('[data-content-for="errors"]');
    if (!errors) { content.hidden = true; stateElement.hidden = false; stateElement.textContent = "エラー一覧を取得できませんでした。"; return; }
    const entries = Array.isArray(errors.errors) ? errors.errors.slice() : [];
    entries.sort((left, right) => (left && left.status === "open" ? 0 : 1) - (right && right.status === "open" ? 0 : 1));
    const container = getElement("error-list"); container.replaceChildren();
    if (!entries.length) { const item = document.createElement("li"); item.textContent = "表示するエラーはありません。"; container.appendChild(item); }
    else { for (const error of entries) { const item = document.createElement("li"); item.textContent = formatErrorItem(error); container.appendChild(item); } }
    stateElement.hidden = true; content.hidden = false;
  }

  async function loadErrors() {
    const stateElement = document.querySelector('[data-state-for="errors"]'); const content = document.querySelector('[data-content-for="errors"]');
    stateElement.hidden = false; stateElement.textContent = "エラー一覧を読み込んでいます。"; content.hidden = true;
    try { await fetchDocument("errors"); renderErrors(); } catch (_error) { renderErrors(); }
  }
  function renderDecisions() {
    const data = getDocument("decisions"); const stateElement = document.querySelector('[data-state-for="decisions"]'); const content = document.querySelector('[data-content-for="decisions"]');
    if (!data) { content.hidden = true; stateElement.hidden = false; stateElement.textContent = "決定事項を取得できませんでした。"; return; }
    const entries = Array.isArray(data.decisions) ? data.decisions.slice() : [];
    entries.sort((left, right) => String(right && right.decided_at || "").localeCompare(String(left && left.decided_at || "")));
    const container = getElement("decision-list"); container.replaceChildren();
    if (!entries.length) { const item = document.createElement("li"); item.textContent = "表示する決定事項はありません。"; container.appendChild(item); }
    else { for (const decision of entries) { const value = decision && typeof decision === "object" ? decision : {}; const item = document.createElement("li"); item.textContent = "表題: " + (value.title || "不明") + " / 状態: " + (value.status || "不明") + " / 決定: " + formatTimestamp(value.decided_at) + " / 内容: " + (value.description || "内容を取得できませんでした。") + " / 理由: " + (value.reason || "理由はありません。"); container.appendChild(item); } }
    stateElement.hidden = true; content.hidden = false;
  }

  async function loadDecisions() {
    const stateElement = document.querySelector('[data-state-for="decisions"]'); const content = document.querySelector('[data-content-for="decisions"]'); stateElement.hidden = false; stateElement.textContent = "決定事項を読み込んでいます。"; content.hidden = true;
    try { await fetchDocument("decisions"); renderDecisions(); } catch (_error) { renderDecisions(); }
  }
  function renderFiles() {
    const data = getDocument("files"); const stateElement = document.querySelector('[data-state-for="files"]'); const content = document.querySelector('[data-content-for="files"]');
    if (!data) { content.hidden = true; stateElement.hidden = false; stateElement.textContent = "変更ファイルを取得できませんでした。"; return; }
    const entries = Array.isArray(data.files) ? data.files : (Array.isArray(data.changed_files) ? data.changed_files : []);
    const container = getElement("file-list"); container.replaceChildren();
    if (!entries.length) { const item = document.createElement("li"); item.textContent = "変更ファイルはありません。"; container.appendChild(item); }
    else { for (const file of entries) { const value = file && typeof file === "object" ? file : {}; const item = document.createElement("li"); const path = value.path || value.new_path || "パス不明"; const rawStatus = value.status || value.change_type; const statusLabels = { added: "追加", deleted: "削除", modified: "変更", renamed: "名前変更", untracked: "未追跡" }; const status = statusLabels[rawStatus] || "状態不明"; const added = Number.isInteger(value.added_lines) ? "+" + value.added_lines + "行" : "追加行数不明"; const deleted = Number.isInteger(value.deleted_lines) ? "-" + value.deleted_lines + "行" : "削除行数不明"; const staged = value.staged === true ? "あり" : "なし"; item.textContent = "パス: " + path + " / 状態: " + status + " / 変更: " + added + " / " + deleted + " / ステージ: " + staged; container.appendChild(item); } }
    stateElement.hidden = true; content.hidden = false;
  }

  async function loadFiles() {
    const stateElement = document.querySelector('[data-state-for="files"]'); const content = document.querySelector('[data-content-for="files"]'); stateElement.hidden = false; stateElement.textContent = "変更ファイルを読み込んでいます。"; content.hidden = true;
    try { await fetchDocument("files"); renderFiles(); } catch (_error) { renderFiles(); }
  }
  function showScreen(screenName) {
    const activeScreen = SCREEN_NAMES.has(screenName) ? screenName : "dashboard";
    for (const screen of document.querySelectorAll("[data-screen]")) {
      screen.hidden = screen.dataset.screen !== activeScreen;
    }
    for (const link of document.querySelectorAll("[data-screen-link]")) {
      if (link.dataset.screenLink === activeScreen) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    }
    if (activeScreen === "recent") {
      void loadRecent();
    } else if (activeScreen === "chat") {
      void loadChat(false);
    } else if (activeScreen === "errors") {
      void loadErrors();
    } else if (activeScreen === "decisions") {
      void loadDecisions();
    } else if (activeScreen === "files") {
      void loadFiles();
    }
  }

  function setupNavigation() {
    for (const link of document.querySelectorAll("[data-screen-link]")) {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        showScreen(link.dataset.screenLink);
      });
    }
  }
  function renderDashboard() {
    const dashboard = getDocument("dashboard");
    const screenState = document.querySelector('[data-state-for="dashboard"]');
    const content = document.querySelector('[data-content-for="dashboard"]');
    if (!screenState || !content) {
      throw createClientError("dashboard_elements_missing");
    }
    if (!dashboard) {
      content.hidden = true;
      screenState.hidden = false;
      screenState.textContent = "ダッシュボードを取得できませんでした。";
      return;
    }

    const project = dashboard.project && typeof dashboard.project === "object" ? dashboard.project : {};
    setText("project-name", project.name);
    setText("project-phase", typeof project.phase === "string" && project.phase ? "Phase: " + project.phase : null);
    const codex = dashboard.codex && typeof dashboard.codex === "object" ? dashboard.codex : null;
    setText("codex-status", "Codex: " + formatCodexStatus(codex));
    setText("current-work", formatCurrentWork(codex));
    setText("latest-summary", dashboard.latest && dashboard.latest.summary);
    setText("error-summary", formatErrorSummary(dashboard.errors));
    setText("git-summary", formatGitSummary(dashboard.git));
    setText("dashboard-generated-at", formatTimestamp(dashboard.generated_at));
    renderNextActions(dashboard.next_actions);

    screenState.hidden = true;
    content.hidden = false;
  }

  async function initialize() {
    const appShell = document.querySelector(".app-shell");
    const missingWorkspace = getElement("missing-workspace");
    const globalStatus = getElement("global-status");
    const refreshStatus = getElement("refresh-status");
    const refreshButton = getElement("refresh-button");

    if (!appShell) {
      throw createClientError("app_shell_missing");
    }

    state.workspaceId = readWorkspaceId();
    refreshButton.disabled = true;

    if (!state.workspaceId) {
      appShell.dataset.appState = APP_STATE.MISSING_WORKSPACE;
      missingWorkspace.hidden = false;
      globalStatus.textContent = "workspace_idが指定されていないため、データを取得していません。";
      refreshStatus.textContent = "更新できません";
      return;
    }

    appShell.dataset.appState = APP_STATE.LOADING;
    missingWorkspace.hidden = true;
    globalStatus.textContent = "初期データを読み込んでいます。";
    refreshStatus.textContent = "更新中";

    const results = await fetchInitialDocuments();
    const failures = results.filter(({ result }) => result.status === "rejected");
    for (const { documentName, result } of failures) {
      state.errors.set(documentName, result.reason.code || "request_failed");
    }
    renderDashboard();

    appShell.dataset.appState = failures.length ? APP_STATE.DEGRADED : APP_STATE.READY;
    globalStatus.textContent = failures.length
      ? "一部の初期データを取得できませんでした。"
      : "初期データを取得しました。";
    const lastUpdatedStatus = formatLastUpdatedStatus();
    refreshStatus.textContent = failures.length
      ? "一部の取得に失敗 / " + lastUpdatedStatus
      : lastUpdatedStatus;
  }

  window.CodexMobileDashboard = {
    fetchDocument,
    fetchInitialDocuments,
    getDocument,
    getDocumentError,
    getWorkspaceId: () => state.workspaceId,
    loadChat,
    loadDecisions,
    loadErrors,
    loadFiles,
    loadRecent,
    renderDashboard,
    renderRecent,
    showScreen,
  };

  document.addEventListener("DOMContentLoaded", () => {
    setupNavigation();
    const previousMessagesButton = document.getElementById("load-previous-messages");
    if (previousMessagesButton) { previousMessagesButton.addEventListener("click", () => { void loadChat(true); }); }
    void initialize();
  });
})();
