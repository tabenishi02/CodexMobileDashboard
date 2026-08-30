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

async function run() {
  const listeners = {};
  const elements = new Map([
    ["missing-workspace", { hidden: true }],
    ["global-status", { textContent: "" }],
    ["refresh-status", { textContent: "" }],
    ["refresh-button", { disabled: false }],
  ]);
  const appShell = { dataset: {} };
  const requests = [];
  const responses = [
    response(200, { data_type: "dashboard" }, '"dashboard-v1"'),
    response(200, { data_type: "metadata" }, '"metadata-v1"'),
    response(200, { status: "ok" }, '"health-v1"'),
  ];
  const context = {
    URLSearchParams,
    console,
    document: {
      addEventListener: (name, listener) => { listeners[name] = listener; },
      getElementById: (id) => elements.get(id) || null,
      querySelector: (selector) => selector === ".app-shell" ? appShell : null,
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
  console.log("app JSON retrieval tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
