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

    appShell.dataset.appState = failures.length ? APP_STATE.DEGRADED : APP_STATE.READY;
    globalStatus.textContent = failures.length
      ? "一部の初期データを取得できませんでした。"
      : "初期データを取得しました。";
    refreshStatus.textContent = failures.length ? "一部の取得に失敗" : "更新待機中";
  }

  window.CodexMobileDashboard = {
    fetchDocument,
    fetchInitialDocuments,
    getDocument,
    getDocumentError,
    getWorkspaceId: () => state.workspaceId,
  };

  document.addEventListener("DOMContentLoaded", () => {
    void initialize();
  });
})();
