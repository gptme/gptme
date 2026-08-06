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
import type { ExecutingTool } from '@/stores/conversations';
import { ConversationContent } from '../ConversationContent';

// Control how many items the virtualizer "renders" (simulates viewport size).
// Defaults to Infinity so existing tests see all messages (no change in behaviour).
// Set __virtualWindow to a number in specific tests to verify bounded rendering.
declare global {
  var __virtualWindow: number;
}
global.__virtualWindow = Infinity;
const mockScrollToIndex = jest.fn();

// Track the count passed to the virtualizer so tests can assert that
// hidden/system/collapsed messages are excluded from the virtualizer geometry.
let lastVirtualizerCount = 0;

jest.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (opts: { count: number }) => {
    lastVirtualizerCount = opts.count;
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
    executingTool: null as ExecutingTool | null,
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

  it('passes only visible message count to the virtualizer (regression guard for #3379)', () => {
    // 10 total messages: 2 hidden + 1 initial-system (hidden by default) + 7 visible.
    // The virtualizer count must equal 7, not 10 — hidden messages must not
    // reserve geometry slots (they would create blank scroll regions).
    act(() => {
      mockConversation$.data.log.set([
        message('system', 'Initial system prompt'),
        message('user', 'Question 1'),
        message('assistant', 'Answer 1'),
        { ...message('system', 'Hidden tool result'), hide: true },
        message('assistant', 'Answer 1 continued'),
        message('user', 'Question 2'),
        { ...message('assistant', 'Hidden draft'), hide: true },
        message('assistant', 'Answer 2'),
        message('user', 'Question 3'),
        message('assistant', 'Answer 3'),
      ]);
    });
    renderComponent();

    // 10 total - 1 initial system - 2 hidden = 7 visible
    expect(lastVirtualizerCount).toBe(7);
  });
});

// ─── Scroll stability during tool execution (gptme#3440) ────────────────────
//
// When InlineToolExecution renders below the virtualizer it adds height outside
// the virtual list.  scrollToBottom must:
//   (a) call scrollToIndex to ensure the last virtual item is rendered, AND
//   (b) set container.scrollTop = scrollHeight - clientHeight so the card
//       itself is also in view.
//
// If (b) is skipped the onScroll handler sees the container is not at the real
// bottom and sets autoScrollAborted = true, silently killing auto-scroll for
// the remainder of the tool run.
describe('scroll stability during tool execution (gptme#3440)', () => {
  const makeExecutingTool = () => ({
    id: 'tool-1',
    tooluse: { tool: 'shell', args: [], content: 'ls -la' },
    startedAt: Date.now(),
    partialOutput: '',
  });

  beforeEach(() => {
    mockConversation$.set(makeConversationState().peek());
    jest.clearAllMocks();
    mockScrollToIndex.mockClear();
    mockIsDemoMode.mockReturnValue(false);
    isConnected$.set(true);
    lastConnectionResult$.set(null);
  });

  it('InlineToolExecution renders when executingTool$ is set', () => {
    act(() => {
      mockConversation$.executingTool.set(makeExecutingTool());
    });
    renderComponent();
    // The card header text appears when executingTool is set
    expect(screen.getByText(/running/i)).toBeInTheDocument();
    expect(screen.getByText(/shell/i)).toBeInTheDocument();
  });

  it('InlineToolExecution is absent when executingTool$ is null', () => {
    renderComponent();
    // The compact "Running <tool>" header must not appear without an executing tool
    expect(screen.queryByRole('code', { name: /shell/i })).toBeNull();
  });

  it('scrollToIndex is called when executingTool$ transitions null → set', () => {
    jest.useFakeTimers();

    mockConversation$.data.log.set([message('user', 'Run it'), message('assistant', 'Sure')]);
    renderComponent();
    mockScrollToIndex.mockClear();

    act(() => {
      mockConversation$.executingTool.set(makeExecutingTool());
    });
    // Advance time enough for nested rAFs to fire (≤16 ms each) without
    // triggering ElapsedTimer's 100ms setInterval which would loop infinitely
    // with runAllTimers().
    act(() => {
      jest.advanceTimersByTime(50);
    });

    expect(mockScrollToIndex).toHaveBeenCalledWith(
      expect.any(Number),
      expect.objectContaining({ align: 'end' })
    );

    jest.useRealTimers();
  });

  it('container.scrollTop is set to scrollHeight - clientHeight after scrollToIndex', () => {
    jest.useFakeTimers();

    mockConversation$.data.log.set([message('user', 'hello'), message('assistant', 'world')]);
    const { getByTestId } = renderComponent();

    const viewport = getByTestId('message-scroll-viewport');
    // Simulate a container taller than the virtualizer alone.
    // Real-world case: virtualizer = ~300px, InlineToolExecution card = ~300px extra.
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, get: () => 600 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, get: () => 400 });

    let capturedScrollTop: number | undefined;
    Object.defineProperty(viewport, 'scrollTop', {
      configurable: true,
      set(v: number) {
        capturedScrollTop = v;
      },
      get() {
        return capturedScrollTop ?? 0;
      },
    });

    act(() => {
      mockConversation$.executingTool.set(makeExecutingTool());
    });
    // Advance past nested rAFs (3 levels × ≤16 ms each), stay under 100ms
    // to avoid triggering ElapsedTimer's setInterval.
    act(() => {
      jest.advanceTimersByTime(50);
    });

    // scrollHeight(600) - clientHeight(400) = 200
    expect(capturedScrollTop).toBe(200);

    jest.useRealTimers();
  });

  it('overlapping scroll cycles: newer cycle is not interrupted by older cleanup', () => {
    // Regression test for Greptile P1: when two scrollToBottom cycles overlap,
    // the older cycle's inner rAF must NOT clear isAutoScrolling$ while the
    // newer cycle is still in-flight.  Without the generation guard the onScroll
    // handler would see isAutoScrolling$=false mid-programmatic-scroll and set
    // autoScrollAborted=true, killing auto-scroll for the rest of the tool run.
    jest.useFakeTimers();

    mockConversation$.data.log.set([message('user', 'hello'), message('assistant', 'world')]);
    const { getByTestId } = renderComponent();

    const viewport = getByTestId('message-scroll-viewport');
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, get: () => 600 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, get: () => 400 });
    let capturedScrollTop: number | undefined;
    Object.defineProperty(viewport, 'scrollTop', {
      configurable: true,
      set(v: number) {
        capturedScrollTop = v;
      },
      get() {
        return capturedScrollTop ?? 0;
      },
    });

    // Fire executingTool twice in the same rAF window so their inner cleanup
    // rAFs can interleave.  The second call (gen=2) must survive; the first
    // call's cleanup (gen=1) must be a no-op.
    act(() => {
      mockConversation$.executingTool.set(makeExecutingTool());
    });
    act(() => {
      mockConversation$.executingTool.set({ ...makeExecutingTool(), id: 'tool-2' });
    });

    // Advance past all nested rAFs; stay under 100ms to avoid ElapsedTimer loop.
    act(() => {
      jest.advanceTimersByTime(50);
    });

    // scrollToIndex must have been called for both executingTool transitions
    // (plus initial renders — exact count is intentionally not checked here)
    expect(mockScrollToIndex).toHaveBeenCalledWith(
      expect.any(Number),
      expect.objectContaining({ align: 'end' })
    );
    // Final scrollTop must reflect the most recent programmatic scroll —
    // the key regression guard: the older cycle's cleanup must not have
    // prematurely cleared isAutoScrolling$ while the newer cycle's rAF was
    // still pending, which would let onScroll abort auto-scroll.
    expect(capturedScrollTop).toBe(200);

    jest.useRealTimers();
  });

  it('honours user scroll during isAutoScrolling window (Greptile P1 #3450)', () => {
    // When the user scrolls up during the two-rAF scrollToBottom window,
    // isAutoScrolling$ suppresses onScroll so autoScrollAborted$ is never set
    // by the scroll handler.  The second rAF must detect the position drift
    // and set autoScrollAborted$ itself before releasing the lock.
    //
    // We simulate the user scroll by intercepting the first programmatic
    // scrollTop assignment (rAF1 → 200) and returning a non-bottom value (50)
    // from subsequent reads — exactly as if the user scrolled between the two
    // rAFs.  rAF2 must see 600-50-400=150 > 1 and call autoScrollAborted$.set(true).
    jest.useFakeTimers();

    mockConversation$.data.log.set([message('user', 'hello'), message('assistant', 'world')]);
    const { getByTestId } = renderComponent();

    // Drain the initial-mount useEffect rAF (requestAnimationFrame(scrollToBottom))
    // before installing the scrollTop interceptor.  Without this, two concurrent
    // scrollToBottom cycles fire in the same advanceTimersByTime below — the
    // generation counter correctly kills the older cycle's cleanup but also
    // prevents the drift check from running for that cycle.
    act(() => {
      jest.advanceTimersByTime(50);
    });

    const viewport = getByTestId('message-scroll-viewport');
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, get: () => 600 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, get: () => 400 });

    // True bottom = scrollHeight(600) - clientHeight(400) = 200.
    // The interceptor treats the first programmatic scrollTop assignment
    // (rAF1 landing at 200) as an immediate user scroll to 50 — exactly the
    // position the user would land at if they scrolled up between the two rAFs.
    let programmaticSets = 0;
    let currentScrollTop = 200; // already at bottom after initial drain
    Object.defineProperty(viewport, 'scrollTop', {
      configurable: true,
      set(v: number) {
        programmaticSets++;
        currentScrollTop = programmaticSets === 1 ? 50 : v;
      },
      get() {
        return currentScrollTop;
      },
    });

    mockScrollToIndex.mockClear();
    act(() => {
      mockConversation$.executingTool.set(makeExecutingTool());
    });
    // Advance past all nested rAFs (outer + rAF1 + rAF2).
    act(() => {
      jest.advanceTimersByTime(50);
    });

    // rAF2 detected the drift (scrollTop=50 < true-bottom=200) and set
    // autoScrollAborted$.  Verify: a second executingTool transition must NOT
    // call scrollToIndex because the abort guard fires first.
    mockScrollToIndex.mockClear();
    act(() => {
      mockConversation$.executingTool.set({ ...makeExecutingTool(), id: 'tool-2' });
    });
    act(() => {
      jest.advanceTimersByTime(50);
    });

    expect(mockScrollToIndex).not.toHaveBeenCalled();

    jest.useRealTimers();
  });

  it('does not snap to bottom when user scrolled up before first rAF fires (Greptile P1 pre-rAF race)', () => {
    // Covers the pre-rAF1 window: user scrolls between scrollToBottom() setting
    // isAutoScrolling$=true (which blocks onScroll) and the first inner rAF that
    // would otherwise assign scrollTop = scrollHeight - clientHeight.
    //
    // Mechanism: scrollTopSnapshot is captured before scrollToIndex.  If scrollTop
    // drops below that snapshot by the time rAF1 fires, the scroll was user-initiated
    // (scrollToIndex never moves the viewport backward).  rAF1 sets autoScrollAborted$
    // and returns without snapping — the user's position is preserved.
    //
    // We simulate the user scroll via mockScrollToIndex: immediately after the
    // virtualizer call (still inside the isAutoScrolling$ lock), we set currentScrollTop
    // below the snapshot value (200 → 50).  rAF1 sees scrollTop=50 < snapshot=200
    // and must abort without assigning scrollTop.
    jest.useFakeTimers();

    mockConversation$.data.log.set([message('user', 'hello'), message('assistant', 'world')]);
    const { getByTestId } = renderComponent();
    act(() => {
      jest.advanceTimersByTime(50);
    });

    const viewport = getByTestId('message-scroll-viewport');
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, get: () => 600 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, get: () => 400 });

    // Start at true bottom (snapshot = 200).  scrollToIndex immediately drops
    // scrollTop to 50 — simulating the user scrolling up during the lock window.
    let snappedToBottom = false;
    let currentScrollTop = 200;
    Object.defineProperty(viewport, 'scrollTop', {
      configurable: true,
      set(v: number) {
        snappedToBottom = true;
        currentScrollTop = v;
      },
      get() {
        return currentScrollTop;
      },
    });

    mockScrollToIndex.mockImplementationOnce(() => {
      currentScrollTop = 50;
    });

    mockScrollToIndex.mockClear();
    act(() => {
      mockConversation$.executingTool.set(makeExecutingTool());
    });
    act(() => {
      jest.advanceTimersByTime(50);
    });

    // rAF1 detected backward drift (scrollTop=50 < snapshot=200) and aborted
    // before touching scrollTop — no snap should have occurred.
    expect(snappedToBottom).toBe(false);

    // autoScrollAborted$ must be set — a second transition must not auto-scroll.
    mockScrollToIndex.mockClear();
    act(() => {
      mockConversation$.executingTool.set({ ...makeExecutingTool(), id: 'tool-2' });
    });
    act(() => {
      jest.advanceTimersByTime(50);
    });
    expect(mockScrollToIndex).not.toHaveBeenCalled();

    jest.useRealTimers();
  });
});
