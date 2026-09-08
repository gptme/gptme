import { observable } from '@legendapp/state';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useApi } from '@/contexts/ApiContext';
import { conversations$, initConversation } from '@/stores/conversations';
import { useConversation } from '../useConversation';

jest.mock('@/contexts/ApiContext', () => ({
  useApi: jest.fn(),
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
  const subscribeToEvents = jest.fn().mockResolvedValue(undefined);
  const step = jest.fn().mockResolvedValue(undefined);
  const interruptGenerationApi = jest.fn().mockResolvedValue(undefined);
  const editMessageApi = jest.fn();
  const rerunTools = jest.fn().mockResolvedValue(undefined);
  const closeEventStream = jest.fn();
  const getChatConfig = jest.fn().mockResolvedValue(null);
  let eventHandlers:
    | {
        onConnected?: () => void;
        onMessageStart?: () => void;
      }
    | undefined;

  beforeEach(() => {
    conversations$.set(new Map());
    subscribeToEvents.mockImplementation((_conversationId, handlers) => {
      eventHandlers = handlers;
      return Promise.resolve();
    });
    step.mockReset().mockResolvedValue(undefined);
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

    mockedUseApi.mockReturnValue({
      getClient: () =>
        ({
          subscribeToEvents,
          step,
          interruptGeneration: interruptGenerationApi,
          editMessage: editMessageApi,
          rerunTools,
          closeEventStream,
          getConversation: jest.fn(),
          getChatConfig,
          waitForConversationCreation: jest.fn().mockResolvedValue(undefined),
        }) as any,
      isConnected$: observable(true),
    } as any);
  });

  afterEach(() => {
    eventHandlers = undefined;
    jest.clearAllMocks();
  });

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
});
