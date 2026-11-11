// The preview service worker's path rules pass the same cases as the server (tests/test_content.py).
// Run: node --test tests/path_rules.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { srResolvePath, srContentType } = require("../api/_core/preview/lib.js");
const rules = JSON.parse(readFileSync(new URL("./path_rules.json", import.meta.url), "utf8"));

for (const site of rules.sites) {
  test(`path rules, start page ${site.entry}`, () => {
    const files = new Set(site.files);
    for (const c of site.cases) {
      const want = "file" in c ? { file: c.file } : "redirect" in c ? { redirect: c.redirect, status: c.status } : { missing: true };
      assert.deepEqual(srResolvePath(c.path, site.entry, (p) => files.has(p)), want, JSON.stringify(c));
    }
  });
}

test("content types", () => {
  assert.equal(srContentType("index.html"), "text/html; charset=utf-8");
  assert.equal(srContentType("a.b/app.JS"), "text/javascript; charset=utf-8");
  assert.equal(srContentType("dir.v2/README"), "application/octet-stream");
  assert.equal(srContentType("img/logo.svg"), "image/svg+xml");
});
