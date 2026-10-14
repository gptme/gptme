// Fail when built HTML/CSS references a same-origin file that is not in the
// output tree. `vite build` and prerender stay green when a font or image
// 404s (e.g. an inlined @import whose url() was not rebased), so the gap is
// otherwise only visible in a screenshot.
//
// Usage: node scripts/check-assets.mjs [dir]   (default: dist)
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const ATTR_RE = /\s(?:src|href|poster)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi;
const SRCSET_RE = /\ssrcset\s*=\s*(?:"([^"]*)"|'([^']*)')/gi;
const URL_RE = /url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]+))\s*\)/gi;
const IMPORT_RE = /@import\s+(?:"([^"]*)"|'([^']*)')/gi;
// CSS inside HTML: <style> blocks and style="" attributes, not page prose.
const STYLE_RE = /<style\b[^>]*>([\s\S]*?)<\/style>|\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/gi;

/** Same-origin file references in an HTML or CSS document. */
export function extractRefs(text, kind) {
  const refs = [];
  const take = (re, split = false, source = text) => {
    for (const m of source.matchAll(re)) {
      const value = m[1] ?? m[2] ?? m[3] ?? "";
      if (split) refs.push(...value.split(",").map((c) => c.trim().split(/\s+/)[0]));
      else refs.push(value.trim());
    }
  };
  let css = text;
  if (kind === "html") {
    take(ATTR_RE);
    take(SRCSET_RE, true);
    css = [...text.matchAll(STYLE_RE)].map((m) => m[1] ?? m[2] ?? m[3] ?? "").join("\n");
  }
  take(URL_RE, false, css);
  take(IMPORT_RE, false, css);
  return refs.filter((r) => r && !/^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(r));
}

/** File a reference from `fromFile` resolves to (null: the document itself). */
export function resolveRef(root, fromFile, ref) {
  let path = ref.split(/[?#]/)[0];
  if (!path) return null; // "?x" / "#x" alone point at the current document
  try {
    path = decodeURIComponent(path);
  } catch {
    // keep the raw path; a malformed escape will simply not exist
  }
  const target = path.startsWith("/") ? join(root, path) : resolve(dirname(fromFile), path);
  if (path.endsWith("/") || (existsSync(target) && statSync(target).isDirectory())) {
    return join(target, "index.html");
  }
  return target;
}

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
  );
}

/** Every unresolved reference under root as {file, ref}. */
export function findMissing(root) {
  root = resolve(root);
  const missing = [];
  for (const file of walk(root)) {
    const kind = file.endsWith(".html") ? "html" : file.endsWith(".css") ? "css" : null;
    if (!kind) continue;
    for (const ref of new Set(extractRefs(readFileSync(file, "utf8"), kind))) {
      const target = resolveRef(root, file, ref);
      // Outside the deployed tree counts as missing even if it exists locally.
      const escapes = target && relative(root, target).startsWith("..");
      if (target && (escapes || !existsSync(target))) missing.push({ file: relative(root, file), ref });
    }
  }
  return missing;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const root = process.argv[2] ?? "dist";
  const missing = findMissing(root);
  if (missing.length) {
    console.error(`asset check failed: ${missing.length} reference(s) missing from ${root}/`);
    for (const { file, ref } of missing) console.error(`  ${file}: ${ref}`);
    process.exit(1);
  }
  console.log(`asset check: all same-origin references in ${root}/ resolve`);
}
