import { observable } from '@legendapp/state';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useApi } from '@/contexts/ApiContext';
import { getClientForServer } from '@/stores/serverClients';
import { conversations$, initConversation, setConnectionStatus } from '@/stores/conversations';
import { useConversation } from '../useConversation';
import { ApiClientError } from '@/utils/api';
import type { Message } from '@/types/conversation';
import { request } from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';

jest.mock('@/contexts/ApiContext', () => ({
  useApi: jest.fn(),
}));

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: jest.fn(),
}));

const mockToast = jest.fn();

jest.mock('@/components/ui/use-toast', () => ({
  useToast: () => ({ toast: mockToast }),
}));

jest.mock('@/utils/audio', () => ({
  playChime: jest.fn().mockResolvedValue(undefined),
}));

jest.mock('@/utils/notifications', () => ({
  notifyGenerationComplete: jest.fn().mockResolvedValue(undefined),
  notifyToolConfirmation: jest.fn().mockResolvedValue(undefined),
}));

describe('useConversation', () => {
  const mockedUseApi = useApi as jest.MockedFunction<typeof useApi>;
  const mockedGetClientForServer = getClientForServer as jest.MockedFunction<
    typeof getClientForServer
  >;
  const subscribeToEvents = jest.fn().mockResolvedValue(undefined);
  const step = jest.fn().mockResolvedValue(undefined);
  const sendMessageApi = jest.fn().mockResolvedValue(undefined);
  const interruptGenerationApi = jest.fn().mockResolvedValue(undefined);
  const editMessageApi = jest.fn();
  const rerunTools = jest.fn().mockResolvedValue(undefined);
  const confirmToolApi = jest.fn().mockResolvedValue(undefined);
  const closeEventStream = jest.fn();
  const getChatConfig = jest.fn().mockResolvedValue(null);
  let eventHandlers:
    | {
        onConnected?: () => void;
        onReconnectState?: (state: {
          generating: boolean;
          pendingTools: {
            tool_id: string;
            tooluse: { tool: string; content: string };
            auto_confirm: boolean;
          }[];
        }) => void;
        onMessageStart?: () => void;
        onMessageAdded?: (message: Message) => void;
        onStepComplete?: () => void;
        onToolPending?: (id: string, tooluse: any, auto: boolean) => void;
        onToolExecuting?: (id: string) => void;
        onReconnectState?: (state: { generating: boolean; pendingTools: any[] }) => void;
        onInterrupted?: () => void;
        onToolComplete?: (id: string, duration: number, success: boolean) => void;
        onError?: (error: string) => void;
      }
    | undefined;
  // A pooled client is a stable object for a server's lifetime (the pool keys
  // clients on the auth header); the mock must return the same object so the
  // hook's client-generation check is stable across renders.
  let primaryClient: Record<string, unknown>;

  const makeClient = (subscribeImpl: jest.Mock) => ({
    subscribeToEvents: subscribeImpl,
    step,
    sendMessage: sendMessageApi,
    interruptGeneration: interruptGenerationApi,
    editMessage: editMessageApi,
    rerunTools,
    confirmTool: confirmToolApi,
    closeEventStream,
    getConversation: jest.fn(),
    reconcileLateUploadOrder: jest.fn().mockResolvedValue(undefined),
    getChatConfig,
    waitForConversationCreation: jest.fn().mockResolvedValue(undefined),
  });

  beforeEach(() => {
    mockedGetClientForServer.mockReset().mockReturnValue(null);
    conversations$.set(new Map());
    subscribeToEvents.mockImplementation((_conversationId, handlers) => {
      eventHandlers = handlers;
      return Promise.resolve();
    });
    step.mockReset().mockResolvedValue(undefined);
    sendMessageApi.mockReset().mockResolvedValue(undefined);
    interruptGenerationApi.mockReset().mockResolvedValue(undefined);
    editMessageApi.mockReset().mockResolvedValue({ log: [], branches: {} });
    rerunTools.mockReset().mockResolvedValue(undefined);
    confirmToolApi.mockReset().mockResolvedValue(undefined);
    closeEventStream.mockClear();
    getChatConfig.mockReset().mockResolvedValue(null);

    initConversation(
      'chat-placeholder',
      {
        id: 'chat-placeholder',
        name: 'New conversation',
        log: [
          {
            role: 'user',
            content: 'What is gptme?',
            timestamp: '2026-06-07T00:00:00.000Z',
          },
        ],
        logfile: 'chat-placeholder',
        branches: {},
        workspace: '.',
      },
      { needsInitialStep: true, initialStepStream: false }
    );

    primaryClient = makeClient(subscribeToEvents);
    mockedUseApi.mockReturnValue({
      getClient: () => primaryClient,
      isConnected$: observable(true),
    } as any);
  });

  afterEach(() => {
    eventHandlers = undefined;
    jest.clearAllMocks();
  });

  it('leaves auto-confirm scheduling to the server and clears pending on execution', async () => {
    renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    act(() => {
      eventHandlers!.onToolPending!('auto-tool', { tool: 'shell', content: 'echo ok' }, true);
    });
    expect(confirmToolApi).not.toHaveBeenCalled();
    act(() => eventHandlers!.onToolExecuting!('auto-tool'));
    expect(conversations$.get('chat-placeholder')!.pendingTool.get()).toBeNull();
    expect(conversations$.get('chat-placeholder')!.executingTool.get()?.id).toBe('auto-tool');
  });

  it('clears stale pending approval when reconnect reports no pending tools', async () => {
    renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    act(() => {
      eventHandlers!.onToolPending!('done-tool', { tool: 'shell', content: 'echo ok' }, false);
      eventHandlers!.onReconnectState!({ generating: false, pendingTools: [] });
    });
    expect(conversations$.get('chat-placeholder')!.pendingTool.get()).toBeNull();
  });

  it('clears a duplicate approval without clearing a newer pending tool', async () => {
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    act(() => eventHandlers!.onToolPending!('done', { tool: 'shell' }, false));
    confirmToolApi.mockResolvedValue(true);
    await act(async () => {
      await result.current.confirmTool('confirm');
    });
    expect(conversations$.get('chat-placeholder')!.pendingTool.get()).toBeNull();
    act(() => eventHandlers!.onToolPending!('old', { tool: 'shell' }, false));
    confirmToolApi.mockImplementationOnce(async () => {
      eventHandlers!.onToolPending!('new', { tool: 'shell' }, false);
      return true;
    });
    await act(async () => {
      await result.current.confirmTool('confirm');
    });
    expect(conversations$.get('chat-placeholder')!.pendingTool.get()?.id).toBe('new');
  });

  it('reports a genuine missing approval without blind retries', async () => {
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    act(() => eventHandlers!.onToolPending!('missing', { tool: 'shell' }, false));
    confirmToolApi.mockRejectedValue(new ApiClientError('Tool not found: missing', 404));
    await act(async () => {
      await expect(result.current.confirmTool('confirm')).rejects.toThrow('Tool not found');
    });
    expect(confirmToolApi).toHaveBeenCalledTimes(1);
  });

  it('retries generation without reposting a saved user message', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    step.mockRejectedValueOnce(new Error('generation unavailable'));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    const options = {
      model: 'test/model',
      stream: false,
      maxTokens: 123,
      temperature: 0.4,
      topP: 0.8,
      reasoningEffort: 'high',
      files: ['attached.txt'],
    };
    await act(async () => {
      await result.current.sendMessage({ message: 'Retry me once', options });
    });
    const failed = conversations$.get('chat-placeholder')!.data.log.get().at(-1)!;
    expect(failed._status).toBe('failed');
    await act(async () => {
      await result.current.retryMessage(failed);
    });
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
    expect(step).toHaveBeenCalledTimes(2);
    expect(step).toHaveBeenLastCalledWith(
      'chat-placeholder',
      'test/model',
      false,
      'main',
      123,
      0.4,
      0.8,
      'high'
    );
    expect(
      conversations$
        .get('chat-placeholder')!
        .data.log.get()
        .filter((message) => message.content === 'Retry me once')
    ).toHaveLength(1);
  });

  it.each(['user', 'assistant', 'system'] as const)(
    'withholds saved Retry when a newer %s message exists',
    async (role) => {
      conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
      step.mockRejectedValueOnce(new Error('generation unavailable'));
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Earlier failed turn' });
      });
      const log$ = conversations$.get('chat-placeholder')!.data.log;
      const failed = log$.get().at(-1)!;
      act(() => {
        log$.push({ role, content: 'Newer history', timestamp: '2026-10-19T12:00:00Z' });
      });
      await act(async () => {
        await result.current.retryMessage(failed);
      });
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
      expect(step).toHaveBeenCalledTimes(1);
      expect(log$.get().find((message) => message.timestamp === failed.timestamp)?._status).toBe(
        'failed'
      );
    }
  );

  it.each(['backoff', 'deferred'])(
    'rechecks saved Retry history after %s waiting',
    async (waiting) => {
      conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
      step.mockRejectedValueOnce(new Error('generation unavailable'));
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Earlier failed turn' });
      });
      const log$ = conversations$.get('chat-placeholder')!.data.log;
      const failed = log$.get().at(-1)!;
      step.mockRejectedValue(new ApiClientError('Busy', 409));
      jest.useFakeTimers();
      try {
        let retrying: Promise<void>;
        await act(async () => {
          retrying = result.current.retryMessage(failed);
        });
        if (waiting === 'deferred') {
          for (const delay of [250, 500, 1000, 2000]) {
            await act(async () => {
              await jest.advanceTimersByTimeAsync(delay);
            });
          }
          await act(async () => {
            await retrying;
          });
        }
        const attempts = step.mock.calls.length;
        act(() => {
          log$.push({ role: 'assistant', content: 'Reply arrived while waiting' });
        });
        await act(async () => {
          if (waiting === 'backoff') await jest.runAllTimersAsync();
          else eventHandlers?.onStepComplete?.();
          await retrying;
        });
        expect(step).toHaveBeenCalledTimes(attempts);
        expect(sendMessageApi).toHaveBeenCalledTimes(1);
      } finally {
        jest.useRealTimers();
      }
    }
  );

  it('resends a definitely rejected save with the original settings and files', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    sendMessageApi.mockRejectedValueOnce(new ApiClientError('Invalid request', 400));
    primaryClient.uploadFiles = jest.fn().mockResolvedValue({ files: [{ path: 'uploaded.txt' }] });
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({
        message: 'Rejected once',
        options: {
          model: 'test/model',
          maxTokens: 50,
          temperature: 0,
          topP: 1,
          pendingFiles: [new File(['data'], 'upload.txt')],
        },
      });
    });
    expect(step).not.toHaveBeenCalled();
    const failed = conversations$.get('chat-placeholder')!.data.log.get().at(-1)!;
    await act(async () => {
      await result.current.retryMessage(failed);
    });
    expect(sendMessageApi).toHaveBeenCalledTimes(2);
    expect(sendMessageApi.mock.calls[1][1]).toMatchObject({
      content: 'Rejected once',
      files: ['uploaded.txt'],
      timestamp: failed.timestamp,
    });
    expect(primaryClient.uploadFiles).toHaveBeenCalledTimes(1);
    expect(step).toHaveBeenLastCalledWith(
      'chat-placeholder',
      'test/model',
      undefined,
      'main',
      50,
      0,
      1,
      undefined
    );
  });

  it.each([
    new TypeError('Failed to fetch'),
    new ApiClientError('Gateway timeout', 504),
    new ApiClientError('Request aborted', 499),
  ])('withholds Retry after an ambiguous save: %s', async (error) => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    sendMessageApi.mockRejectedValueOnce(error);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Unknown save' });
    });
    const failed = conversations$.get('chat-placeholder')!.data.log.get().at(-1)!;
    await act(async () => {
      await result.current.retryMessage(failed);
    });
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
    expect(step).not.toHaveBeenCalled();
    expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)!._status).toBe('failed');
  });

  it('rethrows a rejected POST 409 for the input queue without starting generation', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    const busy = new ApiClientError('Busy', 409);
    sendMessageApi.mockRejectedValueOnce(busy);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await expect(result.current.sendMessage({ message: 'Queue me' })).rejects.toBe(busy);
    });
    expect(step).not.toHaveBeenCalled();
    expect(conversations$.get('chat-placeholder')!.data.log.get()).toHaveLength(1);
  });

  it('retries a busy generation without another POST', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    step.mockRejectedValueOnce(new ApiClientError('Busy', 409));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Busy step' });
    });
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
    expect(step).toHaveBeenCalledTimes(2);
    expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)!._status).toBe('sent');
  });

  it('cancels a deferred Retry step when Stop is pressed', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    step.mockRejectedValueOnce(new Error('Unavailable'));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Deferred retry' });
    });
    const failed = conversations$.get('chat-placeholder')!.data.log.get().at(-1)!;
    step.mockRejectedValue(new ApiClientError('Busy', 409));
    jest.useFakeTimers();
    try {
      let retrying: Promise<void>;
      await act(async () => {
        retrying = result.current.retryMessage(failed);
      });
      for (const delay of [250, 500, 1000, 2000]) {
        await act(async () => {
          await jest.advanceTimersByTimeAsync(delay);
        });
      }
      await act(async () => {
        await retrying;
        await result.current.interruptGeneration();
      });
      const attempts = step.mock.calls.length;
      await act(async () => {
        eventHandlers?.onStepComplete?.();
      });
      expect(step).toHaveBeenCalledTimes(attempts);
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
    }
  });

  it('does not start generation when Stop races a successful save', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    let finishSave: () => void = () => {};
    sendMessageApi.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        finishSave = resolve;
      })
    );
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    let sending: Promise<void>;
    await act(async () => {
      sending = result.current.sendMessage({ message: 'Stop after save' });
    });
    await act(async () => {
      await result.current.interruptGeneration();
      finishSave();
      await sending;
    });
    expect(step).not.toHaveBeenCalled();
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
  });

  it('keeps retry state across hook instances and preserves ordinary successful send', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    step.mockRejectedValueOnce(new Error('Unavailable'));
    const first = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await first.result.current.sendMessage({ message: 'Shared retry' });
    });
    const failed = conversations$.get('chat-placeholder')!.data.log.get().at(-1)!;
    first.unmount();
    const second = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await second.result.current.retryMessage(failed);
    });
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
    expect(step).toHaveBeenCalledTimes(2);
    await act(async () => {
      await second.result.current.sendMessage({ message: 'Ordinary send' });
    });
    expect(sendMessageApi).toHaveBeenCalledTimes(2);
    expect(step).toHaveBeenCalledTimes(3);
    expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)!._status).toBe('sent');
  });

  // Opt-in contract integration: a real server with only the LLM boundary stubbed.
  // Start tests/fixtures/retry_server.py and set GPTME_RETRY_TEST_SERVER plus
  // GPTME_RETRY_TEST_ROOT to its URL and scratch directory. No paid inference.
  (process.env.GPTME_RETRY_TEST_SERVER ? it : it.skip)(
    'persists exactly one user message through mounted-hook Retry on a real server',
    async () => {
      const root = process.env.GPTME_RETRY_TEST_ROOT!;
      const id = `retry-hook-${Date.now()}`;
      const http = (method: string, path: string, body?: unknown): Promise<any> =>
        new Promise((resolve, reject) => {
          const req = request(
            new URL(path, process.env.GPTME_RETRY_TEST_SERVER),
            {
              method,
              headers: { 'Content-Type': 'application/json' },
            },
            (response) => {
              let data = '';
              response.on('data', (chunk) => {
                data += chunk;
              });
              response.on('end', () => {
                const value = JSON.parse(data);
                if (response.statusCode! >= 400)
                  reject(new ApiClientError(value.error, response.statusCode));
                else resolve(value);
              });
            }
          );
          req.on('error', reject);
          req.end(body === undefined ? undefined : JSON.stringify(body));
        });
      const created = await http('PUT', `/api/v2/conversations/${id}`, {
        messages: [],
        config: { model: 'openai/mock-model' },
      });
      conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
      sendMessageApi.mockImplementation((_id, message) =>
        http('POST', `/api/v2/conversations/${id}`, message)
      );
      step.mockImplementation(() =>
        http('POST', `/api/v2/conversations/${id}/step`, {
          session_id: created.session_id,
          stream: false,
        })
      );
      writeFileSync(`${root}/step_mode.txt`, 'fail');
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Real persisted retry' });
      });
      const failed = conversations$.get('chat-placeholder')!.data.log.get().at(-1)!;
      expect(failed._status).toBe('failed');
      writeFileSync(`${root}/step_mode.txt`, 'ok');
      await act(async () => {
        await result.current.retryMessage(failed);
      });
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
      expect(step).toHaveBeenCalledTimes(2);
      const history = await http('GET', `/api/v2/conversations/${id}`);
      expect(
        history.log.filter((m: { content: string }) => m.content === 'Real persisted retry')
      ).toHaveLength(1);
      const persisted = readFileSync(`${root}/logs/${id}/conversation.jsonl`, 'utf8')
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line));
      expect(
        persisted.filter((m) => m.role === 'user' && m.content === 'Real persisted retry')
      ).toHaveLength(1);
      expect(persisted.some((m) => m.role === 'assistant')).toBe(true);
    },
    30000
  );

  it('does not apply chat config after switching conversations', async () => {
    let resolveChatConfig: (value: null) => void = () => {};
    getChatConfig.mockReturnValueOnce(
      new Promise<null>((resolve) => {
        resolveChatConfig = resolve;
      })
    );

    const { rerender } = renderHook(({ conversationId }) => useConversation(conversationId), {
      initialProps: { conversationId: 'chat-placeholder' },
    });

    await waitFor(() => {
      expect(getChatConfig).toHaveBeenCalledWith('chat-placeholder');
    });

    initConversation(
      'chat-other',
      {
        id: 'chat-other',
        name: 'Other conversation',
        log: [],
        logfile: 'chat-other',
        branches: {},
        workspace: '.',
      },
      {}
    );
    rerender({ conversationId: 'chat-other' });

    await act(async () => {
      resolveChatConfig(null);
      await Promise.resolve();
    });

    expect(conversations$.get('chat-placeholder')?.chatConfig.peek()).toBeUndefined();
  });

  it('keeps Stop available when an idle handshake starts the initial step', async () => {
    conversations$.get('chat-placeholder')?.isGenerating.set(true);
    // Hold the request open to expose the interval before generation_started.
    step.mockReturnValueOnce(new Promise(() => {}));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());

    act(() => {
      eventHandlers!.onReconnectState!({ generating: false, pendingTools: [] });
      eventHandlers!.onConnected!();
    });

    expect(step).toHaveBeenCalledTimes(1);
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(true);
    await act(async () => {
      await result.current.interruptGeneration();
    });
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);
    expect(interruptGenerationApi).toHaveBeenCalledWith('chat-placeholder');
  });

  it('clears placeholder initial-step state after subscription connects', async () => {
    renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      eventHandlers?.onConnected?.();
      await Promise.resolve();
    });

    expect(step).toHaveBeenCalledWith(
      'chat-placeholder',
      undefined,
      false,
      'main',
      undefined,
      undefined,
      undefined
    );
    expect(conversations$.get('chat-placeholder')?.needsInitialStep.get()).toBe(false);
    expect(conversations$.get('chat-placeholder')?.initialStepStream.get()).toBeUndefined();
    expect(
      Object.prototype.hasOwnProperty.call(
        conversations$.get('chat-placeholder')?.peek() ?? {},
        'initialStepStream'
      )
    ).toBe(false);
  });

  it('re-subscribes on the replacement client after a credential swap', async () => {
    const { rerender } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    // The stream established successfully on the first client.
    act(() => {
      setConnectionStatus('chat-placeholder', 'connected');
    });
    expect(conversations$.get('chat-placeholder')?.isConnected.get()).toBe(true);

    // A credential refresh swaps the pooled client: serverClients.ts disposes the
    // old one, closing its event stream, while the conversation still reports
    // "connected". The hook must re-subscribe on the replacement instead of
    // returning early and leaving the open chat without events (gptme#4028).
    const newSubscribeToEvents = jest.fn().mockResolvedValue(undefined);
    mockedUseApi.mockReturnValue({
      getClient: () => makeClient(newSubscribeToEvents),
      isConnected$: observable(true),
    } as any);

    rerender();

    await waitFor(() => {
      expect(newSubscribeToEvents).toHaveBeenCalledTimes(1);
    });
    // Only the replacement client subscribes; the disposed one is not re-used.
    expect(subscribeToEvents).toHaveBeenCalledTimes(1);
  });

  it('does not restart a live stream when a second instance mounts (split view)', async () => {
    const first = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });
    act(() => {
      setConnectionStatus('chat-placeholder', 'connected');
    });

    // Split view renders the same conversation twice on one client. The second
    // instance must see the shared subscription generation and return early
    // instead of re-subscribing and restarting the live stream.
    const second = renderHook(() => useConversation('chat-placeholder'));

    await act(async () => {
      await Promise.resolve();
    });

    expect(subscribeToEvents).toHaveBeenCalledTimes(1);

    first.unmount();
    second.unmount();
  });

  it('uses the latest generation settings for the initial step', async () => {
    renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    // Regression for gptme/gptme#3903: settings can change while the event
    // subscription is connecting; the initial step must read the store at
    // connection time instead of using values captured at subscription time.
    act(() => {
      conversations$.get('chat-placeholder')?.maxTokens.set(64);
      conversations$.get('chat-placeholder')?.temperature.set(0.2);
      conversations$.get('chat-placeholder')?.topP.set(0.8);
    });

    await act(async () => {
      eventHandlers?.onConnected?.();
      await Promise.resolve();
    });

    expect(step).toHaveBeenCalledWith('chat-placeholder', undefined, false, 'main', 64, 0.2, 0.8);
  });

  it('honors Stop before the SSE session exists by cancelling the pending initial step', async () => {
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    const { result } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await result.current.interruptGeneration();
    });

    expect(conversations$.get('chat-placeholder')?.needsInitialStep.get()).toBe(false);
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);
    expect(interruptGenerationApi).toHaveBeenCalledWith('chat-placeholder');

    await act(async () => {
      eventHandlers?.onConnected?.();
      await Promise.resolve();
    });

    expect(step).not.toHaveBeenCalled();
  });

  it('hides Stop even after onConnected already consumed the pending initial step', async () => {
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    const { result } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      eventHandlers?.onConnected?.();
      await Promise.resolve();
    });
    expect(step).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.interruptGeneration();
    });

    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);

    await act(async () => {
      eventHandlers?.onMessageStart?.();
    });

    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);
    expect(interruptGenerationApi).toHaveBeenCalledTimes(2);
  });

  it('clears generating when the initial step request fails', async () => {
    step.mockRejectedValueOnce(new Error('step failed'));
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      eventHandlers?.onConnected?.();
    });

    await waitFor(() => {
      expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);
    });
    expect(step).toHaveBeenCalled();
  });

  it.each([
    [
      'edit with truncation',
      async (result: { current: ReturnType<typeof useConversation> }) => {
        await result.current.editMessage(0, 'What is gptme?', true);
      },
    ],
    [
      'rerun',
      async (result: { current: ReturnType<typeof useConversation> }) => {
        rerunTools.mockRejectedValueOnce(new Error('no tools'));
        await result.current.rerunFromMessage(0);
      },
    ],
    [
      'regenerate',
      async (result: { current: ReturnType<typeof useConversation> }) => {
        conversations$.get('chat-placeholder')?.data.log.set([
          {
            role: 'user',
            content: 'What is gptme?',
            timestamp: '2026-06-07T00:00:00.000Z',
          },
          {
            role: 'assistant',
            content: 'An agent.',
            timestamp: '2026-06-07T00:00:01.000Z',
          },
        ]);
        await result.current.regenerateMessage(1);
      },
    ],
  ])('does not interrupt a later %s after Stop', async (_label, startGeneration) => {
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    const { result } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await result.current.interruptGeneration();
    });
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);

    await act(async () => {
      await startGeneration(result);
    });
    expect(step).toHaveBeenCalled();

    await act(async () => {
      eventHandlers?.onMessageStart?.();
    });

    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(true);
    expect(interruptGenerationApi).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      'edit-with-truncation',
      (result: { current: ReturnType<typeof useConversation> }) =>
        result.current.editMessage(0, 'What is gptme?', true),
    ],
    [
      'rerun-of-a-non-last-message',
      (result: { current: ReturnType<typeof useConversation> }) =>
        result.current.rerunFromMessage(0),
    ],
    [
      'regenerate',
      (result: { current: ReturnType<typeof useConversation> }) =>
        result.current.regenerateMessage(1),
    ],
  ])('honors a Stop pressed while the %s request is in flight', async (_label, startGeneration) => {
    conversations$.get('chat-placeholder')?.data.log.set([
      {
        role: 'user',
        content: 'What is gptme?',
        timestamp: '2026-06-07T00:00:00.000Z',
      },
      {
        role: 'assistant',
        content: 'An agent.',
        timestamp: '2026-06-07T00:00:01.000Z',
      },
    ]);
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    // Hold the truncation request open so Stop lands while it is in flight.
    let resolveEdit: (value: { log: []; branches: Record<string, never> }) => void = () => {};
    editMessageApi.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveEdit = resolve;
      })
    );

    const { result } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    let pending: Promise<unknown> | undefined;
    act(() => {
      pending = startGeneration(result);
    });

    await act(async () => {
      await result.current.interruptGeneration();
    });

    await act(async () => {
      resolveEdit({ log: [], branches: {} });
      await pending;
    });

    await act(async () => {
      eventHandlers?.onMessageStart?.();
    });

    // The Stop is newer than the request: do not start generation, and do
    // not let a late onMessageStart resume it.
    expect(step).not.toHaveBeenCalled();
    expect(rerunTools).not.toHaveBeenCalled();
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);
    expect(interruptGenerationApi).toHaveBeenCalledTimes(2);
  });

  it('re-interrupts after a successful rerunTools that raced with Stop', async () => {
    conversations$.get('chat-placeholder')?.data.log.set([
      {
        role: 'user',
        content: 'What is gptme?',
        timestamp: '2026-06-07T00:00:00.000Z',
      },
      {
        role: 'assistant',
        content: 'An agent.',
        timestamp: '2026-06-07T00:00:01.000Z',
      },
    ]);
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    // Hold rerunTools itself open. Unlike the truncation-request race, a
    // successful rerun can start auto-confirm execution before it returns.
    let resolveRerun: (value: { status: string; tool_ids: string[] }) => void = () => {};
    rerunTools.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRerun = resolve;
      })
    );

    const { result } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    let pending: Promise<unknown> | undefined;
    act(() => {
      // Last message: skip truncation and go straight to rerunTools.
      pending = result.current.rerunFromMessage(1);
    });

    await act(async () => {
      await result.current.interruptGeneration();
    });

    await act(async () => {
      resolveRerun({ status: 'ok', tool_ids: ['tool-1'] });
      await pending;
    });

    await act(async () => {
      eventHandlers?.onMessageStart?.();
    });

    expect(step).not.toHaveBeenCalled();
    expect(rerunTools).toHaveBeenCalledTimes(1);
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(false);
    // 1: user Stop while rerunTools is in flight
    // 2: post-success re-interrupt of the execution rerun started
    // 3: late onMessageStart still sees the stop flag
    expect(interruptGenerationApi).toHaveBeenCalledTimes(3);
  });

  it('re-interrupts leftover rerun tools before a later action that started in flight', async () => {
    conversations$.get('chat-placeholder')?.data.log.set([
      {
        role: 'user',
        content: 'What is gptme?',
        timestamp: '2026-06-07T00:00:00.000Z',
      },
      {
        role: 'assistant',
        content: 'An agent.',
        timestamp: '2026-06-07T00:00:01.000Z',
      },
    ]);
    conversations$.get('chat-placeholder')?.isGenerating.set(true);

    let resolveRerun: (value: { status: string; tool_ids: string[] }) => void = () => {};
    rerunTools.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRerun = resolve;
      })
    );

    const { result } = renderHook(() => useConversation('chat-placeholder'));

    await waitFor(() => {
      expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    });

    let pendingRerun: Promise<unknown> | undefined;
    act(() => {
      pendingRerun = result.current.rerunFromMessage(1);
    });

    await act(async () => {
      await result.current.interruptGeneration();
    });

    let pendingEdit: Promise<unknown> | undefined;
    act(() => {
      pendingEdit = result.current.editMessage(0, 'What is gptme?', true);
    });

    await act(async () => {
      resolveRerun({ status: 'ok', tool_ids: ['tool-1'] });
      await pendingRerun;
      await pendingEdit;
    });

    await act(async () => {
      eventHandlers?.onMessageStart?.();
    });

    expect(rerunTools).toHaveBeenCalledTimes(1);
    expect(step).toHaveBeenCalledTimes(1);
    expect(conversations$.get('chat-placeholder')?.isGenerating.get()).toBe(true);
    // 1: user Stop while rerunTools is in flight
    // 2: old rerun re-interrupts leftover auto-confirm tools before the edit steps
    // Late onMessageStart belongs to the edit, so it must not interrupt again.
    expect(interruptGenerationApi).toHaveBeenCalledTimes(2);
  });

  it('ignores a previous owner’s delayed load after switching the same ID to another server', async () => {
    let resolveOldLoad!: (data: object) => void;
    const oldClient = makeClient(jest.fn().mockResolvedValue(undefined));
    oldClient.getConversation.mockReturnValue(
      new Promise((resolve) => {
        resolveOldLoad = resolve;
      })
    );
    const newClient = makeClient(jest.fn().mockResolvedValue(undefined));
    const payload = (name: string) => ({
      id: 'shared-id',
      name,
      log: [],
      logfile: 'shared-id',
      branches: {},
      workspace: '.',
    });
    newClient.getConversation.mockResolvedValue(payload('Server B chat'));
    mockedUseApi.mockReturnValue({
      getClient: (id: string) => (id === 'server-a' ? oldClient : newClient),
      isConnected$: observable(true),
    } as any);
    mockedGetClientForServer.mockImplementation(
      (id: string) => (id === 'server-a' ? oldClient : newClient) as never
    );
    initConversation('shared-id');
    const { rerender } = renderHook(({ owner }) => useConversation('shared-id', owner), {
      initialProps: { owner: 'server-a' },
    });
    await waitFor(() => expect(oldClient.getConversation).toHaveBeenCalled());
    rerender({ owner: 'server-b' });
    await waitFor(() =>
      expect(conversations$.get('shared-id')!.data.name.get()).toBe('Server B chat')
    );

    await act(async () => {
      resolveOldLoad(payload('Server A stale chat'));
    });

    expect(conversations$.get('shared-id')!.data.name.get()).toBe('Server B chat');
    expect(conversations$.get('shared-id')!.serverId.get()).toBe('server-b');
    expect(oldClient.subscribeToEvents).not.toHaveBeenCalled();
  });

  it('reloads a same-ID entry that was loaded from another server', async () => {
    initConversation('shared-id', {
      id: 'shared-id',
      name: 'Server A chat',
      log: [
        {
          role: 'user',
          content: 'Only on server A',
          timestamp: '2026-06-07T00:00:00.000Z',
        },
      ],
      logfile: 'shared-id',
      branches: {},
      workspace: '.',
    });
    conversations$.get('shared-id')!.serverId.set('server-a');
    // The previous owner's pooled client must have its live stream closed
    // before the reset, or its callbacks keep writing into the shared entry.
    const oldOwnerClose = jest.fn();
    mockedGetClientForServer.mockImplementation((id: string) =>
      id === 'server-a' ? ({ closeEventStream: oldOwnerClose } as never) : null
    );
    (primaryClient.getConversation as jest.Mock).mockResolvedValue({
      id: 'shared-id',
      name: 'Server B chat',
      log: [],
      logfile: 'shared-id',
      branches: {},
      workspace: '.',
    });

    renderHook(() => useConversation('shared-id', 'server-b'));

    await waitFor(() =>
      expect(conversations$.get('shared-id')!.data.name.get()).toBe('Server B chat')
    );
    expect(conversations$.get('shared-id')!.serverId.get()).toBe('server-b');
    expect(primaryClient.getConversation).toHaveBeenCalledWith('shared-id', { limit: 50 });
    expect(oldOwnerClose).toHaveBeenCalledWith('shared-id');
  });

  it.each([false, true])(
    'keeps a refusal retryable after a system error event (%s)',
    async (systemError) => {
      conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));

      // The event stream is subscribed when a message starts a generation, so
      // send once first; onError then covers the failure that arrives mid-turn.
      jest.useFakeTimers({ now: new Date('2026-11-18T15:59:00Z') });
      await act(async () => {
        await result.current.sendMessage({ message: 'Hello' });
      });
      jest.setSystemTime(new Date('2026-11-18T15:59:01Z'));
      mockToast.mockClear();

      // What the server forwards for an HTTP 402 from the LLM gateway: the
      // provider string, with no hint about what the user should do next.
      const providerError = 'Error code: 402 - Payment required';

      expect(eventHandlers).toBeDefined();
      act(() => {
        eventHandlers!.onMessageStart!();
        if (systemError) {
          eventHandlers!.onMessageAdded!({
            role: 'system',
            content: `Error: ${providerError}`,
            timestamp: '2026-11-18T16:00:00Z',
          });
        }
        eventHandlers!.onError!(providerError);
      });

      const log = conversations$.get('chat-placeholder')!.data.log.get();
      const last = log.find((message) => message.content === 'Hello')!;
      // The failure stays attached to the message that triggered it, so it
      // survives toast dismissal and a reload, instead of vanishing with the
      // discarded empty assistant bubble.
      expect(last.role).toBe('user');
      expect(last._status).toBe('failed');
      expect(last._error).toBe(providerError);

      // The toast is classified rather than labelled a generic "Error".
      expect(mockToast).toHaveBeenCalledWith(
        expect.objectContaining({
          variant: 'destructive',
          title: 'Payment required',
          description: providerError,
        })
      );
      await act(async () => {
        await result.current.retryMessage(last);
      });
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
      expect(step).toHaveBeenCalledTimes(2);
      expect(
        conversations$
          .get('chat-placeholder')!
          .data.log.get()
          .map((message) => message.content)
      ).toEqual(log.map((message) => message.content));
      expect(
        conversations$
          .get('chat-placeholder')!
          .data.log.get()
          .find((message) => message.content === 'Hello')?._status
      ).toBe('sent');
      jest.useRealTimers();
    }
  );

  it('allows successive refusal diagnostics but does not blame a late upload', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Active prompt' });
    });
    const log$ = conversations$.get('chat-placeholder')!.data.log;
    const owner = log$.get().at(-1)!;
    for (const [index, error] of [
      'Error code: 402 - Payment required',
      'Error code: 401 - Unauthorized',
    ].entries()) {
      act(() => {
        eventHandlers!.onMessageAdded!({
          role: 'system',
          content: `Error: ${error}`,
          timestamp: `error-${index}`,
        });
        eventHandlers!.onError!(error);
      });
      await act(async () => {
        await result.current.retryMessage(
          log$.get().find((message) => message.timestamp === owner.timestamp)!
        );
      });
    }
    expect(step).toHaveBeenCalledTimes(3);
    act(() => {
      log$.push({
        role: 'user',
        content: 'Late upload',
        timestamp: 'late-upload',
        _status: 'sent',
      });
      eventHandlers!.onError!('Error code: 402 - Payment required');
    });
    expect(log$.get().find((message) => message.timestamp === owner.timestamp)?._status).toBe(
      'failed'
    );
    expect(log$.get().at(-1)?._status).toBe('sent');
  });

  it('attributes a split-view refusal to the prompt sent by the second hook', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(subscribeToEvents).toHaveBeenCalledTimes(1));
    act(() => setConnectionStatus('chat-placeholder', 'connected'));
    const second = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await second.result.current.sendMessage({ message: 'Split-view prompt' });
    });
    expect(subscribeToEvents).toHaveBeenCalledTimes(1);
    act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
    expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)).toMatchObject({
      content: 'Split-view prompt',
      _status: 'failed',
    });
  });

  it.each(['backoff', 'deferred'])(
    'keeps active ownership while another step is %s',
    async (waiting) => {
      conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Active A' });
      });
      const log$ = conversations$.get('chat-placeholder')!.data.log;
      const owner = log$.get().at(-1)!;
      jest.useFakeTimers({ now: new Date('2026-11-18T22:05:00Z') });
      act(() => eventHandlers!.onMessageStart!());
      jest.setSystemTime(new Date('2026-11-18T22:05:01Z'));
      try {
        step.mockRejectedValue(new ApiClientError('Busy', 409));
        let sending: Promise<void>;
        await act(async () => {
          sending = result.current.sendMessage({ message: 'Pending B' });
        });
        if (waiting === 'deferred') {
          for (const delay of [250, 500, 1000, 2000]) {
            await act(async () => {
              await jest.advanceTimersByTimeAsync(delay);
            });
          }
          await sending!;
        }
        act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
        expect(log$.get().find((message) => message.timestamp === owner.timestamp)?._status).toBe(
          'failed'
        );
        expect(log$.get().find((message) => message.content === 'Pending B')?._status).toBe('sent');
        step.mockResolvedValue(undefined);
        await act(async () => {
          if (waiting === 'backoff') await jest.runAllTimersAsync();
          else eventHandlers!.onStepComplete!();
          await sending!;
        });
        act(() => eventHandlers!.onError!('Error code: 401 - Unauthorized'));
        expect(log$.get().find((message) => message.content === 'Pending B')?._status).toBe(
          'failed'
        );
      } finally {
        jest.useRealTimers();
      }
    }
  );

  it('keeps a newly accepted prompt after the previous completion arrives during HTTP', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      await result.current.sendMessage({ message: 'Old turn' });
    });
    await new Promise((resolve) => setTimeout(resolve, 2));
    step.mockImplementationOnce(async () => {
      eventHandlers!.onStepComplete!();
    });
    await act(async () => {
      await result.current.sendMessage({ message: 'New turn' });
    });
    act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
    const log = conversations$.get('chat-placeholder')!.data.log.get();
    expect(log.find((m) => m.content === 'Old turn')?._status).toBe('sent');
    expect(log.find((m) => m.content === 'New turn')?._status).toBe('failed');
  });

  it('does not retain a rejected candidate when the previous completion races a 409', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      await result.current.sendMessage({ message: 'Old turn' });
    });
    await new Promise((resolve) => setTimeout(resolve, 2));
    jest.useFakeTimers();
    try {
      step.mockImplementationOnce(async () => {
        eventHandlers!.onStepComplete!();
        throw new ApiClientError('Busy', 409);
      });
      let sending!: Promise<void>;
      await act(async () => {
        sending = result.current.sendMessage({ message: 'Busy candidate' });
      });
      act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
      expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)?._status).toBe('sent');
      await act(async () => {
        await jest.runAllTimersAsync();
        await sending;
      });
    } finally {
      jest.useRealTimers();
    }
  });

  it('releases a skipped tool turn before its confirmation HTTP response', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      await result.current.sendMessage({ message: 'Skip turn' });
    });
    act(() => eventHandlers!.onToolPending!('skip-tool', { tool: 'shell', content: 'pwd' }, false));
    (primaryClient.confirmTool as jest.Mock).mockImplementationOnce(async () => {
      eventHandlers!.onStepComplete!();
    });
    await act(async () => {
      await result.current.confirmTool('skip');
    });
    await new Promise((resolve) => setTimeout(resolve, 2));
    step.mockImplementationOnce(async () => {
      eventHandlers!.onError!('Error code: 402 - Payment required');
    });
    await act(async () => {
      await result.current.sendMessage({ message: 'After skip' });
    });
    const log = conversations$.get('chat-placeholder')!.data.log.get();
    expect(log.find((m) => m.content === 'Skip turn')?._status).toBe('sent');
    expect(log.find((m) => m.content === 'After skip')?._status).toBe('failed');
  });

  it('keeps the queue busy while Skip reserves its continuation', async () => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    act(() => eventHandlers!.onToolPending!('skip-tool', { tool: 'shell', content: 'pwd' }, false));
    let finish!: () => void;
    (primaryClient.confirmTool as jest.Mock).mockReturnValueOnce(
      new Promise<void>((resolve) => {
        finish = resolve;
      })
    );
    let skipping!: Promise<void>;
    act(() => {
      skipping = result.current.confirmTool('skip');
    });
    // Same busy predicate as ChatInput: clearing pendingTool must not flush its queue.
    expect(conv$.pendingTool.get()).toBeNull();
    expect(
      conv$.isGenerating.get() || !!conv$.executingTool.get() || !!conv$.pendingTool.get()
    ).toBe(true);
    act(() => eventHandlers!.onStepComplete!());
    expect(conv$.isGenerating.get()).toBe(false);
    await act(async () => {
      finish();
      await skipping;
    });
    expect(conv$.isGenerating.get()).toBe(false);
  });

  it('releases the Skip reservation when confirmation fails', async () => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    act(() => eventHandlers!.onToolPending!('skip-tool', { tool: 'shell', content: 'pwd' }, false));
    (primaryClient.confirmTool as jest.Mock).mockRejectedValueOnce(new ApiClientError('Busy', 409));
    await act(async () => {
      await expect(result.current.confirmTool('skip')).rejects.toThrow('Busy');
    });
    expect(conv$.isGenerating.get()).toBe(false);
  });

  it('releases failed rerun ownership before the next early refusal', async () => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const prompt = conv$.data.log.get()[0];
    conv$.data.log.set([
      prompt,
      { role: 'assistant', content: 'Old answer', timestamp: 'old-answer' },
    ]);
    rerunTools.mockRejectedValueOnce(new Error('No tool uses found'));
    step.mockRejectedValueOnce(new ApiClientError('Step failed', 500));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      await result.current.rerunFromMessage(1);
    });
    expect(conv$.generationPrompt.get()).toBeUndefined();
    step.mockImplementationOnce(async () => {
      eventHandlers!.onError!('Error code: 402 - Payment required');
    });
    await act(async () => {
      await result.current.sendMessage({ message: 'After failed rerun' });
    });
    expect(conv$.data.log.get()[0]._status).not.toBe('failed');
    expect(conv$.data.log.get().at(-1)).toMatchObject({
      content: 'After failed rerun',
      _status: 'failed',
    });
  });

  it('keeps Retry when initial HTTP refusal precedes its SSE diagnostic', async () => {
    const log$ = conversations$.get('chat-placeholder')!.data.log;
    log$[0]._retry.set({ persistence: 'saved', options: { stream: false } });
    step.mockRejectedValueOnce(new ApiClientError('Error code: 402 - Payment required', 500));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      eventHandlers!.onConnected!();
    });
    act(() => {
      eventHandlers!.onMessageAdded!({
        role: 'system',
        content: 'Error: Error code: 402 - Payment required',
        timestamp: 'delayed-error',
      });
      eventHandlers!.onError!('Error code: 402 - Payment required');
    });
    await act(async () => {
      await result.current.retryMessage(log$[0].get());
    });
    expect(step).toHaveBeenCalledTimes(2);
    expect(sendMessageApi).not.toHaveBeenCalled();
  });

  it.each(['edit', 'rerun', 'regenerate'])('owns refusals from %s generation', async (action) => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const prompt = conv$.data.log.get()[0];
    conv$.data.log.set([
      prompt,
      { role: 'assistant', content: 'Old answer', timestamp: 'old-answer' },
    ]);
    editMessageApi.mockResolvedValue({ log: [prompt], branches: {} });
    rerunTools.mockRejectedValueOnce(new Error('no tools'));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      if (action === 'edit') await result.current.editMessage(0, prompt.content, true);
      else if (action === 'rerun') await result.current.rerunFromMessage(0);
      else await result.current.regenerateMessage(1);
    });
    act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
    expect(conv$.data.log.get()[0]._status).toBe('failed');
    await act(async () => {
      await result.current.retryMessage(conv$.data.log.get()[0]);
    });
    expect(step).toHaveBeenCalledTimes(2);
    expect(sendMessageApi).not.toHaveBeenCalled();
  });

  it('attributes an early SSE refusal before the step HTTP response', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    step.mockImplementationOnce(async () => {
      eventHandlers!.onError!('Error code: 402 - Payment required');
      throw new ApiClientError('Error code: 402 - Payment required', 500);
    });
    await act(async () => {
      await result.current.sendMessage({ message: 'Early refusal' });
    });
    expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)).toMatchObject({
      content: 'Early refusal',
      _status: 'failed',
      _retry: {
        continuation: [],
        diagnostics: ['Error: Error code: 402 - Payment required'],
      },
    });
  });

  it('gives a new prompt ownership after the previous prompt was refused', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    jest.useFakeTimers({ now: new Date('2026-11-18T22:10:00Z') });
    try {
      await act(async () => {
        await result.current.sendMessage({ message: 'Refused A' });
      });
      act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
      jest.setSystemTime(new Date('2026-11-18T22:10:01Z'));
      step.mockImplementationOnce(async () => {
        eventHandlers!.onMessageAdded!({
          role: 'system',
          content: 'Error: Error code: 401 - Unauthorized',
          timestamp: 'b-diagnostic',
        });
        eventHandlers!.onError!('Error code: 401 - Unauthorized');
        throw new ApiClientError('Error code: 401 - Unauthorized', 500);
      });
      await act(async () => {
        await result.current.sendMessage({ message: 'Refused B' });
      });
      const log$ = conversations$.get('chat-placeholder')!.data.log;
      const b = log$.get().find((message) => message.content === 'Refused B')!;
      expect(b._retry?.diagnostics).toContain('Error: Error code: 401 - Unauthorized');
      await act(async () => {
        await result.current.retryMessage(b);
      });
      expect(step).toHaveBeenCalledTimes(3);
      expect(sendMessageApi).toHaveBeenCalledTimes(2);
    } finally {
      jest.useRealTimers();
    }
  });

  it.each(['local', 'server', 'completed'])(
    'does not retain a %s prompt as owner of the next early refusal',
    async (ending) => {
      conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await waitFor(() => expect(eventHandlers).toBeDefined());
      jest.useFakeTimers({ now: new Date('2026-11-18T22:15:00Z') });
      try {
        let finishStep!: () => void;
        step.mockReturnValueOnce(
          new Promise<void>((resolve) => {
            finishStep = resolve;
          })
        );
        let sending!: Promise<void>;
        await act(async () => {
          sending = result.current.sendMessage({ message: 'Old A' });
        });
        await act(async () => {
          if (ending === 'local') await result.current.interruptGeneration();
          else if (ending === 'server') eventHandlers!.onInterrupted!();
          else eventHandlers!.onStepComplete!();
          finishStep();
          await sending;
        });
        jest.setSystemTime(new Date('2026-11-18T22:15:01Z'));
        const error = 'Error code: 401 - Unauthorized';
        step.mockImplementationOnce(async () => {
          eventHandlers!.onMessageAdded!({
            role: 'system',
            content: `Error: ${error}`,
            timestamp: 'b-error',
          });
          eventHandlers!.onError!(error);
          throw new ApiClientError(error, 500);
        });
        await act(async () => {
          await result.current.sendMessage({ message: 'New B' });
        });
        const log$ = conversations$.get('chat-placeholder')!.data.log;
        expect(log$.get().find((message) => message.content === 'Old A')?._status).toBe('sent');
        const b = log$.get().find((message) => message.content === 'New B')!;
        expect(b._retry?.diagnostics).toContain(`Error: ${error}`);
        await act(async () => {
          await result.current.retryMessage(b);
        });
        expect(step).toHaveBeenCalledTimes(3);
        expect(sendMessageApi).toHaveBeenCalledTimes(2);
      } finally {
        jest.useRealTimers();
      }
    }
  );

  it.each(['pending', 'executing'])(
    'keeps ownership across step_complete with a %s manual tool',
    async (toolState) => {
      conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Confirm a tool' });
      });
      const log$ = conversations$.get('chat-placeholder')!.data.log;
      const prompt = log$.get().at(-1)!;
      // Keep the streamed placeholder's timestamp distinct from the sent prompt.
      await new Promise((resolve) => setTimeout(resolve, 2));
      act(() => {
        eventHandlers!.onMessageAdded!({
          role: 'assistant',
          content: 'Tool call',
          timestamp: 'call',
        });
        eventHandlers!.onToolPending!('tool-1', { tool: 'shell', content: 'pwd' }, false);
      });
      if (toolState === 'executing') {
        await act(async () => {
          await result.current.confirmTool('confirm');
        });
        act(() => {
          eventHandlers!.onToolExecuting!('tool-1');
        });
      }
      act(() => {
        eventHandlers!.onStepComplete!();
      });
      if (toolState === 'pending') {
        await act(async () => {
          await result.current.confirmTool('confirm');
        });
        act(() => {
          eventHandlers!.onToolExecuting!('tool-1');
        });
      }
      act(() => {
        eventHandlers!.onMessageAdded!({
          role: 'tool',
          content: '/workspace',
          timestamp: 'result',
        });
        eventHandlers!.onToolComplete!('tool-1', 10, true);
        eventHandlers!.onMessageStart!();
        eventHandlers!.onMessageAdded!({
          role: 'system',
          content: 'Error: Error code: 402 - Payment required',
          timestamp: 'error',
        });
        eventHandlers!.onError!('Error code: 402 - Payment required');
      });
      const failed = log$.get().find((message) => message.timestamp === prompt.timestamp)!;
      expect(failed._status).toBe('failed');
      await act(async () => {
        await result.current.retryMessage(failed);
      });
      expect(step).toHaveBeenCalledTimes(2);
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
      expect(editMessageApi).not.toHaveBeenCalled();
    }
  );

  it.each([401, 402])('offers Retry for an HTTP-500-wrapped initial %s refusal', async (status) => {
    const log$ = conversations$.get('chat-placeholder')!.data.log;
    log$[0]._retry.set({ persistence: 'saved', options: { stream: false } });
    step.mockRejectedValueOnce(new ApiClientError(`Error code: ${status} - Provider refused`, 500));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      eventHandlers!.onConnected!();
    });
    expect(log$[0]._status.get()).toBe('failed');
    await act(async () => {
      await result.current.retryMessage(log$[0].get());
    });
    expect(step).toHaveBeenCalledTimes(2);
    expect(sendMessageApi).not.toHaveBeenCalled();
  });

  it('offers Retry for an HTTP-only initial-step refusal', async () => {
    const log$ = conversations$.get('chat-placeholder')!.data.log;
    log$[0]._retry.set({ persistence: 'saved', options: { stream: false } });
    step.mockRejectedValueOnce(new ApiClientError('Unauthorized', 401));
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    await act(async () => {
      eventHandlers!.onConnected!();
    });
    expect(log$[0]._status.get()).toBe('failed');
    await act(async () => {
      await result.current.retryMessage(log$[0].get());
    });
    expect(step).toHaveBeenCalledTimes(2);
    expect(sendMessageApi).not.toHaveBeenCalled();
  });

  it.each(['user', 'assistant', 'system', 'tool'] as const)(
    'withholds continuation Retry after newer %s activity',
    async (role) => {
      conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Refused turn' });
      });
      const log$ = conversations$.get('chat-placeholder')!.data.log;
      const prompt = log$.get().at(-1)!;
      act(() => {
        eventHandlers!.onMessageAdded!({
          role: 'assistant',
          content: 'Tool call',
          timestamp: 'call',
        });
        eventHandlers!.onMessageAdded!({
          role: 'tool',
          content: 'Tool result',
          timestamp: 'result',
        });
        eventHandlers!.onError!('Error code: 402 - Payment required');
        log$.push({ role, content: 'New activity', timestamp: 'new' });
      });
      await act(async () => {
        await result.current.retryMessage(
          log$.get().find((message) => message.timestamp === prompt.timestamp)!
        );
      });
      expect(step).toHaveBeenCalledTimes(1);
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
    }
  );

  it('retries a refused tool continuation without reposting or truncating the turn', async () => {
    conversations$.get('chat-placeholder')!.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Run a tool' });
    });
    const log$ = conversations$.get('chat-placeholder')!.data.log;
    const prompt = log$.get().at(-1)!;
    act(() => {
      eventHandlers!.onMessageAdded!({
        role: 'assistant',
        content: '```shell\npwd\n```',
        timestamp: 'tool-call',
      });
      eventHandlers!.onMessageAdded!({
        role: 'tool',
        content: '/workspace',
        timestamp: 'tool-result',
      });
      eventHandlers!.onMessageAdded!({
        role: 'system',
        content: 'Error: Error code: 402 - Payment required',
        timestamp: 'refusal',
      });
      eventHandlers!.onError!('Error code: 402 - Payment required');
    });
    const history = log$
      .get()
      .map(({ role, content, timestamp }) => ({ role, content, timestamp }));
    await act(async () => {
      await result.current.retryMessage(
        log$.get().find((message) => message.timestamp === prompt.timestamp)!
      );
    });
    expect(step).toHaveBeenCalledTimes(2);
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
    expect(
      log$.get().map(({ role, content, timestamp }) => ({ role, content, timestamp }))
    ).toEqual(history);
    expect(log$.get().find((message) => message.timestamp === prompt.timestamp)?._status).toBe(
      'sent'
    );
    expect(editMessageApi).not.toHaveBeenCalled();
  });

  it('does not resurrect a superseded step owner after the newer HTTP acknowledgment', async () => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const first = renderHook(() => useConversation('chat-placeholder'));
    await waitFor(() => expect(eventHandlers).toBeDefined());
    const second = renderHook(() => useConversation('chat-placeholder'));
    let acknowledgeOld!: () => void;
    step.mockImplementationOnce(() => new Promise<void>((resolve) => (acknowledgeOld = resolve)));
    let oldSend!: Promise<void>;
    await act(async () => {
      oldSend = first.result.current.sendMessage({ message: 'Superseded step' });
    });
    await new Promise((resolve) => setTimeout(resolve, 2));
    await act(async () => {
      await second.result.current.sendMessage({ message: 'Accepted newer step' });
    });
    await act(async () => {
      acknowledgeOld();
      await oldSend;
    });
    act(() => eventHandlers!.onError!('Error code: 402 - Payment required'));
    const log = conv$.data.log.get();
    expect(log.find((message) => message.content === 'Superseded step')?._status).toBe('sent');
    expect(log.find((message) => message.content === 'Accepted newer step')?._status).toBe(
      'failed'
    );
  });

  it.each(['confirm', 'skip'] as const)(
    'restores inline Retry when a reconnected tool is %s',
    async (action) => {
      const conv$ = conversations$.get('chat-placeholder')!;
      conv$.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Reconnect this turn' });
      });
      const prompt = conv$.data.log.get().at(-1)!;
      act(() => {
        eventHandlers!.onMessageAdded!({
          role: 'assistant',
          content: 'Tool call',
          timestamp: 'tool-call',
        });
        eventHandlers!.onToolPending!('tool-1', { tool: 'shell', content: 'pwd' }, false);
        eventHandlers!.onStepComplete!();
        eventHandlers!.onError!('Connection lost and max reconnects reached');
        // Repeated connection failures must not discard the recovery candidate.
        eventHandlers!.onError!('Connection lost and max reconnects reached');
        eventHandlers!.onConnected!();
        eventHandlers!.onReconnectState!({
          generating: false,
          pendingTools: [
            {
              tool_id: 'tool-1',
              tooluse: { tool: 'shell', content: 'pwd' },
              auto_confirm: false,
            },
          ],
        });
      });
      (primaryClient.confirmTool as jest.Mock).mockImplementationOnce(async () => {
        if (action === 'confirm') {
          eventHandlers!.onToolExecuting!('tool-1');
          eventHandlers!.onToolComplete!('tool-1', 10, true);
        }
        eventHandlers!.onMessageAdded!({
          role: 'system',
          content: 'Error: Error code: 402 - Payment required',
          timestamp: 'refusal',
        });
        eventHandlers!.onError!('Error code: 402 - Payment required');
      });
      await act(async () => {
        await result.current.confirmTool(action);
      });
      const failed = conv$.data.log
        .get()
        .find((message) => message.timestamp === prompt.timestamp)!;
      expect(failed._status).toBe('failed');
      await act(async () => {
        await result.current.retryMessage(failed);
      });
      expect(step).toHaveBeenCalledTimes(2);
      expect(sendMessageApi).toHaveBeenCalledTimes(1);
      expect(editMessageApi).not.toHaveBeenCalled();
    }
  );

  it.each(['stop', 'server interruption', 'idle reconnect', 'newer prompt'])(
    'does not recover a disconnected owner after %s',
    async (supersededBy) => {
      const conv$ = conversations$.get('chat-placeholder')!;
      conv$.needsInitialStep.set(false);
      const { result } = renderHook(() => useConversation('chat-placeholder'));
      await act(async () => {
        await result.current.sendMessage({ message: 'Disconnected turn' });
      });
      act(() => eventHandlers!.onError!('Connection lost and max reconnects reached'));
      if (supersededBy === 'stop') {
        await act(async () => {
          await result.current.interruptGeneration();
        });
      } else if (supersededBy === 'server interruption') {
        act(() => eventHandlers!.onInterrupted!());
      } else if (supersededBy === 'idle reconnect') {
        act(() => eventHandlers!.onReconnectState!({ generating: false, pendingTools: [] }));
      } else {
        await new Promise((resolve) => setTimeout(resolve, 2));
        await act(async () => {
          await result.current.sendMessage({ message: 'Newer turn' });
        });
      }
      act(() => {
        eventHandlers!.onReconnectState!({ generating: true, pendingTools: [] });
        eventHandlers!.onError!('Error code: 402 - Payment required');
      });
      expect(
        conv$.data.log.get().find((message) => message.content === 'Disconnected turn')?._status
      ).toBe('sent');
      if (supersededBy === 'newer prompt') {
        expect(
          conv$.data.log.get().find((message) => message.content === 'Newer turn')?._status
        ).toBe('failed');
      }
    }
  );

  it('restores ownership for a still-generating turn after reconnect', async () => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Still active on server' });
    });
    act(() => {
      eventHandlers!.onError!('Connection lost and max reconnects reached');
      eventHandlers!.onReconnectState!({ generating: true, pendingTools: [] });
      eventHandlers!.onError!('Error code: 401 - Unauthorized');
    });
    const failed = conv$.data.log.get().at(-1)!;
    expect(failed._status).toBe('failed');
    await act(async () => {
      await result.current.retryMessage(failed);
    });
    expect(step).toHaveBeenCalledTimes(2);
    expect(sendMessageApi).toHaveBeenCalledTimes(1);
  });

  it('does not blame a disconnected turn for the next prompt early refusal', async () => {
    const conv$ = conversations$.get('chat-placeholder')!;
    conv$.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Disconnected turn' });
    });
    act(() => eventHandlers!.onError!('Connection lost and max reconnects reached'));
    await new Promise((resolve) => setTimeout(resolve, 2));
    step.mockImplementationOnce(async () => {
      eventHandlers!.onError!('Error code: 402 - Payment required');
    });
    await act(async () => {
      await result.current.sendMessage({ message: 'New refused turn' });
    });
    const log = conv$.data.log.get();
    expect(log.find((message) => message.content === 'Disconnected turn')?._status).toBe('sent');
    expect(log.find((message) => message.content === 'New refused turn')?._status).toBe('failed');
  });

  it('does not mark a saved prompt failed when the event stream loses its connection', async () => {
    conversations$.get('chat-placeholder')?.needsInitialStep.set(false);
    const { result } = renderHook(() => useConversation('chat-placeholder'));
    await act(async () => {
      await result.current.sendMessage({ message: 'Still generating' });
    });
    act(() => {
      eventHandlers!.onError!('Connection lost and max reconnects reached');
    });
    expect(conversations$.get('chat-placeholder')!.data.log.get().at(-1)).toMatchObject({
      content: 'Still generating',
      _status: 'sent',
    });
  });
});
