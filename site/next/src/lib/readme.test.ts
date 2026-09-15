import assert from "node:assert/strict";
import { test } from "node:test";
import { renderReadme, rewriteUrl, sanitizeReadmeHtml, stripHeader } from "./readme.ts";

test("rewriteUrl keeps http(s) and mailto, drops javascript/data", () => {
  assert.equal(rewriteUrl("https://gptme.org/docs/"), "https://gptme.org/docs/");
  assert.equal(rewriteUrl("mailto:hi@example.com"), "mailto:hi@example.com");
  assert.equal(rewriteUrl("#install"), "#install");
  assert.equal(rewriteUrl("javascript:alert(1)"), "");
  assert.equal(rewriteUrl("data:text/html,<script>alert(1)</script>"), "");
  assert.equal(rewriteUrl("vbscript:alert(1)"), "");
});

test("rewriteUrl maps relative media and repo paths", () => {
  assert.equal(rewriteUrl("./media/logo.png"), "https://gptme.org/media/logo.png");
  assert.equal(rewriteUrl("docs/tools.md"), "https://github.com/gptme/gptme/blob/master/docs/tools.md");
  assert.equal(rewriteUrl("//evil.example/x"), "https://evil.example/x");
});

test("sanitizeReadmeHtml strips script, event handlers, and javascript hrefs", () => {
  const html = sanitizeReadmeHtml(
    `<p>ok</p><script>alert(1)</script><img src="https://gptme.org/media/logo.png" alt="logo" onerror="alert(1)"><a href="javascript:alert(1)">xss</a>`,
  );
  assert.equal(html.includes("<script"), false);
  assert.equal(html.includes("onerror"), false);
  assert.equal(html.includes("javascript:"), false);
  assert.equal(html.includes("alert(1)"), false);
  assert.match(html, /<p>ok<\/p>/);
  assert.match(html, /src="https:\/\/gptme.org\/media\/logo.png"/);
});

test("sanitizeReadmeHtml drops unsafe srcset and data/vbscript URLs at the sink", () => {
  const html = sanitizeReadmeHtml(
    `<img src="data:image/svg+xml,<svg>" srcset="javascript:alert(1) 1x, https://gptme.org/media/logo.png 2x" alt="x"><a href="vbscript:alert(1)">nope</a>`,
  );
  assert.equal(html.includes("javascript:"), false);
  assert.equal(html.includes("data:"), false);
  assert.equal(html.includes("vbscript:"), false);
  assert.equal(html.includes("alert(1)"), false);
  assert.match(html, /srcset="https:\/\/gptme.org\/media\/logo.png 2x"/);
});

test("sanitizeReadmeHtml keeps heading permalinks and table wrappers", () => {
  const html = sanitizeReadmeHtml(
    `<h2 id="install" class="group relative"><a class="absolute" href="#install" aria-hidden="true" tabindex="-1">#</a>Install</h2><div class="overflow-x-auto"><table><thead><tr><th>A</th></tr></thead></table></div>`,
  );
  assert.match(html, /id="install"/);
  assert.match(html, /href="#install"/);
  assert.match(html, /aria-hidden="true"/);
  assert.match(html, /overflow-x-auto/);
  assert.match(html, /<table>/);
});

test("renderReadme does not emit raw HTML from the markdown source", () => {
  const html = renderReadme(`# Title

Hello <script>alert(1)</script>

[xss](javascript:alert(1))

<img src=x onerror=alert(1)>
`);
  assert.equal(html.includes("<script"), false);
  assert.equal(html.includes("javascript:"), false);
  assert.equal(html.includes("onerror"), false);
  assert.equal(html.includes("alert(1)"), false);
  assert.match(html, /id="title"/);
  assert.match(html, />Hello\s*</);
});

test("stripHeader drops the badge block", () => {
  const md = stripHeader(`<p align="center">logo</p>\n<!-- Badges -->\n<p>badges</p>\n# Real\n`);
  assert.equal(md.startsWith("# Real"), true);
});
