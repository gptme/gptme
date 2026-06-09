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
  const closeEventStream = jest.fn();
  let eventHandlers:
    | {
        onConnected?: () => void;
      }
    | undefined;

  beforeEach(() => {
    conversations$.set(new Map());
    subscribeToEvents.mockImplementation((_conversationId, handlers) => {
      eventHandlers = handlers;
      return Promise.resolve();
    });
    step.mockClear();
    closeEventStream.mockClear();

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
          closeEventStream,
          getConversation: jest.fn(),
          getChatConfig: jest.fn().mockResolvedValue(null),
        }) as any,
      isConnected$: observable(true),
    } as any);
  });

  afterEach(() => {
    eventHandlers = undefined;
    jest.clearAllMocks();
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
});
