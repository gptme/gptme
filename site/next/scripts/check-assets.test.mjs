import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { extractRefs, findMissing } from "./check-assets.mjs";

function tree(files) {
  const root = mkdtempSync(join(tmpdir(), "check-assets-"));
  for (const [path, body] of Object.entries(files)) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), body);
  }
  return root;
}

test("skips external, protocol-relative, fragment and data refs", () => {
  const html = `<a href="https://x.org/a"></a><a href="//cdn.x/a"></a><a href="#top"></a>
    <img src="data:image/png;base64,AA"><a href="mailto:a@b.c"></a><a href="/ok/">`;
  assert.deepEqual(extractRefs(html, "html"), ["/ok/"]);
});

test("reads srcset candidates and CSS only from style blocks/attributes", () => {
  const html = `<img srcset="/a.png 1x, /b.png 2x"><style>.x{background:url('/c.png')}</style>
    <div style="background:url(/d.png)"></div><p>prose mentioning url(/not-a-ref)</p>`;
  assert.deepEqual(extractRefs(html, "html").sort(), ["/a.png", "/b.png", "/c.png", "/d.png"]);
});

test("ignores inline script bodies and unrendered template syntax", () => {
  const html = `<script src="/app.js"></script><script>el.innerHTML = \`<a href="\${url}">\`</script>
    <link href="{{ pathto('_static/theme.css', 1) }}" rel="stylesheet">`;
  assert.deepEqual(extractRefs(html, "html"), ["/app.js"]);
});

test("passes when every same-origin reference resolves", () => {
  const root = tree({
    "index.html": `<link href="/assets/s.css" rel="stylesheet"><a href="/readme/#demos"></a><img src="media/logo.png?v=1">`,
    "readme/index.html": `<a href="../">home</a>`,
    "assets/s.css": `@font-face{src:url(/assets/f.woff2) format("woff2"),url("./f.woff")}`,
    "assets/f.woff2": "",
    "assets/f.woff": "",
    "media/logo.png": "",
  });
  assert.deepEqual(findMissing(root), []);
});

test("reports fonts whose url() was not rebased after an inlined @import", () => {
  const root = tree({
    "index.html": `<link href="/assets/s.css" rel="stylesheet">`,
    "assets/s.css": `@font-face{src:url(./files/inter-latin-400-normal.woff2)}`,
  });
  assert.deepEqual(findMissing(root), [
    { file: "assets/s.css", ref: "./files/inter-latin-400-normal.woff2" },
  ]);
});

test("a reference that escapes the deployed tree is missing", () => {
  const root = tree({ "site/index.html": `<img src="../outside.png">`, "outside.png": "" });
  assert.deepEqual(findMissing(join(root, "site")), [{ file: "index.html", ref: "../outside.png" }]);
});
