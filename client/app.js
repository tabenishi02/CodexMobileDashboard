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
    renderDashboard,
  };

  document.addEventListener("DOMContentLoaded", () => {
    void initialize();
  });
})();
