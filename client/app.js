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
  const JST_TIMESTAMP_FORMATTER = new Intl.DateTimeFormat("ja-JP", {
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
    minute: "2-digit",
    month: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Tokyo",
    year: "numeric",
  });
  const state = {
    activeScreen: "dashboard",
    cache: new Map(),
    documents: new Map(),
    errors: new Map(),
    workspaceId: null,
    autoRefreshTimer: null,
    chatFilter: "chat",
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

  function formatLoadFailure(documentName, subject) {
    const code = getDocumentError(documentName);
    if (code === "json_invalid" || code === "json_shape_invalid") { return subject + "のJSONデータが不正です。"; }
    return subject + "を取得できませんでした（通信失敗または不正な応答）。";
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
    if (typeof value !== "string" || !value) { return "日時不明"; }
    const timestamp = new Date(value);
    if (!Number.isFinite(timestamp.getTime())) { return "日時不明"; }
    return JST_TIMESTAMP_FORMATTER.format(timestamp) + " JST";
  }

  function formatOptionalTimestamp(value) {
    return typeof value === "string" && value ? formatTimestamp(value) : "日時未記録";
  }

  function renderDefinitionList(elementId, entries) {
    const container = getElement(elementId);
    container.replaceChildren();
    for (const [label, value] of entries) {
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = value;
      container.appendChild(term);
      container.appendChild(description);
    }
  }

  function formatAvailability(value) {
    if (value === true) { return "利用可能"; }
    if (value === false) { return "利用不可"; }
    return "状態不明";
  }

  function formatCollectorStatus(value) {
    const labels = { failed: "取得失敗", ok: "正常", warning: "注意あり" };
    return labels[value] || "状態不明";
  }

  function formatRawCodexStatus(value) {
    return CODEX_STATUS_LABELS[value] || CODEX_STATUS_LABELS.unknown;
  }

  function isStaleTimestamp(value) {
    const time = Date.parse(value);
    return Number.isFinite(time) && Date.now() - time > 10 * 60 * 1000;
  }
  function formatLastUpdatedStatus() {
    const dashboard = getDocument("dashboard");
    const timestamp = dashboard && dashboard.generated_at;
    return "最終更新: " + formatTimestamp(timestamp) + (isStaleTimestamp(timestamp) ? " / データが古い可能性があります" : "");
  }

  function formatGitSummary(git) {
    if (!git || typeof git !== "object") {
      return "Git情報を取得できませんでした。";
    }
    const branch = typeof git.branch === "string" && git.branch ? git.branch : "ブランチ不明";
    const changedFiles = Number.isInteger(git.changed_files) ? git.changed_files : "—";
    return branch + " / 変更ファイル " + changedFiles + "件";
  }

  function formatChangeSummary(latest) {
    if (!latest || typeof latest !== "object" || typeof latest.summary !== "string" || !latest.summary.trim()) {
      return "データがありません（変更要約）。";
    }
    return latest.summary;
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
      item.textContent = "データがありません（次の作業）。";
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

  function formatTurnMetadata(turn) {
    const value = turn && typeof turn === "object" ? turn : {};
    const completedAt = value.completed_at ? formatTimestamp(value.completed_at) : "未完了";
    const rolledBack = value.rolled_back === true ? "あり" : "なし";
    return "状態: " + formatTurnStatus(value.status)
      + " / 開始: " + formatTimestamp(value.started_at)
      + " / 完了: " + completedAt
      + " / ロールバック: " + rolledBack;
  }

  function formatTurnPreviews(turn) {
    const value = turn && typeof turn === "object" ? turn : {};
    const userPreview = typeof value.user_preview === "string" && value.user_preview ? value.user_preview : "内容を取得できませんでした。";
    const assistantPreview = typeof value.assistant_preview === "string" && value.assistant_preview
      ? value.assistant_preview
      : "応答プレビューはありません。";
    return "#### ユーザー\n\n" + userPreview + "\n\n#### Codex\n\n" + assistantPreview;
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
      screenState.textContent = formatLoadFailure("recent", "最近の更新");
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
      item.textContent = "データがありません（最近の更新）。";
      container.appendChild(item);
    } else {
      for (const turn of turns) {
        const item = document.createElement("li");
        const metadata = document.createElement("p");
        metadata.className = "turn-metadata";
        metadata.textContent = formatTurnMetadata(turn);
        const previews = document.createElement("div");
        previews.className = "markdown-body";
        renderMarkdown(previews, formatTurnPreviews(turn));
        item.appendChild(metadata);
        item.appendChild(previews);
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
    const createdAt = message && message.created_at ? formatTimestamp(message.created_at) : "日時不明";
    return label + " / 日時: " + createdAt + "\n\n" + messageText(message);
  }

  function messageCategory(message) {
    const value = message && typeof message === "object" ? message : {};
    if (value.message_type === "developer_instruction" || value.message_type === "developer_instruction_reference" || value.role === "developer" || value.role === "system") { return "internal"; }
    if (value.message_type === "tool_summary" || value.role === "tool") { return "tool"; }
    if (value.role === "assistant" && value.phase === "commentary") { return "commentary"; }
    return "chat";
  }

  const COLLAPSED_MESSAGE_LABELS = {
    chat: "長文メッセージを表示",
    commentary: "Codexの内部進捗を表示",
    tool: "ツール実行情報を表示",
    internal: "内部指示を表示",
  };

  function renderMarkdown(container, text) {
    if (!window.markdownit || !window.DOMPurify || typeof text !== "string") { container.textContent = text || "内容を取得できませんでした。"; return; }
    try {
      const renderer = window.markdownit({ html: false, linkify: true, maxNesting: 20, highlight(code, language) {
        if (window.hljs && language && window.hljs.getLanguage(language)) { return '<pre><code class="hljs language-' + language + '">' + window.hljs.highlight(code, { language }).value + "</code></pre>"; }
        return "<pre><code>" + renderer.utils.escapeHtml(code) + "</code></pre>";
      } });
      renderer.validateLink = (url) => /^https?:\/\//i.test(url);
      container.innerHTML = window.DOMPurify.sanitize(renderer.render(text), { ALLOWED_TAGS: ["p","h1","h2","h3","h4","h5","h6","strong","em","ul","ol","li","blockquote","pre","code","table","thead","tbody","tr","th","td","hr","a"], ALLOWED_ATTR: ["href","title","class","target","rel"] });
      for (const link of container.querySelectorAll("a")) { if (!/^https?:\/\//i.test(link.href)) { link.remove(); } else { link.target = "_blank"; link.rel = "noopener noreferrer"; } }
      for (const code of container.querySelectorAll("pre > code")) { const button = document.createElement("button"); button.type = "button"; button.textContent = "コードをコピー"; button.addEventListener("click", async () => { try { if (!navigator.clipboard) { throw new Error("clipboard_unavailable"); } await navigator.clipboard.writeText(code.textContent); button.textContent = "コピーしました"; } catch (_error) { button.textContent = "コピーできませんでした"; } }); code.parentElement.before(button); }
    } catch (_error) { container.textContent = text; }
  }

  function renderChat(pages) {
    const stateElement = document.querySelector('[data-state-for="chat"]');
    const content = document.querySelector('[data-content-for="chat"]');
    const container = getElement("chat-messages");
    const previous = getElement("load-previous-messages");
    container.replaceChildren();
    const allMessages = pages.flatMap((page) => Array.isArray(page.messages) ? page.messages : []);
    const messages = state.chatFilter === "all"
      ? allMessages
      : allMessages.filter((message) => messageCategory(message) === state.chatFilter);
    if (!messages.length) {
      const item = document.createElement("li");
      item.textContent = allMessages.length ? "選択した種別のメッセージはありません。" : "データがありません（メッセージ）。";
      container.appendChild(item);
    } else {
      for (const message of messages) {
        const item = document.createElement("li");
        const text = formatMessage(message);
        const category = messageCategory(message);
        if (message && message.display_mode === "collapsed") {
          const details = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = COLLAPSED_MESSAGE_LABELS[category];
          const body = document.createElement(category === "chat" ? "div" : "pre");
          if (category === "chat") {
            body.className = "markdown-body";
            renderMarkdown(body, text);
          } else {
            body.textContent = text;
          }
          details.appendChild(summary); details.appendChild(body); item.appendChild(details);
        } else {
          const body = document.createElement("div"); body.className = "markdown-body"; renderMarkdown(body, text); item.appendChild(body);
        }
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
    } catch (_error) { stateElement.textContent = formatLoadFailure("messages", "チャット"); content.hidden = true; }
  }
  function errorSection(error) {
    const value = error && typeof error === "object" ? error : {};
    if (value.rolled_back === true) { return "ロールバック済み"; }
    if (value.status === "open") { return "未解決"; }
    if (value.status === "resolved") { return "解決済み"; }
    if (value.status === "ignored") { return "無視"; }
    return "区分不明";
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
    return "区分: " + errorSection(value) + " / 重要度: " + severity + " / 状態: " + status + " / 発生: " + occurredAt + " / 回数: " + count + " / " + summary + preview + details;
  }

  function renderErrors() {
    const errors = getDocument("errors");
    const stateElement = document.querySelector('[data-state-for="errors"]');
    const content = document.querySelector('[data-content-for="errors"]');
    if (!errors) { content.hidden = true; stateElement.hidden = false; stateElement.textContent = formatLoadFailure("errors", "エラー一覧"); return; }
    const entries = Array.isArray(errors.errors) ? errors.errors.slice() : [];
    const counts = errors.counts && typeof errors.counts === "object" ? errors.counts : {};
    const openCount = Number.isInteger(counts.open) ? counts.open : entries.filter((entry) => entry && entry.status === "open" && entry.rolled_back !== true).length;
    const criticalCount = Number.isInteger(counts.critical) ? counts.critical : entries.filter((entry) => entry && entry.status === "open" && entry.rolled_back !== true && entry.severity === "critical").length;
    const rolledBackCount = entries.filter((entry) => entry && entry.rolled_back === true).length;
    getElement("error-count-summary").textContent = "未解決 " + openCount + "件 / 重大 " + criticalCount + "件 / ロールバック済み " + rolledBackCount + "件 / 全履歴 " + entries.length + "件";
    const sectionOrder = { "未解決": 0, "ロールバック済み": 1, "解決済み": 2, "無視": 3, "区分不明": 4 };
    entries.sort((left, right) => sectionOrder[errorSection(left)] - sectionOrder[errorSection(right)]);
    const container = getElement("error-list"); container.replaceChildren();
    if (!entries.length) { const item = document.createElement("li"); item.textContent = "データがありません（エラー）。"; container.appendChild(item); }
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
    if (!data) { content.hidden = true; stateElement.hidden = false; stateElement.textContent = formatLoadFailure("decisions", "決定事項"); return; }
    const entries = Array.isArray(data.decisions) ? data.decisions.slice() : [];
    entries.sort((left, right) => String(right && right.decided_at || "").localeCompare(String(left && left.decided_at || "")));
    const container = getElement("decision-list"); container.replaceChildren();
    if (!entries.length) { const item = document.createElement("li"); item.textContent = "データがありません（決定事項）。"; container.appendChild(item); }
    else { for (const decision of entries) { const value = decision && typeof decision === "object" ? decision : {}; const item = document.createElement("li"); item.textContent = "表題: " + (value.title || "不明") + " / 状態: " + (value.status || "不明") + " / 決定: " + formatTimestamp(value.decided_at) + " / 内容: " + (value.description || "内容を取得できませんでした。") + " / 理由: " + (value.reason || "理由はありません。"); container.appendChild(item); } }
    stateElement.hidden = true; content.hidden = false;
  }

  async function loadDecisions() {
    const stateElement = document.querySelector('[data-state-for="decisions"]'); const content = document.querySelector('[data-content-for="decisions"]'); stateElement.hidden = false; stateElement.textContent = "決定事項を読み込んでいます。"; content.hidden = true;
    try { await fetchDocument("decisions"); renderDecisions(); } catch (_error) { renderDecisions(); }
  }
  function renderFiles() {
    const data = getDocument("files"); const stateElement = document.querySelector('[data-state-for="files"]'); const content = document.querySelector('[data-content-for="files"]');
    if (!data) { content.hidden = true; stateElement.hidden = false; stateElement.textContent = formatLoadFailure("files", "変更ファイル"); return; }
    const entries = Array.isArray(data.files) ? data.files : (Array.isArray(data.changed_files) ? data.changed_files : []);
    const container = getElement("file-list"); container.replaceChildren();
    if (!entries.length) { const item = document.createElement("li"); item.textContent = "データがありません（変更ファイル）。"; container.appendChild(item); }
    else { for (const file of entries) { const value = file && typeof file === "object" ? file : {}; const item = document.createElement("li"); const path = value.path || value.new_path || "パス不明"; const rawStatus = value.status || value.change_type; const statusLabels = { added: "追加", deleted: "削除", modified: "変更", renamed: "名前変更", untracked: "未追跡" }; const status = statusLabels[rawStatus] || "状態不明"; const added = Number.isInteger(value.added_lines) ? "+" + value.added_lines + "行" : "追加行数不明"; const deleted = Number.isInteger(value.deleted_lines) ? "-" + value.deleted_lines + "行" : "削除行数不明"; const staged = value.staged === true ? "あり" : "なし"; item.textContent = "パス: " + path + " / 状態: " + status + " / 変更: " + added + " / " + deleted + " / ステージ: " + staged; container.appendChild(item); } }
    stateElement.hidden = true; content.hidden = false;
  }

  async function loadFiles() {
    const stateElement = document.querySelector('[data-state-for="files"]'); const content = document.querySelector('[data-content-for="files"]'); stateElement.hidden = false; stateElement.textContent = "変更ファイルを読み込んでいます。"; content.hidden = true;
    try { await fetchDocument("files"); renderFiles(); } catch (_error) { renderFiles(); }
  }
  function showScreen(screenName) {
    const activeScreen = SCREEN_NAMES.has(screenName) ? screenName : "dashboard";
    state.activeScreen = activeScreen;
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
    } else if (activeScreen === "system") {
      renderSystem();
    }
  }

  function stopAutoRefresh() {
    if (state.autoRefreshTimer !== null && typeof window.clearInterval === "function") { window.clearInterval(state.autoRefreshTimer); }
    state.autoRefreshTimer = null;
  }

  function setupAutoRefresh() {
    if (state.autoRefreshTimer !== null || typeof window.setInterval !== "function") { return; }
    state.autoRefreshTimer = window.setInterval(() => { void initialize(); }, 15 * 1000);
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "hidden") { stopAutoRefresh(); return; }
    void initialize();
    setupAutoRefresh();
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
      screenState.textContent = formatLoadFailure("dashboard", "ダッシュボード");
      return;
    }

    const project = dashboard.project && typeof dashboard.project === "object" ? dashboard.project : {};
    setText("project-name", project.name);
    setText("project-phase", typeof project.phase === "string" && project.phase ? "Phase: " + project.phase : "Phase: 情報なし");
    const codex = dashboard.codex && typeof dashboard.codex === "object" ? dashboard.codex : null;
    setText("codex-status", "Codex: " + formatCodexStatus(codex));
    setText("current-work", formatCurrentWork(codex));
    setText("latest-summary", formatChangeSummary(dashboard.latest));
    setText("error-summary", formatErrorSummary(dashboard.errors));
    setText("git-summary", formatGitSummary(dashboard.git));
    setText("dashboard-generated-at", formatTimestamp(dashboard.generated_at));
    renderNextActions(dashboard.next_actions);

    screenState.hidden = true;
    content.hidden = false;
  }

  function renderSystem() {
    const metadata = getDocument("metadata");
    const health = getDocument("health");
    const screenState = document.querySelector('[data-state-for="system"]');
    const content = document.querySelector('[data-content-for="system"]');
    if (!screenState || !content) {
      throw createClientError("system_elements_missing");
    }
    if (!metadata || !health) {
      content.hidden = true;
      screenState.hidden = false;
      if ((!metadata && !getDocumentError("metadata")) || (!health && !getDocumentError("health"))) {
        screenState.textContent = "システム情報を読み込んでいます。";
      } else {
        screenState.textContent = !metadata
          ? formatLoadFailure("metadata", "PC収集ツール情報")
          : formatLoadFailure("health", "Androidサーバー情報");
      }
      return;
    }

    const collector = metadata.collector && typeof metadata.collector === "object"
      ? metadata.collector
      : {};
    renderDefinitionList("collector-status", [
      ["収集状態", formatCollectorStatus(collector.status)],
      ["Codex状態", formatRawCodexStatus(collector.codex_status)],
      ["最終確認日時", formatTimestamp(collector.last_checked_at)],
      ["最終データ変更日時", formatOptionalTimestamp(collector.last_data_change_at)],
      ["前回送信成功日時", formatOptionalTimestamp(collector.last_send_succeeded_at)],
    ]);
    renderDefinitionList("server-status", [
      ["サーバー状態", health.status === "ok" ? "正常" : "状態不明"],
      ["サーバーバージョン", typeof health.server_version === "string" && health.server_version ? health.server_version : "不明"],
      ["稼働秒数", Number.isFinite(health.uptime_seconds) ? health.uptime_seconds + "秒" : "不明"],
      ["公開領域", formatAvailability(health.public_available)],
      ["受信領域", formatAvailability(health.staging_available)],
      ["ログ", formatAvailability(health.logging_available)],
      ["保存容量", formatAvailability(health.storage_available)],
      ["公開中Snapshot ID", typeof health.current_snapshot_id === "string" && health.current_snapshot_id ? health.current_snapshot_id : "公開済みデータなし"],
      ["最終受信日時", formatOptionalTimestamp(health.last_received_at)],
    ]);
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
    if (typeof appShell.setAttribute === "function") { appShell.setAttribute("aria-busy", "true"); }
    missingWorkspace.hidden = true;
    globalStatus.textContent = "初期データを読み込んでいます。";
    refreshStatus.textContent = "更新中";

    const results = await fetchInitialDocuments();
    const failures = results.filter(({ result }) => result.status === "rejected");
    for (const { documentName, result } of failures) {
      state.errors.set(documentName, result.reason.code || "request_failed");
    }
    renderDashboard();
    if (state.activeScreen === "system") {
      renderSystem();
    }

    appShell.dataset.appState = failures.length ? APP_STATE.DEGRADED : APP_STATE.READY;
    globalStatus.textContent = failures.length
      ? "一部の初期データを取得できませんでした。"
      : "初期データを取得しました。";
    const lastUpdatedStatus = formatLastUpdatedStatus();
    refreshStatus.textContent = failures.length
      ? "一部の取得に失敗 / " + lastUpdatedStatus
      : lastUpdatedStatus;
    if (typeof appShell.setAttribute === "function") { appShell.setAttribute("aria-busy", "false"); }
    refreshButton.disabled = false;
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
    renderSystem,
    showScreen,
  };

  document.addEventListener("DOMContentLoaded", () => {
    setupNavigation();
    setupAutoRefresh();
    document.addEventListener("visibilitychange", handleVisibilityChange);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    const refreshButton = document.getElementById("refresh-button");
    if (refreshButton && typeof refreshButton.addEventListener === "function") { refreshButton.addEventListener("click", () => { void initialize(); }); }
    const previousMessagesButton = document.getElementById("load-previous-messages");
    if (previousMessagesButton) { previousMessagesButton.addEventListener("click", () => { void loadChat(true); }); }
    const chatFilter = document.getElementById("chat-type-filter");
    if (chatFilter && typeof chatFilter.addEventListener === "function") {
      chatFilter.addEventListener("change", () => {
        const allowed = new Set(["chat", "commentary", "tool", "internal", "all"]);
        state.chatFilter = allowed.has(chatFilter.value) ? chatFilter.value : "chat";
        if (state.chatPages) { renderChat(state.chatPages.pages); }
      });
    }
    void initialize();
  });
})();
