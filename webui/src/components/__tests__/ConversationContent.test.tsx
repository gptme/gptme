/**
 * Focused tests for the server-disconnected banner in ConversationContent.
 *
 * The full component has many complex dependencies; we mock them heavily so
 * we can focus on verifying that the banner appears/disappears based on
 * connection state and demo mode.
 */
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import type { Message } from '@/types/conversation';
import { ConversationContent } from '../ConversationContent';

// Control how many items the virtualizer "renders" (simulates viewport size).
// Defaults to Infinity so existing tests see all messages (no change in behaviour).
// Set __virtualWindow to a number in specific tests to verify bounded rendering.
declare global {
  var __virtualWindow: number;
}
global.__virtualWindow = Infinity;
const mockScrollToIndex = jest.fn();

jest.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (opts: { count: number }) => {
    const win = isFinite(global.__virtualWindow) ? global.__virtualWindow : opts.count;
    const items = Array.from({ length: Math.min(opts.count, win) }, (_, i) => ({
      index: i,
      key: i,
      start: i * 150,
      size: 150,
      end: (i + 1) * 150,
      lane: 0,
    }));
    return {
      getTotalSize: () => opts.count * 150,
      getVirtualItems: () => items,
      scrollToIndex: mockScrollToIndex,
      measureElement: () => {},
    };
  },
}));

const mockBuildStepRoles = jest.fn((_messages: Message[]) => new Map());
let mockStepRoles = new Map<number, { type: 'grouped'; groupId: number }>();

jest.mock('@/utils/stepGrouping', () => ({
  ...jest.requireActual('@/utils/stepGrouping'),
  buildStepRoles: (messages: Message[]) => {
    mockBuildStepRoles(messages);
    return mockStepRoles;
  },
}));

// --- Mocks ---

const mockNavigate = jest.fn();
const mockConnect = jest.fn();
const mockCheckConnection = jest.fn().mockResolvedValue(true);
const mockIsDemoMode = jest.fn(() => false);
const mockIsLikelyChromeCorsPna = jest.fn((_url: string) => false);

const isConnected$ = observable(true);
const lastConnectionResult$ = observable<null | {
  ok: false;
  url: string;
  reason: 'network' | 'http_error' | 'parse_error' | 'timeout' | 'cors';
  message: string;
}>(null);
const sessions$ = observable(new Map<string, string>());

// Secondary server observables for serverId tests
const secondaryIsConnected$ = observable(true);
const secondaryLastConnectionResult$ = observable<null | {
  ok: false;
  url: string;
  reason: 'network' | 'http_error' | 'parse_error' | 'timeout' | 'cors';
  message: string;
}>(null);
const secondaryCheckConnection = jest.fn().mockResolvedValue(true);

// Minimal ConversationState observable for the component to reach the banner
function makeConversationState() {
  return observable({
    loadError: null,
    data: {
      log: [] as Message[],
      logdir: 'demo/test',
      name: 'Test',
      id: 'demo/test',
      logfile: 'demo/test',
      branches: {},
      workspace: '/demo',
      agent: {},
    },
    connectionStatus: 'connected',
    reconnectAttempt: null,
    reconnectMaxAttempts: null,
    reconnectRetryInMs: null,
    reconnectRetryStartedAt: null,
    connectionError: null,
    hasMoreBefore: false,
    isConnected: true,
    isGenerating: false,
    pendingTool: null,
    executingTool: null,
    lastCompletedTool: null,
    showInitialSystem: false,
    chatConfig: null,
    needsInitialStep: false,
    currentBranch: 'main',
    logRevision: 0,
    logOffset: 0,
    isWindowHydrated: true,
    lastMessage: undefined,
    maxTokens: undefined,
    temperature: undefined,
    topP: undefined,
  });
}

const mockConversation$ = makeConversationState();

// Allow tests to control which server IDs are "registered" in the server registry.
const mockGetClientForServer = jest.fn((serverId?: string) => {
  if (serverId === 'secondary-server') {
    return {
      isConnected$: secondaryIsConnected$,
      lastConnectionResult$: secondaryLastConnectionResult$,
      checkConnection: secondaryCheckConnection,
    };
  }
  return null; // unregistered / removed server
});

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: (serverId?: string) => mockGetClientForServer(serverId),
}));

jest.mock('@/utils/api', () => ({
  isLikelyChromeCorsPna: (url: string) => mockIsLikelyChromeCorsPna(url),
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [new URLSearchParams(), jest.fn()],
  };
});

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      isConnected$,
      lastConnectionResult$,
      sessions$,
      authHeader: null,
      getUserInfo: jest.fn().mockResolvedValue({}),
      step: jest.fn(),
      sendMessage: jest.fn(),
      subscribeToEvents: jest.fn(),
      cancelPendingRequests: jest.fn(),
      getConversation: jest.fn(),
      getConversations: jest.fn(),
      checkConnection: mockCheckConnection,
    },
    isConnected$,
    connect: mockConnect,
    getClient: (serverId?: string) => {
      if (serverId === 'secondary-server') {
        return {
          isConnected$: secondaryIsConnected$,
          lastConnectionResult$: secondaryLastConnectionResult$,
          checkConnection: secondaryCheckConnection,
        };
      }
      // Unknown server ID falls back to primary (matches ApiContext behavior)
      return {
        isConnected$,
        lastConnectionResult$,
        checkConnection: mockCheckConnection,
      };
    },
    connectionConfig: {
      baseUrl: 'http://localhost:5700',
      authToken: null,
      useAuthToken: false,
    },
  }),
}));

jest.mock('@/contexts/SettingsContext', () => ({
  useSettings: () => ({
    settings: {
      showHiddenMessages: false,
      showInitialSystem: false,
      blocksDefaultOpen: true,
    },
    updateSettings: jest.fn(),
  }),
}));

jest.mock('@/hooks/useModels', () => ({
  useModels: () => ({ defaultModel: undefined }),
}));

jest.mock('@/hooks/useConversation', () => ({
  useConversation: () => ({
    conversation$: mockConversation$,
    retryLoad: jest.fn(),
    sendMessage: jest.fn(),
    retryMessage: jest.fn(),
    editMessage: jest.fn(),
    deleteMessage: jest.fn(),
    rerunFromMessage: jest.fn(),
    regenerateMessage: jest.fn(),
    forkConversation: jest.fn(),
    switchBranch: jest.fn(),
    confirmTool: jest.fn(),
    interruptGeneration: jest.fn(),
    isLoadingOlderMessages: false,
    loadOlderMessages: jest.fn(),
  }),
}));

// Heavy component deps that aren't under test — stub to avoid rendering complexity
jest.mock('../ChatInput', () => ({
  ChatInput: () => <div data-testid="chat-input" />,
}));

jest.mock('../ChatMessage', () => ({
  ChatMessage: () => <div data-testid="chat-message" />,
}));

jest.mock('../OpenConversationPathButton', () => ({
  OpenConversationPathButton: () => null,
}));

jest.mock('../BranchIndicator', () => ({
  BranchIndicator: () => null,
}));

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ cancelQueries: jest.fn(), invalidateQueries: jest.fn() }),
}));

// --- Tests ---

function renderComponent() {
  return render(<ConversationContent conversationId="demo/test" />);
}

function message(role: Message['role'], content: string): Message {
  return { role, content };
}

describe('step role recomputation', () => {
  beforeEach(() => {
    mockConversation$.set(makeConversationState().peek());
    mockBuildStepRoles.mockClear();
    mockStepRoles = new Map();
  });

  it('does not recompute roles for streamed content updates', () => {
    mockConversation$.data.log.set([
      message('user', 'Write code'),
      message('assistant', 'Working'),
      message('system', 'Saved file'),
      message('assistant', 'Initial response'),
    ]);

    const view = renderComponent();
    expect(mockBuildStepRoles).toHaveBeenCalledTimes(1);

    act(() => {
      mockConversation$.data.log[3].content.set('Initial response plus streamed token');
    });

    expect(mockBuildStepRoles).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it('recomputes roles when a message is added', () => {
    mockConversation$.data.log.set([message('user', 'Write code')]);

    const view = renderComponent();
    expect(mockBuildStepRoles).toHaveBeenCalledTimes(1);

    act(() => {
      mockConversation$.data.log.push(message('assistant', 'Working'));
    });

    expect(mockBuildStepRoles).toHaveBeenCalledTimes(2);
    view.unmount();
  });
});

describe('server disconnected banner', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockIsDemoMode.mockReturnValue(false);
    isConnected$.set(true);
    lastConnectionResult$.set(null);
  });

  it('is hidden when connected', () => {
    isConnected$.set(true);
    renderComponent();
    expect(screen.queryByText(/server not connected/i)).toBeNull();
  });

  it('shows when disconnected and not in demo mode', () => {
    isConnected$.set(false);
    renderComponent();
    expect(screen.getByText(/server not connected/i)).toBeInTheDocument();
  });

  it('is hidden when disconnected but in intentional demo mode', () => {
    isConnected$.set(false);
    mockIsDemoMode.mockReturnValue(true);
    renderComponent();
    expect(screen.queryByText(/server not connected/i)).toBeNull();
  });

  it('shows CORS guidance when the failure reason is cors', () => {
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://localhost:5700',
      reason: 'cors',
      message: 'CORS error',
    });
    renderComponent();
    expect(screen.getByText(/--cors-origin/i)).toBeInTheDocument();
  });

  it('shows network guidance when the failure reason is network', () => {
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://localhost:5700',
      reason: 'network',
      message: 'Network error',
    });
    renderComponent();
    expect(screen.getByText(/check that it is running/i)).toBeInTheDocument();
  });

  it('shows timeout guidance when the failure reason is timeout', () => {
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://localhost:5700',
      reason: 'timeout',
      message: 'Timeout',
    });
    renderComponent();
    expect(screen.getByText(/timed out/i)).toBeInTheDocument();
  });

  it('shows a Retry button that calls connect()', async () => {
    isConnected$.set(false);
    renderComponent();
    const btn = screen.getByRole('button', { name: /retry/i });
    btn.click();
    expect(mockConnect).toHaveBeenCalled();
  });
});

describe('server disconnected banner — serverId (secondary server)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockIsDemoMode.mockReturnValue(false);
    // Primary is always connected in these tests
    isConnected$.set(true);
    lastConnectionResult$.set(null);
    // Secondary starts connected
    secondaryIsConnected$.set(true);
    secondaryLastConnectionResult$.set(null);
  });

  it('is hidden when the secondary server is connected (primary irrelevant)', () => {
    secondaryIsConnected$.set(true);
    render(<ConversationContent conversationId="demo/test" serverId="secondary-server" />);
    expect(screen.queryByText(/server not connected/i)).toBeNull();
  });

  it('shows when the secondary server is disconnected, even if primary is connected', () => {
    secondaryIsConnected$.set(false);
    render(<ConversationContent conversationId="demo/test" serverId="secondary-server" />);
    expect(screen.getByText(/server not connected/i)).toBeInTheDocument();
  });

  it('Retry calls checkConnection() on the secondary server, not connect()', async () => {
    secondaryIsConnected$.set(false);
    render(<ConversationContent conversationId="demo/test" serverId="secondary-server" />);
    const btn = screen.getByRole('button', { name: /retry/i });
    btn.click();
    expect(secondaryCheckConnection).toHaveBeenCalled();
    expect(mockConnect).not.toHaveBeenCalled();
  });
});

describe('server disconnected banner — removed server', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockIsDemoMode.mockReturnValue(false);
    // Primary is connected
    isConnected$.set(true);
    lastConnectionResult$.set(null);
    // Simulate a removed server: getClientForServer returns null for 'removed-server'
    mockGetClientForServer.mockImplementation((serverId?: string) => {
      if (serverId === 'secondary-server') {
        return {
          isConnected$: secondaryIsConnected$,
          lastConnectionResult$: secondaryLastConnectionResult$,
          checkConnection: secondaryCheckConnection,
        };
      }
      return null;
    });
  });

  it('shows banner when serverId refers to a server no longer in the registry', () => {
    render(<ConversationContent conversationId="demo/test" serverId="removed-server" />);
    expect(screen.getByText(/server not connected/i)).toBeInTheDocument();
    expect(screen.getByText(/no longer registered/i)).toBeInTheDocument();
  });

  it('hides Retry button when server is no longer in registry', () => {
    render(<ConversationContent conversationId="demo/test" serverId="removed-server" />);
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });

  it('does not show removed-server banner in intentional demo mode', () => {
    mockIsDemoMode.mockReturnValue(true);
    render(<ConversationContent conversationId="demo/test" serverId="removed-server" />);
    expect(screen.queryByText(/server not connected/i)).toBeNull();
  });
});

describe('virtual message list', () => {
  beforeEach(() => {
    mockConversation$.set(makeConversationState().peek());
    jest.clearAllMocks();
    mockIsDemoMode.mockReturnValue(false);
    isConnected$.set(true);
    lastConnectionResult$.set(null);
    global.__virtualWindow = Infinity; // reset to "render all" default
    mockStepRoles = new Map();
  });

  afterEach(() => {
    global.__virtualWindow = Infinity;
  });

  it('renders only a bounded subset of messages when the virtualizer window is smaller than the list', () => {
    // Simulate a viewport that fits 8 items — the rest stay off-screen (no DOM node).
    global.__virtualWindow = 8;
    const many = Array.from({ length: 50 }, (_, i) =>
      message(i === 0 ? 'user' : 'assistant', `Message ${i}`)
    );
    act(() => {
      mockConversation$.data.log.set(many);
    });
    renderComponent();

    const rendered = document.querySelectorAll('[data-message-index]');
    expect(rendered.length).toBe(8);
    expect(rendered.length).toBeLessThan(50);
  });

  it('renders all messages when the list is smaller than the virtualizer window', () => {
    // With __virtualWindow = Infinity the mock returns all items (min(3, ∞) = 3).
    const few = [
      message('user', 'Hello'),
      message('assistant', 'Hi there'),
      message('user', 'Thanks'),
    ];
    act(() => {
      mockConversation$.data.log.set(few);
    });
    renderComponent();

    const rendered = document.querySelectorAll('[data-message-index]');
    expect(rendered.length).toBe(3);
  });

  it('excludes hidden messages from virtualizer geometry', () => {
    act(() => {
      mockConversation$.data.log.set([
        message('system', 'Initial prompt'),
        { ...message('assistant', 'Hidden lesson'), hide: true },
        message('user', 'Visible question'),
        message('assistant', 'Visible answer'),
      ]);
    });
    renderComponent();

    expect(document.querySelectorAll('[data-index]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-message-index]')).toHaveLength(2);
  });

  it('excludes grouped messages when their group is collapsed', () => {
    mockStepRoles = new Map([[1, { type: 'grouped', groupId: 0 }]]);
    act(() => {
      mockConversation$.data.log.set([
        message('user', 'Question'),
        message('system', 'Tool result'),
        message('assistant', 'Answer'),
      ]);
    });
    renderComponent();

    expect(document.querySelectorAll('[data-index]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-message-index]')).toHaveLength(2);
  });

  it('retries search highlighting until a virtual row mounts', () => {
    jest.useFakeTimers();
    global.__virtualWindow = 1;
    act(() => {
      mockConversation$.data.log.set([
        message('user', 'First'),
        message('assistant', 'Find this target'),
      ]);
    });
    renderComponent();

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', ctrlKey: true }));
    });
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'target' } });

    expect(mockScrollToIndex).toHaveBeenCalledWith(1, { align: 'center' });
    act(() => jest.advanceTimersByTime(16 * 10));
    jest.useRealTimers();
  });

  it('assigns data-index to rendered wrappers (required for ResizeObserver height measurement)', () => {
    act(() => {
      mockConversation$.data.log.set([message('user', 'Hello'), message('assistant', 'Hi')]);
    });
    renderComponent();

    const items = document.querySelectorAll('[data-index]');
    expect(items.length).toBeGreaterThan(0);
    items.forEach((item) => {
      expect(item.getAttribute('data-index')).not.toBeNull();
    });
  });

  it('mounts without errors when there are no messages', () => {
    act(() => {
      mockConversation$.data.log.set([]);
    });
    expect(() => renderComponent()).not.toThrow();
  });
});
