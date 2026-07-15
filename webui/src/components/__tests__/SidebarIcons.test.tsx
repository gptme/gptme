import '@testing-library/jest-dom';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { SidebarIcons } from '../SidebarIcons';

// Mock stores
jest.mock('@/stores/sidebar', () => ({
  leftSidebarCollapsed$: { get: jest.fn(() => false) },
  toggleLeftSidebarCollapsed: jest.fn(),
}));

jest.mock('@/stores/commandPalette', () => ({
  commandPaletteOpen$: { set: jest.fn() },
}));

// Mock useProviderHealth to avoid ApiContext dependency
jest.mock('@/hooks/useProviderHealth', () => ({
  useProviderHealth: () => ({
    data: null,
    isLoading: false,
    error: null,
    refresh: jest.fn(),
  }),
}));

// Mock SettingsModal to avoid complex context requirements
jest.mock('../SettingsModal', () => ({
  SettingsModal: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock Legend State's use$ to return false (not collapsed)
jest.mock('@legendapp/state/react', () => ({
  use$: jest.fn(() => false),
}));

// localStorage mock
const mockLocalStorage = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(window, 'localStorage', { value: mockLocalStorage });

// Helper: set window width and trigger resize
const setWindowWidth = (width: number) => {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  fireEvent(window, new Event('resize'));
};

const renderSidebar = () =>
  render(
    <BrowserRouter>
      <SidebarIcons tasks={[]} />
    </BrowserRouter>
  );

describe('SidebarIcons', () => {
  beforeEach(() => {
    mockLocalStorage.clear();
    window.history.replaceState(null, '', '/chat');
    // Default to lg screen
    Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true });
  });

  it('renders navigation items', () => {
    renderSidebar();
    expect(
      screen.getByRole('button', { name: /toggle conversations sidebar/i })
    ).toBeInTheDocument();
    expect(screen.getByTestId('toggle-conversations-sidebar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /agents/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /tasks/i })).toBeInTheDocument();
  });

  it('preserves demo mode when navigating between top-level sections', () => {
    window.history.replaceState(null, '', '/chat?demo=1');
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: /agents/i }));

    expect(window.location.pathname).toBe('/agents');
    expect(window.location.search).toBe('?demo=1');
  });

  it('starts expanded on lg+ screens when no preference stored', () => {
    renderSidebar();
    const sidebar = screen.getByTestId('nav-sidebar');
    expect(sidebar).toHaveAttribute('data-expanded', 'true');
    expect(sidebar).toHaveClass('w-44');
  });

  it('starts collapsed on small screens regardless of preference', () => {
    Object.defineProperty(window, 'innerWidth', { value: 800, configurable: true });
    renderSidebar();
    const sidebar = screen.getByTestId('nav-sidebar');
    expect(sidebar).toHaveAttribute('data-expanded', 'false');
    expect(sidebar).toHaveClass('w-11');
  });

  it('respects stored expanded preference on lg+ screen', () => {
    mockLocalStorage.setItem('nav-sidebar-expanded', 'false');
    renderSidebar();
    const sidebar = screen.getByTestId('nav-sidebar');
    expect(sidebar).toHaveAttribute('data-expanded', 'false');
    expect(sidebar).toHaveClass('w-11');
  });

  it('collapses when toggle button is clicked', () => {
    renderSidebar();
    const toggle = screen.getByTestId('toggle-nav-sidebar');
    fireEvent.click(toggle);
    const sidebar = screen.getByTestId('nav-sidebar');
    expect(sidebar).toHaveAttribute('data-expanded', 'false');
    expect(mockLocalStorage.getItem('nav-sidebar-expanded')).toBe('false');
  });

  it('expands when toggle button is clicked while collapsed', () => {
    mockLocalStorage.setItem('nav-sidebar-expanded', 'false');
    renderSidebar();
    const toggle = screen.getByTestId('toggle-nav-sidebar');
    fireEvent.click(toggle);
    const sidebar = screen.getByTestId('nav-sidebar');
    expect(sidebar).toHaveAttribute('data-expanded', 'true');
    expect(mockLocalStorage.getItem('nav-sidebar-expanded')).toBe('true');
  });

  it('auto-collapses on resize below lg breakpoint', () => {
    renderSidebar();
    expect(screen.getByTestId('nav-sidebar')).toHaveAttribute('data-expanded', 'true');

    act(() => setWindowWidth(800));

    expect(screen.getByTestId('nav-sidebar')).toHaveAttribute('data-expanded', 'false');
  });

  it('auto-expands on resize to lg+ if preference is expanded', () => {
    Object.defineProperty(window, 'innerWidth', { value: 800, configurable: true });
    renderSidebar();
    expect(screen.getByTestId('nav-sidebar')).toHaveAttribute('data-expanded', 'false');

    act(() => setWindowWidth(1280));

    expect(screen.getByTestId('nav-sidebar')).toHaveAttribute('data-expanded', 'true');
  });

  it('does not expand on resize to lg+ if preference is collapsed', () => {
    mockLocalStorage.setItem('nav-sidebar-expanded', 'false');
    Object.defineProperty(window, 'innerWidth', { value: 800, configurable: true });
    renderSidebar();

    act(() => setWindowWidth(1280));

    // User set pref to collapsed, so should stay collapsed even on lg+
    expect(screen.getByTestId('nav-sidebar')).toHaveAttribute('data-expanded', 'false');
  });

  it('shows labels (non-zero opacity) when expanded', () => {
    renderSidebar();
    const chatLabel = screen
      .getByTestId('toggle-conversations-sidebar')
      .querySelector('span.flex-1');
    expect(chatLabel).toHaveClass('opacity-100');
  });

  it('toggle on medium viewport (768-1023px) flips preference and stores it', () => {
    // Start on lg+ with expanded preference, then shrink to medium
    renderSidebar();
    act(() => setWindowWidth(900));
    const sidebar = screen.getByTestId('nav-sidebar');
    // Auto-collapsed on medium viewport
    expect(sidebar).toHaveAttribute('data-expanded', 'false');

    // Click collapse → should set prefExpanded to false (not a no-op)
    const toggle = screen.getByTestId('toggle-nav-sidebar');
    fireEvent.click(toggle);
    expect(mockLocalStorage.getItem('nav-sidebar-expanded')).toBe('false');

    // Click again → should toggle prefExpanded back to true
    fireEvent.click(toggle);
    expect(mockLocalStorage.getItem('nav-sidebar-expanded')).toBe('true');
  });

  it('hides labels (zero opacity) when collapsed', () => {
    mockLocalStorage.setItem('nav-sidebar-expanded', 'false');
    renderSidebar();
    const chatLabel = screen
      .getByTestId('toggle-conversations-sidebar')
      .querySelector('span.flex-1');
    expect(chatLabel).toHaveClass('opacity-0');
  });
});
