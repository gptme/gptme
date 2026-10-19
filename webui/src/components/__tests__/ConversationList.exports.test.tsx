import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import type { ConversationSummary, Message } from '@/types/conversation';
import { TooltipProvider } from '@/components/ui/tooltip';
import { MemoryRouter } from 'react-router-dom';
import { ConversationList } from '../ConversationList';
import { serverRegistry$ } from '@/stores/servers';
import { getClientGeneration } from '@/utils/api';

const mockGetConversation = jest.fn();
const mockGetClient = jest.fn(() => ({ getConversation: mockGetConversation }));
const mockExportMarkdown = jest.fn();
const mockExportJSON = jest.fn();
const mockReportDownloadOutcome = jest.fn();

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {},
    getClient: mockGetClient,
    connectionConfig: { baseUrl: 'http://localhost:5700', apiKey: '' },
    isConnected$: observable(true),
  }),
}));

jest.mock('sonner', () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}));

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}));

jest.mock('@/democonversations', () => ({
  demoConversations: [{ id: 'demo-1', name: 'Demo Chat' }],
  getDemoMessages: () => [],
}));

jest.mock('@/utils/exportConversation', () => {
  const actual = jest.requireActual('@/utils/exportConversation');
  return {
    ...actual,
    exportConversationAsMarkdown: (...args: unknown[]) => mockExportMarkdown(...args),
    exportConversationAsJSON: (...args: unknown[]) => mockExportJSON(...args),
    reportDownloadOutcome: (...args: unknown[]) => mockReportDownloadOutcome(...args),
  };
});

jest.mock('@/stores/conversations', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable } = require('@legendapp/state');
  return {
    conversations$: observable(new Map()),
    selectedConversation$: observable(''),
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { conversations$ } = require('@/stores/conversations');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { toast } = require('sonner');

const SERVER_ID = 'server-1';

const fullMessages = (): Message[] =>
  Array.from({ length: 120 }, (_, index) => ({
    role: index % 2 === 0 ? 'user' : 'assistant',
    content:
      index === 0 ? 'OLDEST-SENTINEL' : index === 119 ? 'NEWEST-SENTINEL' : `message-${index}`,
    timestamp: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString(),
  }));

function setLoadedConversation(
  messages: Message[],
  overrides: {
    hasMoreBefore?: boolean;
    name?: string;
    currentBranch?: string;
    streamClientGeneration?: number;
  } = {}
) {
  conversations$.set('conv-1', {
    data: {
      id: 'conv-1',
      name: overrides.name ?? 'Full history chat',
      log: messages,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    },
    isGenerating: false,
    isConnected: false,
    connectionStatus: 'connected',
    reconnectAttempt: null,
    reconnectMaxAttempts: null,
    reconnectRetryInMs: null,
    reconnectRetryStartedAt: null,
    connectionError: null,
    loadError: null,
    pendingTool: null,
    executingTool: null,
    lastCompletedTool: null,
    showInitialSystem: false,
    chatConfig: null,
    needsInitialStep: false,
    currentBranch: overrides.currentBranch ?? 'main',
    streamClientGeneration: overrides.streamClientGeneration,
    logRevision: 0,
    logOffset: 70,
    hasMoreBefore: overrides.hasMoreBefore ?? true,
    isWindowHydrated: true,
  });
}

const conv = (overrides: Partial<ConversationSummary> = {}): ConversationSummary => ({
  id: 'conv-1',
  name: 'Full history chat',
  messages: 120,
  modified: Date.now() / 1000,
  serverId: SERVER_ID,
  serverName: 'Local',
  readonly: false,
  ...overrides,
});

function renderList(summary: ConversationSummary = conv()) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ConversationList
          conversations={[summary]}
          onSelect={jest.fn()}
          fetchNextPage={jest.fn()}
        />
      </TooltipProvider>
    </MemoryRouter>
  );
}

async function openExport(kind: 'Markdown' | 'JSON') {
  fireEvent.contextMenu(screen.getByTestId('conversation-title'));
  fireEvent.click(await screen.findByRole('menuitem', { name: /Export/ }));
  fireEvent.click(await screen.findByRole('menuitem', { name: new RegExp(kind) }));
}

describe('ConversationList exports', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetClient.mockImplementation(() => ({ getConversation: mockGetConversation }));
    conversations$.set(new Map());
    serverRegistry$.set({
      servers: [
        {
          id: SERVER_ID,
          name: 'Local',
          baseUrl: 'http://localhost:5700',
          authToken: null,
          useAuthToken: false,
          isPreset: true,
          createdAt: 0,
          lastUsedAt: 0,
        },
      ],
      activeServerId: SERVER_ID,
      connectedServerIds: [SERVER_ID],
    });
  });

  it('Markdown export fetches the full history for a paginated conversation', async () => {
    const full = fullMessages();
    setLoadedConversation(full.slice(70), { name: 'Cached primary title' });
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Owning server title',
      log: full,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });

    renderList();
    await openExport('Markdown');

    await waitFor(() => expect(mockExportMarkdown).toHaveBeenCalledTimes(1));
    expect(mockGetClient).toHaveBeenCalledWith(SERVER_ID);
    expect(mockGetConversation).toHaveBeenCalledWith('conv-1');
    expect(mockExportMarkdown.mock.calls[0][1]).toBe('Owning server title');
    const exportedMessages = mockExportMarkdown.mock.calls[0][2] as Message[];
    expect(exportedMessages).toHaveLength(120);
    expect(exportedMessages[0].content).toBe('OLDEST-SENTINEL');
    expect(exportedMessages[119].content).toBe('NEWEST-SENTINEL');
    expect(mockReportDownloadOutcome).toHaveBeenCalledWith(undefined, 'Exported as Markdown');
  });

  it('JSON export fetches the full history for a paginated conversation', async () => {
    const full = fullMessages();
    setLoadedConversation(full.slice(70));
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Full history chat',
      log: full,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });

    renderList();
    await openExport('JSON');

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    const exportedMessages = mockExportJSON.mock.calls[0][2] as Message[];
    expect(exportedMessages).toHaveLength(120);
    expect(exportedMessages[0].content).toBe('OLDEST-SENTINEL');
    expect(exportedMessages[119].content).toBe('NEWEST-SENTINEL');
    expect(mockReportDownloadOutcome).toHaveBeenCalledWith(undefined, 'Exported as JSON');
  });

  it.each([
    ['Markdown', () => mockExportMarkdown],
    ['JSON', () => mockExportJSON],
  ] as const)(
    'does not download %s and reports an error when full-history fetch fails',
    async (kind, getExportMock) => {
      setLoadedConversation(fullMessages().slice(70));
      mockGetConversation.mockRejectedValue(new Error('network down'));

      renderList();
      await openExport(kind);

      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith('Failed to load full conversation history')
      );
      expect(getExportMock()).not.toHaveBeenCalled();
      expect(mockReportDownloadOutcome).not.toHaveBeenCalled();
    }
  );

  it.each([
    ['Markdown', () => mockExportMarkdown],
    ['JSON', () => mockExportJSON],
  ] as const)(
    '%s export applies the sidebar inclusion rules to fetched messages',
    async (kind, getExportMock) => {
      const unfiltered: Message[] = [
        { role: 'user', content: 'VISIBLE-USER' },
        { role: 'system', content: 'SYSTEM-HIDDEN' },
        { role: 'tool', content: 'TOOL-VISIBLE' },
        { role: 'assistant', content: 'ASSISTANT-HIDDEN', hide: true },
      ];
      setLoadedConversation(fullMessages().slice(70));
      mockGetConversation.mockResolvedValue({
        id: 'conv-1',
        name: 'Full history chat',
        log: unfiltered,
        logfile: 'conv-1',
        branches: {},
        workspace: '/tmp',
      });

      renderList();
      await openExport(kind);

      await waitFor(() => expect(getExportMock()).toHaveBeenCalledTimes(1));
      const exportedMessages = getExportMock().mock.calls[0][2] as Message[];
      expect(exportedMessages.map((message) => message.content)).toEqual([
        'VISIBLE-USER',
        'TOOL-VISIBLE',
      ]);
    }
  );

  it('exports a short conversation from its owning server', async () => {
    const short = fullMessages().slice(115);
    setLoadedConversation(short, { hasMoreBefore: false });
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Full history chat',
      log: short,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });

    renderList();
    await openExport('Markdown');

    await waitFor(() => expect(mockExportMarkdown).toHaveBeenCalledTimes(1));
    expect(mockGetClient).toHaveBeenCalledWith(SERVER_ID);
    expect(mockGetConversation).toHaveBeenCalledWith('conv-1');
    const exportedMessages = mockExportMarkdown.mock.calls[0][2] as Message[];
    expect(exportedMessages).toHaveLength(5);
  });

  it('fetches and exports an unopened server row', async () => {
    const full = fullMessages();
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Owning server title',
      log: full,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });

    renderList(conv({ name: 'Sidebar row title' }));
    await openExport('JSON');

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    expect(mockGetClient).toHaveBeenCalledWith(SERVER_ID);
    expect(mockGetConversation).toHaveBeenCalledWith('conv-1');
    expect(mockExportJSON.mock.calls[0][1]).toBe('Owning server title');
    expect(mockExportJSON.mock.calls[0][2]).toHaveLength(120);
  });

  it('uses the secondary server history even when the shared cache has a full primary log', async () => {
    const secondaryServerId = 'server-2';
    const primaryFull = fullMessages();
    const secondaryFull = fullMessages().map((message, index) => ({
      ...message,
      content:
        index === 0
          ? 'SECONDARY-OLDEST-SENTINEL'
          : index === 119
            ? 'SECONDARY-NEWEST-SENTINEL'
            : `secondary-${index}`,
    }));

    // Store is keyed only by conv.id: this is a complete primary-server cache
    // for the same ID that happens to be shown as a secondary-server row.
    setLoadedConversation(primaryFull, { hasMoreBefore: false });

    const secondaryGetConversation = jest.fn().mockResolvedValue({
      id: 'conv-1',
      name: 'Secondary server title',
      log: secondaryFull,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });
    mockGetClient.mockImplementation((serverId?: string) =>
      serverId === secondaryServerId
        ? { getConversation: secondaryGetConversation }
        : { getConversation: mockGetConversation }
    );
    serverRegistry$.set({
      servers: [
        {
          id: SERVER_ID,
          name: 'Local',
          baseUrl: 'http://localhost:5700',
          authToken: null,
          useAuthToken: false,
          isPreset: true,
          createdAt: 0,
          lastUsedAt: 0,
        },
        {
          id: secondaryServerId,
          name: 'Secondary',
          baseUrl: 'http://localhost:5701',
          authToken: null,
          useAuthToken: false,
          isPreset: false,
          createdAt: 0,
          lastUsedAt: 0,
        },
      ],
      activeServerId: SERVER_ID,
      connectedServerIds: [SERVER_ID, secondaryServerId],
    });

    renderList(conv({ serverId: secondaryServerId, serverName: 'Secondary' }));
    await openExport('JSON');

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    expect(mockGetClient).toHaveBeenCalledWith(secondaryServerId);
    expect(secondaryGetConversation).toHaveBeenCalledWith('conv-1');
    expect(mockGetConversation).not.toHaveBeenCalled();
    expect(mockExportJSON.mock.calls[0][1]).toBe('Secondary server title');
    const exportedMessages = mockExportJSON.mock.calls[0][2] as Message[];
    expect(exportedMessages[0].content).toBe('SECONDARY-OLDEST-SENTINEL');
    expect(exportedMessages[119].content).toBe('SECONDARY-NEWEST-SENTINEL');
  });

  it('exports the owner-verified selected branch from the full branch map', async () => {
    const ownerClient = { getConversation: mockGetConversation };
    const mainLog = fullMessages();
    const featureLog: Message[] = [
      { role: 'user', content: 'FEATURE-OLDEST' },
      { role: 'assistant', content: 'FEATURE-MIDDLE' },
      { role: 'user', content: 'FEATURE-NEWEST' },
    ];
    mockGetClient.mockReturnValue(ownerClient);
    // The live stream (and therefore the branch selector) belongs to this row's
    // server, so the cached currentBranch is trustworthy — but the messages
    // still must come from the fresh full response, not the cached window.
    setLoadedConversation(mainLog.slice(70), {
      currentBranch: 'feature',
      streamClientGeneration: getClientGeneration(ownerClient),
    });
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Full history chat',
      log: mainLog,
      logfile: 'conv-1',
      branches: { main: mainLog, feature: featureLog },
      workspace: '/tmp',
    });

    renderList();
    await openExport('JSON');

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    const exportedMessages = mockExportJSON.mock.calls[0][2] as Message[];
    expect(exportedMessages.map((message) => message.content)).toEqual([
      'FEATURE-OLDEST',
      'FEATURE-MIDDLE',
      'FEATURE-NEWEST',
    ]);
  });

  it('fails closed when the verified selected branch is missing from the response', async () => {
    const ownerClient = { getConversation: mockGetConversation };
    const mainLog = fullMessages();
    mockGetClient.mockReturnValue(ownerClient);
    setLoadedConversation(mainLog.slice(70), {
      currentBranch: 'feature',
      streamClientGeneration: getClientGeneration(ownerClient),
    });
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Full history chat',
      log: mainLog,
      logfile: 'conv-1',
      branches: { main: mainLog },
      workspace: '/tmp',
    });

    renderList();
    await openExport('JSON');

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Cannot export: branch "feature" is unavailable')
    );
    expect(mockExportJSON).not.toHaveBeenCalled();
    expect(mockReportDownloadOutcome).not.toHaveBeenCalled();
  });

  it('ignores a foreign cache branch and exports the owning server main log', async () => {
    const secondaryServerId = 'server-2';
    const foreignClient = { getConversation: jest.fn() };
    const ownerClient = { getConversation: jest.fn() };
    const mainLog = fullMessages();
    const ownerFeature: Message[] = [{ role: 'user', content: 'OWNER-FEATURE' }];

    mockGetClient.mockImplementation((serverId?: string) =>
      serverId === secondaryServerId ? ownerClient : foreignClient
    );
    serverRegistry$.set({
      servers: [
        {
          id: SERVER_ID,
          name: 'Local',
          baseUrl: 'http://localhost:5700',
          authToken: null,
          useAuthToken: false,
          isPreset: true,
          createdAt: 0,
          lastUsedAt: 0,
        },
        {
          id: secondaryServerId,
          name: 'Secondary',
          baseUrl: 'http://localhost:5701',
          authToken: null,
          useAuthToken: false,
          isPreset: false,
          createdAt: 0,
          lastUsedAt: 0,
        },
      ],
      activeServerId: SERVER_ID,
      connectedServerIds: [SERVER_ID, secondaryServerId],
    });
    // Complete primary cache for the same conv id, with a selected branch. The
    // stream generation proves it belongs to the primary client, not the row.
    setLoadedConversation(fullMessages().slice(70), {
      currentBranch: 'feature',
      streamClientGeneration: getClientGeneration(foreignClient),
    });
    ownerClient.getConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Secondary server title',
      log: mainLog,
      logfile: 'conv-1',
      branches: { main: mainLog, feature: ownerFeature },
      workspace: '/tmp',
    });

    renderList(conv({ serverId: secondaryServerId, serverName: 'Secondary' }));
    await openExport('JSON');

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    expect(ownerClient.getConversation).toHaveBeenCalledWith('conv-1');
    expect(foreignClient.getConversation).not.toHaveBeenCalled();
    const exportedMessages = mockExportJSON.mock.calls[0][2] as Message[];
    expect(exportedMessages).toHaveLength(120);
    expect(exportedMessages[0].content).toBe('OLDEST-SENTINEL');
    expect(exportedMessages[119].content).toBe('NEWEST-SENTINEL');
    expect(exportedMessages.map((message) => message.content)).not.toContain('FOREIGN-FEATURE');
    expect(exportedMessages.map((message) => message.content)).not.toContain('OWNER-FEATURE');
  });

  it('does not expose an export menu for demo conversations', async () => {
    renderList(
      conv({
        id: 'demo-1',
        name: 'Demo Chat',
        serverId: undefined,
        readonly: true,
      })
    );

    fireEvent.contextMenu(screen.getByTestId('conversation-title'));

    expect(screen.queryByRole('menuitem', { name: /Export/ })).not.toBeInTheDocument();
    expect(mockGetClient).not.toHaveBeenCalled();
    expect(mockGetConversation).not.toHaveBeenCalled();
    expect(mockExportMarkdown).not.toHaveBeenCalled();
    expect(mockExportJSON).not.toHaveBeenCalled();
  });

  it('fails closed when the owning server is missing from the registry, even with a full cache', async () => {
    setLoadedConversation(fullMessages(), { hasMoreBefore: false });
    serverRegistry$.set({
      servers: [],
      activeServerId: '',
      connectedServerIds: [],
    });

    renderList();
    await openExport('JSON');

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Cannot export: conversation server is unavailable')
    );
    expect(mockGetClient).not.toHaveBeenCalled();
    expect(mockGetConversation).not.toHaveBeenCalled();
    expect(mockExportJSON).not.toHaveBeenCalled();
  });

  it('keeps the loaded window untouched after a full-history export', async () => {
    const loaded = fullMessages().slice(70);
    setLoadedConversation(loaded);
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Full history chat',
      log: fullMessages(),
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });

    renderList();
    await openExport('Markdown');

    await waitFor(() => expect(mockExportMarkdown).toHaveBeenCalledTimes(1));
    expect(conversations$.get('conv-1').data.log.peek()).toBe(loaded);
    expect(conversations$.get('conv-1').hasMoreBefore.peek()).toBe(true);
  });
});
