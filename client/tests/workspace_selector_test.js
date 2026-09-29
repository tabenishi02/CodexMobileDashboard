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
    hidden: false,
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
  };
}

async function run() {
  const listeners = {};
  const requests = [];
  const workspaceList = listElement();
  const workspaceState = { hidden: false, textContent: "" };
  const appHeader = { hidden: false };
  const selector = { hidden: true };
  const bottomNavigation = { hidden: false };
  const globalStatus = { hidden: false, textContent: "" };
  const refreshButton = { disabled: false, addEventListener() {} };
  const elements = new Map([
    ["app-header", appHeader],
    ["workspace-selector", selector],
    ["workspace-list-state", workspaceState],
    ["workspace-list", workspaceList],
    ["bottom-navigation", bottomNavigation],
    ["global-status", globalStatus],
    ["refresh-button", refreshButton],
    ["refresh-status", { textContent: "" }],
  ]);
  const screens = [{ dataset: { screen: "dashboard" }, hidden: false }];
  const context = {
    URLSearchParams,
    console,
    document: {
      addEventListener(name, listener) { listeners[name] = listener; },
      createElement(tag) {
        return {
          tag,
          children: [],
          hidden: false,
          href: "",
          textContent: "",
          appendChild(child) { this.children.push(child); },
        };
      },
      getElementById(id) { return elements.get(id) || null; },
      querySelector(selectorText) {
        if (selectorText === ".app-shell") { return { dataset: {} }; }
        return null;
      },
      querySelectorAll(selectorText) {
        if (selectorText === "[data-screen]") { return screens; }
        return [];
      },
      visibilityState: "visible",
    },
    window: {
      fetch: async (url, options) => {
        requests.push({ url, options });
        return response(200, {
          workspaces: [
            {
              workspace_id: "workspace-1",
              project_name: "CodexMobileDashboard",
              last_received_at: "2026-09-29T06:00:00+00:00",
            },
            {
              workspace_id: "workspace-2",
              project_name: "NumpadWindowController",
              last_received_at: null,
            },
          ],
        }, '"workspaces-v1"');
      },
      location: { search: "" },
      setInterval: () => 1,
      clearInterval() {},
    },
  };

  vm.runInNewContext(fs.readFileSync("client/app.js", "utf8"), context, { filename: "app.js" });
  listeners.DOMContentLoaded();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.strictEqual(appHeader.hidden, true);
  assert.strictEqual(selector.hidden, false);
  assert.strictEqual(bottomNavigation.hidden, true);
  assert.strictEqual(globalStatus.hidden, true);
  assert.strictEqual(workspaceState.hidden, true);
  assert.strictEqual(workspaceList.hidden, false);
  assert.strictEqual(workspaceList.children.length, 2);
  assert.strictEqual(workspaceList.children[0].children[0].href, "/?workspace_id=workspace-1");
  assert.strictEqual(workspaceList.children[0].children[0].children[0].textContent, "CodexMobileDashboard");
  assert.strictEqual(
    workspaceList.children[0].children[0].children[1].textContent,
    "最終更新: 2026/09/29 15:00:00 JST",
  );
  assert.strictEqual(requests[0].url, "/workspaces");
  assert.strictEqual(context.window.CodexMobileDashboard.getWorkspaceId(), null);
  console.log("workspace selector tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
