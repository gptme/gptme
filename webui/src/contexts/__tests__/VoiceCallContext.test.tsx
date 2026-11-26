import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { VoiceCallProvider, useVoiceCall, isCallFor } from '../VoiceCallContext';
import type { VoiceTarget } from '@/hooks/useVoiceSession';
import type { IApiClient } from '@/utils/api';

// Capture the resolver each call hands to the session hook instead of running
// the real audio pipeline.
const resolvers: Array<() => Promise<string>> = [];
jest.mock('@/hooks/useVoiceSession', () => ({
  useVoiceSession: () => ({
    state: 'connecting',
    error: null,
    level: 0,
    start: (target: VoiceTarget) => {
      if (typeof target === 'function') resolvers.push(target);
    },
    stop: jest.fn(),
    commit: jest.fn(),
    sendText: jest.fn(),
  }),
}));

const client = {
  createVoiceSession: jest.fn(async (id: string, provider?: string) => ({
    provider: provider ?? 'openai',
    ticket: `t-${id}`,
    path: `/api/v2/conversations/${id}/voice`,
    expires_in: 60,
  })),
  voiceWebSocketUrl: (s: { path: string; ticket: string }) => `ws://x${s.path}?ticket=${s.ticket}`,
} as unknown as IApiClient;

const wrapper = ({ children }: { children: ReactNode }) => (
  <VoiceCallProvider>{children}</VoiceCallProvider>
);

beforeEach(() => {
  resolvers.length = 0;
});

describe('VoiceCallProvider', () => {
  it('binds the call to the conversation a new chat resolves to', async () => {
    const { result } = renderHook(() => useVoiceCall(), { wrapper });
    act(() => {
      result.current.start(async () => 'new-chat', { client, serverId: 'srv-2' });
    });
    let url = '';
    await act(async () => {
      url = await resolvers[0]();
    });
    expect(url).toBe('ws://x/api/v2/conversations/new-chat/voice?ticket=t-new-chat');
    expect(result.current.conversationId).toBe('new-chat');
    expect(result.current.serverId).toBe('srv-2');
  });

  it('a superseded call cannot re-bind the current call', async () => {
    let finishA: (id: string) => void = () => {};
    const { result } = renderHook(() => useVoiceCall(), { wrapper });
    act(() => {
      result.current.start(() => new Promise<string>((r) => (finishA = r)), { client });
    });
    const resolveA = resolvers[0]();
    act(() => {
      result.current.stop();
      result.current.start('chat-b', { client, provider: 'grok' });
    });
    await act(async () => {
      finishA('chat-a');
      await expect(resolveA).rejects.toThrow('superseded');
    });
    expect(result.current.conversationId).toBe('chat-b');
    expect(result.current.provider).toBe('grok');
  });
});

describe('isCallFor', () => {
  it('matches conversation and owning server', () => {
    const call = { conversationId: 'c', serverId: null };
    expect(isCallFor(call, 'c', undefined)).toBe(true);
    expect(isCallFor(call, 'c', 'srv-2')).toBe(false);
    expect(isCallFor({ conversationId: 'c', serverId: 'srv-2' }, 'c', 'srv-2')).toBe(true);
    expect(isCallFor(call, undefined, undefined)).toBe(false);
    expect(isCallFor(null, 'c', undefined)).toBe(false);
  });
});
