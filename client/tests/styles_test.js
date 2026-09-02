"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const clientRoot = path.join(__dirname, "..");
const styles = fs.readFileSync(path.join(clientRoot, "styles.css"), "utf8");
const darkTheme = fs.readFileSync(
  path.join(clientRoot, "vendor", "highlight-github-dark-11.11.1.min.css"),
  "utf8",
);

assert.match(
  styles,
  /@import url\("vendor\/highlight-github-dark-11\.11\.1\.min\.css"\) screen and \(prefers-color-scheme: dark\);/,
);
assert.match(darkTheme, /\.hljs\{color:#c9d1d9;background:#0d1117\}/);
assert.doesNotMatch(darkTheme, /background:#fff(?:[;}])/);

console.log("client dark code theme tests passed");
