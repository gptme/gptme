// Render the repository README.md at build time for the /readme page.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import GithubSlugger from "github-slugger";
import { Marked, type Tokens } from "marked";
import { sanitizeReadmeHtml } from "./readme-sanitize.ts";

export { sanitizeReadmeHtml } from "./readme-sanitize.ts";

// Resolved from the project root (site/next), which is the cwd for `vite` and
// the prerender step. import.meta.url is not usable here: at build time this
// module runs from the bundled SSR chunk in dist-ssr/.
const README_PATH = resolve(process.cwd(), "..", "..", "README.md");

const MEDIA_BASE = "https://gptme.org/media/";
const REPO_BLOB_BASE = "https://github.com/gptme/gptme/blob/master/";
const SAFE_ABSOLUTE = /^(https?:|mailto:)/i;

/**
 * Drop the centered HTML header at the top of the README (logo, title,
 * pronunciation, nav links, badges). The site already has its own header, and
 * the badge wall is noise here. Everything from the first Markdown content
 * after the badges block is kept.
 */
export function stripHeader(md: string): string {
  const badges = md.indexOf("<!-- Badges -->");
  if (badges !== -1) {
    const end = md.indexOf("</p>", badges);
    if (end !== -1) return md.slice(end + "</p>".length).trimStart();
  }
  // Fallback: strip leading HTML-only lines until the first Markdown line.
  const lines = md.split("\n");
  let i = 0;
  while (i < lines.length && (lines[i].trim() === "" || /^\s*</.test(lines[i]))) {
    i++;
  }
  return lines.slice(i).join("\n");
}

/** Rewrite a relative URL from the README so it works when served from the site. */
export function rewriteUrl(href: string): string {
  const trimmed = href.trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("#")) return trimmed;
  // Protocol-relative URLs are treated as https so they cannot inherit the page scheme.
  if (trimmed.startsWith("//")) return rewriteUrl(`https:${trimmed}`);
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) {
    return SAFE_ABSOLUTE.test(trimmed) ? trimmed : "";
  }
  const clean = trimmed.replace(/^\.\//, "").replace(/^\//, "");
  if (clean.startsWith("media/")) return MEDIA_BASE + clean.slice("media/".length);
  return REPO_BLOB_BASE + clean;
}

function rewriteSrcset(value: string): string {
  return value
    .split(",")
    .map((part) => {
      const trimmed = part.trim();
      if (!trimmed) return "";
      const [url, ...rest] = trimmed.split(/\s+/);
      return [rewriteUrl(url), ...rest].join(" ");
    })
    .filter(Boolean)
    .join(", ");
}

function rewriteHtmlUrls(html: string): string {
  return html.replace(
    /\b(src|href|srcset)=("|')([^"']*)\2/g,
    (_m, attr: string, q: string, url: string) => {
      const next = attr === "srcset" ? rewriteSrcset(url) : rewriteUrl(url);
      return `${attr}=${q}${next}${q}`;
    },
  );
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function renderReadme(md: string = readFileSync(README_PATH, "utf8")): string {
  const slugger = new GithubSlugger();
  const marked = new Marked({ gfm: true });
  marked.use({
    walkTokens(token) {
      if (token.type === "link" || token.type === "image") {
        const t = token as Tokens.Link | Tokens.Image;
        t.href = rewriteUrl(t.href);
      }
    },
    renderer: {
      heading(this: { parser: { parseInline(tokens: Tokens.Generic[]): string } }, { tokens, depth, text }: Tokens.Heading) {
        // GitHub-compatible ids so the README's own table of contents works.
        const id = escapeAttr(slugger.slug(text));
        const inner = this.parser.parseInline(tokens);
        return `<h${depth} id="${id}" class="group relative"><a class="absolute -left-[1.1em] font-normal text-muted opacity-0 group-hover:opacity-100" href="#${id}" aria-hidden="true" tabindex="-1">#</a>${inner}</h${depth}>\n`;
      },
    },
  });
  const html = marked.parse(stripHeader(md), { async: false }) as string;
  // Wide tables scroll inside their own container instead of the page.
  // Sanitize last so raw README HTML (script tags, javascript: links, event
  // handlers) cannot reach dangerouslySetInnerHTML on /readme/.
  return sanitizeReadmeHtml(
    rewriteHtmlUrls(html)
      .replaceAll("<table>", '<div class="overflow-x-auto"><table>')
      .replaceAll("</table>", "</table></div>"),
  );
}
