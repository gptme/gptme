/**
 * Focused tests for the server-disconnected banner in ConversationContent.
 *
 * The full component has many complex dependencies; we mock them heavily so
 * we can focus on verifying that the banner appears/disappears based on
 * connection state and demo mode.
 */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { ConversationContent } from '../ConversationContent';

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
      log: [],
      logdir: 'demo/test',
      name: 'Test',
      id: 'demo/test',
      logfile: 'demo/test',
      branches: {},
      workspace: '/demo',
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

  it('does not show removed-server banner in intentional demo mode', () => {
    mockIsDemoMode.mockReturnValue(true);
    render(<ConversationContent conversationId="demo/test" serverId="removed-server" />);
    expect(screen.queryByText(/server not connected/i)).toBeNull();
  });
});
