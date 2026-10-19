import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import type { Message } from '@/types/conversation';
import { ConversationSettings } from '../ConversationSettings';
import { conversations$ } from '@/stores/conversations';
import { demoConversations } from '@/democonversations';

// Radix primitives used by the settings form observe layout in jsdom tests.
if (typeof global.ResizeObserver === 'undefined') {
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

const mockGetConversation = jest.fn();
const mockExportMarkdown = jest.fn();
const mockExportJSON = jest.fn();
const mockCopyToClipboard = jest.fn();
const mockReportDownloadOutcome = jest.fn();

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      getConversation: mockGetConversation,
      getChatConfig: jest.fn(),
      updateChatConfig: jest.fn(),
      deleteConversation: jest.fn(),
    },
    isConnected$: observable(true),
  }),
}));

jest.mock('sonner', () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}));

jest.mock('@/hooks/useConversationSettings', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { useForm } = require('react-hook-form');
  return {
    useConversationSettings: () => {
      const form = useForm({
        defaultValues: {
          chat: {
            name: 'Full history chat',
            model: 'test/model',
            tools: [],
            tool_format: 'markdown',
            stream: true,
            interactive: false,
            workspace: '/tmp',
            system_prompt: '',
            temperature: undefined,
            top_p: undefined,
            max_tokens: undefined,
            env: [],
          },
          mcp: { enabled: false, auto_start: false, servers: [] },
        },
      });
      return {
        form,
        toolFields: { fields: [] },
        envFields: { fields: [] },
        serverFields: { fields: [] },
        onSubmit: jest.fn(),
        chatConfig: { chat: { name: 'Full history chat' }, env: {}, mcp: { servers: [] } },
        configError: null,
        isLoadingConfig: false,
      };
    },
  };
});

jest.mock('../DeleteConversationConfirmationDialog', () => ({
  DeleteConversationConfirmationDialog: () => null,
}));
jest.mock('../SessionCostSummary', () => ({ SessionCostSummary: () => null }));
jest.mock('../ModelPicker', () => ({ ModelPickerField: () => null }));
jest.mock('../settings/EnvironmentVariables', () => ({ EnvironmentVariables: () => null }));
jest.mock('../settings/ToolsConfiguration', () => ({ ToolsConfiguration: () => null }));
jest.mock('../settings/McpConfiguration', () => ({ McpConfiguration: () => null }));

jest.mock('@/utils/exportConversation', () => {
  const actual = jest.requireActual('@/utils/exportConversation');
  return {
    ...actual,
    exportConversationAsMarkdown: (...args: unknown[]) => mockExportMarkdown(...args),
    exportConversationAsJSON: (...args: unknown[]) => mockExportJSON(...args),
    copyConversationToClipboard: (...args: unknown[]) => mockCopyToClipboard(...args),
    reportDownloadOutcome: (...args: unknown[]) => mockReportDownloadOutcome(...args),
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { toast } = require('sonner');

const fullMessages = (): Message[] =>
  Array.from({ length: 120 }, (_, index) => ({
    role: index % 2 === 0 ? 'user' : 'assistant',
    content:
      index === 0 ? 'OLDEST-SENTINEL' : index === 119 ? 'NEWEST-SENTINEL' : `message-${index}`,
    timestamp: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString(),
  }));

function setLoadedConversation(messages: Message[], hasMoreBefore: boolean) {
  conversations$.set('conv-1', {
    data: {
      id: 'conv-1',
      name: 'Full history chat',
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
    chatConfig: {
      chat: {
        name: 'Full history chat',
        model: null,
        tools: null,
        tool_format: null,
        stream: true,
        interactive: false,
        workspace: '/tmp',
      },
      env: {},
      mcp: { enabled: false, auto_start: false, servers: [] },
    },
    needsInitialStep: false,
    currentBranch: 'main',
    logRevision: 0,
    logOffset: 70,
    hasMoreBefore,
    isWindowHydrated: true,
  });
}

describe('ConversationSettings JSON export', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    conversations$.set(new Map());
  });

  it('fetches full history before exporting JSON for a paginated conversation', async () => {
    const full = fullMessages();
    setLoadedConversation(full.slice(70), true);
    mockGetConversation.mockResolvedValue({
      id: 'conv-1',
      name: 'Full history chat',
      log: full,
      logfile: 'conv-1',
      branches: {},
      workspace: '/tmp',
    });

    render(<ConversationSettings conversationId="conv-1" />);
    fireEvent.click(screen.getByRole('button', { name: /Download \.json/ }));

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    expect(mockGetConversation).toHaveBeenCalledWith('conv-1');
    const exportedMessages = mockExportJSON.mock.calls[0][2] as Message[];
    expect(exportedMessages).toHaveLength(120);
    expect(exportedMessages[0].content).toBe('OLDEST-SENTINEL');
    expect(exportedMessages[119].content).toBe('NEWEST-SENTINEL');
    expect(mockReportDownloadOutcome).toHaveBeenCalledWith(undefined, 'Downloaded as JSON');
  });

  it('does not download and reports an error when JSON full-history fetch fails', async () => {
    setLoadedConversation(fullMessages().slice(70), true);
    mockGetConversation.mockRejectedValue(new Error('network down'));

    render(<ConversationSettings conversationId="conv-1" />);
    fireEvent.click(screen.getByRole('button', { name: /Download \.json/ }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Failed to load full conversation history')
    );
    expect(mockExportJSON).not.toHaveBeenCalled();
  });

  it('passes the raw message log to the JSON exporter', async () => {
    const rawMessages: Message[] = [
      { role: 'user', content: 'visible user' },
      { role: 'system', content: 'system prompt' },
      { role: 'tool', content: 'tool output' },
      { role: 'assistant', content: 'hidden assistant', hide: true },
    ];
    setLoadedConversation(rawMessages, false);

    render(<ConversationSettings conversationId="conv-1" />);
    fireEvent.click(screen.getByRole('button', { name: /Download \.json/ }));

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    // Settings exports the raw log; exportConversationAsJSON owns importable
    // role filtering. The sidebar pre-filters with getExportableMessages.
    expect(mockExportJSON.mock.calls[0][2]).toEqual(rawMessages);
  });

  it('keeps demo conversations read-only with no JSON export action', () => {
    const demo = demoConversations[0];
    expect(demo).toBeDefined();

    render(<ConversationSettings conversationId={demo.id} />);

    expect(screen.getByText('Read-Only Conversation')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Download \.json/ })).not.toBeInTheDocument();
    expect(mockGetConversation).not.toHaveBeenCalled();
    expect(mockExportJSON).not.toHaveBeenCalled();
  });

  it('exports the loaded log without fetching for a short conversation', async () => {
    setLoadedConversation(fullMessages().slice(115), false);

    render(<ConversationSettings conversationId="conv-1" />);
    fireEvent.click(screen.getByRole('button', { name: /Download \.json/ }));

    await waitFor(() => expect(mockExportJSON).toHaveBeenCalledTimes(1));
    expect(mockGetConversation).not.toHaveBeenCalled();
    const exportedMessages = mockExportJSON.mock.calls[0][2] as Message[];
    expect(exportedMessages).toHaveLength(5);
  });
});
