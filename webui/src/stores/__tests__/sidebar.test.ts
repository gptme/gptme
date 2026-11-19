import type * as Sidebar from '../sidebar';

describe('conversations sidebar preference', () => {
  beforeEach(() => {
    jest.resetModules();
    localStorage.clear();
  });

  function sidebar() {
    // Reload the module to simulate reopening the app.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require('../sidebar') as typeof Sidebar;
  }

  it('defaults to expanded and persists both collapse and expand', () => {
    const state = sidebar();
    expect(state.leftSidebarCollapsed$.get()).toBe(false);
    state.leftSidebarCollapsed$.set(true);
    expect(localStorage.getItem('gptme-conversations-collapsed')).toBe('true');
    state.leftSidebarCollapsed$.set(false);
    expect(localStorage.getItem('gptme-conversations-collapsed')).toBe('false');
  });

  it('restores a collapsed preference without opening the mobile sheet', () => {
    localStorage.setItem('gptme-conversations-collapsed', 'true');
    const state = sidebar();
    expect(state.leftSidebarCollapsed$.get()).toBe(true);
    expect(state.leftSidebarVisible$.get()).toBe(false);
    state.leftSidebarVisible$.set(true);
    state.leftSidebarVisible$.set(false);
    expect(localStorage.getItem('gptme-conversations-collapsed')).toBe('true');
  });
});
