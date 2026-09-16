import assert from "node:assert/strict";
import { test } from "node:test";
import { readmeHashRedirect } from "./readme-hash.ts";

const none = () => false;
const has = (...ids: string[]) => (id: string) => ids.includes(id);

test("readmeHashRedirect sends unknown homepage hashes to /readme/", () => {
  assert.equal(readmeHashRedirect("/", "#installation", none), "/readme/#installation");
  assert.equal(readmeHashRedirect("/index.html", "#-demos", none), "/readme/#-demos");
  assert.equal(
    readmeHashRedirect("/", "#how-do-i-install-gptme", none),
    "/readme/#how-do-i-install-gptme",
  );
});

test("readmeHashRedirect keeps hashes that exist on the current page", () => {
  assert.equal(readmeHashRedirect("/", "#copy-status", has("copy-status")), null);
  assert.equal(readmeHashRedirect("/", "#demo-title", has("demo-title")), null);
});

test("readmeHashRedirect ignores non-home paths and empty hashes", () => {
  assert.equal(readmeHashRedirect("/readme/", "#installation", none), null);
  assert.equal(readmeHashRedirect("/docs/", "#installation", none), null);
  assert.equal(readmeHashRedirect("/", "", none), null);
  assert.equal(readmeHashRedirect("/", "#", none), null);
});

test("readmeHashRedirect decodes percent-encoded hashes", () => {
  assert.equal(readmeHashRedirect("/", "#hello%20world", none), "/readme/#hello%20world");
  assert.equal(readmeHashRedirect("/", "#hello%20world", has("hello world")), null);
});
