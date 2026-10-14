// Point window.location at `href` under Jest.
//
// jsdom 26 makes `location` unforgeable, so tests cannot stub it with
// Object.defineProperty. jest.environment.cjs exposes JSDOM#reconfigure as a
// global instead; this is the typed wrapper. Every URL-derived field
// (hostname, origin, pathname, search, ...) follows the new href, and the
// navigation methods stay jsdom's own: a stray `location.href = ...` logs
// "Not implemented: navigation" through the virtual console instead of
// throwing.
type ReconfigureGlobal = { __jsdomReconfigure?: (url: string) => void };

export function setLocation(href: string): void {
  const reconfigure = (globalThis as ReconfigureGlobal).__jsdomReconfigure;
  if (!reconfigure) {
    throw new Error('setLocation needs the jest.environment.cjs test environment');
  }
  reconfigure(new URL(href).href);
}
