/**
 * Old gptme.org *was* the README, so inbound links like
 * https://gptme.org/#installation need to land on /readme/.
 * Only redirect when the current page has no matching id, so future
 * homepage hashes keep working.
 */
export function readmeHashRedirect(
  path: string,
  hash: string,
  hasId: (id: string) => boolean,
): string | null {
  if ((path !== "/" && path !== "/index.html") || hash.length <= 1) {
    return null;
  }
  const id = hash.slice(1);
  let decoded = id;
  try {
    decoded = decodeURIComponent(id);
  } catch {
    /* keep the raw id */
  }
  if (hasId(id) || hasId(decoded)) return null;
  return `/readme/${hash}`;
}
