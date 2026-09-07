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
});
