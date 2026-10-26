import { observable } from '@legendapp/state';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useApi } from '@/contexts/ApiContext';
import { getClientForServer } from '@/stores/serverClients';
import { conversations$, initConversation, setConnectionStatus } from '@/stores/conversations';
import { useConversation } from '../useConversation';
import { ApiClientError } from '@/utils/api';
import { request } from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';

jest.mock('@/contexts/ApiContext', () => ({
  useApi: jest.fn(),
}));

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: jest.fn(),
}));

jest.mock('@/components/ui/use-toast', () => ({
  useToast: () => ({ toast: jest.fn() }),
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
  const closeEventStream = jest.fn();
  const getChatConfig = jest.fn().mockResolvedValue(null);
  let eventHandlers:
    | {
        onConnected?: () => void;
        onMessageStart?: () => void;
        onStepComplete?: () => void;
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
      0.8
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
      1
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
});
