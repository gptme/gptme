// Prerender every route to static HTML using the SSR build of src/entry-server.tsx.
// Runs after `vite build` (client assets + dist/index.html template) and
// `vite build --ssr` (dist-ssr/entry-server.js).
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = process.cwd();
const dist = resolve(root, "dist");
const ssrDir = resolve(root, "dist-ssr");

const template = readFileSync(join(dist, "index.html"), "utf8");
for (const marker of ["<!--app-head-->", "<!--app-html-->"]) {
  if (!template.includes(marker)) throw new Error(`dist/index.html is missing ${marker}`);
}

const { routes, render } = await import(pathToFileURL(join(ssrDir, "entry-server.js")).href);

for (const route of routes) {
  const { head, html } = await render(route.path);
  // Function replacers: page HTML may contain `$` sequences.
  const page = template.replace("<!--app-head-->", () => head).replace("<!--app-html-->", () => html);
  const out = join(dist, route.file);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, page);
  console.log(`prerendered ${route.path} -> dist/${route.file} (${(page.length / 1024).toFixed(1)} KiB)`);
}

// Guard against shipping an empty SPA shell.
const home = readFileSync(join(dist, "index.html"), "utf8");
if (!home.includes("An AI agent that lives in your terminal")) {
  console.error("prerender check failed: dist/index.html has no hero content");
  process.exit(1);
}

rmSync(ssrDir, { recursive: true, force: true });
