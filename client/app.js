(() => {
  "use strict";

  const APP_STATE = {
    MISSING_WORKSPACE: "missing-workspace",
    READY: "ready",
  };

  function getElement(id) {
    const element = document.getElementById(id);
    if (!element) {
      throw new Error("required_element_missing");
    }
    return element;
  }

  function readWorkspaceId() {
    const workspaceId = new URLSearchParams(window.location.search).get("workspace_id");
    return workspaceId && workspaceId.trim() ? workspaceId.trim() : null;
  }

  function initialize() {
    const appShell = document.querySelector(".app-shell");
    const missingWorkspace = getElement("missing-workspace");
    const globalStatus = getElement("global-status");
    const refreshStatus = getElement("refresh-status");
    const refreshButton = getElement("refresh-button");

    if (!appShell) {
      throw new Error("app_shell_missing");
    }

    const workspaceId = readWorkspaceId();
    refreshButton.disabled = true;

    if (!workspaceId) {
      appShell.dataset.appState = APP_STATE.MISSING_WORKSPACE;
      missingWorkspace.hidden = false;
      globalStatus.textContent = "workspace_idが指定されていないため、データを取得していません。";
      refreshStatus.textContent = "更新できません";
      return;
    }

    appShell.dataset.appState = APP_STATE.READY;
    missingWorkspace.hidden = true;
    globalStatus.textContent = "表示の準備ができました。";
    refreshStatus.textContent = "データ取得処理は未実装です";
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();