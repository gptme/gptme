// Full-page navigation, isolated so tests can mock it.
//
// jsdom 26 makes window.location unforgeable and does not implement
// navigation: assigning window.location.href in a test is silently dropped
// (and logged as "Not implemented: navigation"), so tests cannot observe it
// through window.location. Mocking this module is the observable seam.
export function navigateTo(href: string): void {
  window.location.href = href;
}
