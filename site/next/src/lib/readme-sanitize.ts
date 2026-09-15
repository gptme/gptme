// Browser-safe README HTML sanitizer. Kept off readme.ts so the /readme/
// React component can sanitize at the dangerouslySetInnerHTML boundary
// without pulling node:fs into a future client bundle.
import sanitizeHtml from "sanitize-html";

const SAFE_HREF = /^(https?:|mailto:|#)/i;
const SAFE_SRC = /^https?:/i;

function keepSafeHref(href: string | undefined): string | undefined {
  if (!href) return undefined;
  const trimmed = href.trim();
  return SAFE_HREF.test(trimmed) ? trimmed : undefined;
}

function keepSafeSrc(src: string | undefined): string | undefined {
  if (!src) return undefined;
  const trimmed = src.trim();
  return SAFE_SRC.test(trimmed) ? trimmed : undefined;
}

function keepSafeSrcset(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const kept = value
    .split(",")
    .map((part) => {
      const trimmed = part.trim();
      if (!trimmed) return "";
      const [url, ...rest] = trimmed.split(/\s+/);
      return keepSafeSrc(url) ? [url, ...rest].join(" ") : "";
    })
    .filter(Boolean)
    .join(", ");
  return kept || undefined;
}

const README_SANITIZE: sanitizeHtml.IOptions = {
  allowedTags: [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "em",
    "i",
    "strong",
    "del",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "div",
    "span",
    "details",
    "summary",
    "sup",
    "sub",
    "kbd",
    "abbr",
  ],
  allowedAttributes: {
    a: ["href", "title", "class", "aria-hidden", "tabindex"],
    img: ["src", "srcset", "alt", "title", "width", "height", "class"],
    h1: ["id", "class"],
    h2: ["id", "class"],
    h3: ["id", "class"],
    h4: ["id", "class"],
    h5: ["id", "class"],
    h6: ["id", "class"],
    div: ["class"],
    span: ["class"],
    code: ["class"],
    pre: ["class"],
    ul: ["class", "id"],
    ol: ["start", "type", "class"],
    li: ["value"],
    p: ["align", "class"],
    th: ["align", "colspan", "rowspan", "width"],
    td: ["align", "colspan", "rowspan", "width"],
    details: ["open"],
    abbr: ["title"],
  },
  allowedSchemes: ["http", "https", "mailto"],
  allowedSchemesByTag: { img: ["http", "https"] },
  allowedSchemesAppliedToAttributes: ["href", "src", "cite"],
  allowProtocolRelative: false,
  transformTags: {
    a: (tagName, attribs) => {
      const href = keepSafeHref(attribs.href);
      const next = { ...attribs };
      if (href) next.href = href;
      else delete next.href;
      return { tagName, attribs: next };
    },
    img: (tagName, attribs) => {
      const next = { ...attribs };
      const src = keepSafeSrc(attribs.src);
      const srcset = keepSafeSrcset(attribs.srcset);
      if (src) next.src = src;
      else delete next.src;
      if (srcset) next.srcset = srcset;
      else delete next.srcset;
      return { tagName, attribs: next };
    },
  },
};

/** Strip scripts, event handlers, and unsafe URL schemes from README HTML. */
export function sanitizeReadmeHtml(html: string): string {
  return sanitizeHtml(html, README_SANITIZE);
}
