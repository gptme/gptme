jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  CLOUD_BASE_URL: 'https://gptme.ai',
}));

import { ApiClient, mergeReconnectSnapshot } from '../api';
import {
  conversations$,
  initConversation,
  replaceLog,
  setGenerating,
  updateConversationData,
} from '@/stores/conversations';
import type { ConversationResponse } from '@/types/api';
import type { Message } from '@/types/conversation';

const ID = 'reconnect-conv';
const user: Message = { role: 'user', content: 'q', timestamp: '2026-11-24T10:00:00.000Z' };
const streaming = {
  role: 'assistant' as const,
  content: 'Hel',
  timestamp: '2026-11-24T10:00:01.123Z', // client-generated until completion
  isComplete: false,
};
const reply: Message = {
  role: 'assistant',
  content: 'Hello world',
  timestamp: '2026-11-24T10:00:02Z',
};
const toolOutput: Message = {
  role: 'system',
  content: 'tool output',
  timestamp: '2026-11-24T10:00:03Z',
};

function response(log: Message[], extra: Partial<ConversationResponse> = {}): ConversationResponse {
  return { id: ID, name: 'c', log, logfile: ID, branches: {}, workspace: '.', ...extra };
}

describe('resyncLogAfterReconnect (#5145)', () => {
  let client: ApiClient;
  const state = () => conversations$.get(ID)!;

  beforeEach(() => {
    conversations$.set(new Map());
    initConversation(ID);
    updateConversationData(ID, response([user]));
    client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
  });

  it('recovers a reply and tool output persisted while the stream was down', async () => {
    replaceLog(ID, [user, streaming]);
    setGenerating(ID, false);
    const get = jest
      .spyOn(client, 'getConversation')
      .mockResolvedValue(response([user, reply, toolOutput]));

    await client.resyncLogAfterReconnect(ID);

    expect(get).toHaveBeenCalledWith(ID, { limit: 50 });
    expect(state().data.log.get()).toEqual([user, reply, toolOutput]);
  });

  it('keeps the live assistant when generation is still running', async () => {
    replaceLog(ID, [user, streaming]);
    setGenerating(ID, true);
    jest.spyOn(client, 'getConversation').mockResolvedValue(response([user, toolOutput]));

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user, toolOutput, streaming]);
  });

  it('keeps older loaded pages and unsaved prompts', async () => {
    const older: Message = { role: 'user', content: 'old', timestamp: '2026-11-24T09:00:00Z' };
    const failed: Message = {
      role: 'user',
      content: 'unsent',
      timestamp: '2026-11-24T10:00:05Z',
      _status: 'failed',
    };
    updateConversationData(ID, response([older, user], { has_more: true, before: 7 }));
    replaceLog(ID, [older, user, failed]);
    state().logOffset.set(7);
    state().hasMoreBefore.set(true);
    jest
      .spyOn(client, 'getConversation')
      .mockResolvedValue(response([user, reply], { has_more: true, before: 8 }));

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([older, user, reply, failed]);
    expect(state().logOffset.get()).toBe(7);
    expect(state().hasMoreBefore.get()).toBe(true);
  });

  it('replaces the window when the outage outgrew the snapshot', async () => {
    replaceLog(ID, [user, streaming]);
    jest
      .spyOn(client, 'getConversation')
      .mockResolvedValue(response([reply, toolOutput], { has_more: true, before: 60 }));

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([reply, toolOutput]);
    expect(state().logOffset.get()).toBe(60);
    expect(state().hasMoreBefore.get()).toBe(true);
  });

  it('discards the snapshot when the log was edited during the request', async () => {
    replaceLog(ID, [user, streaming]);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      replaceLog(ID, [user]); // e.g. a delete returned the authoritative log
      return response([user, reply]);
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user]);
  });

  it('skips non-main branch views', async () => {
    state().currentBranch.set('alt');
    const get = jest.spyOn(client, 'getConversation');

    await client.resyncLogAfterReconnect(ID);

    expect(get).not.toHaveBeenCalled();
  });

  it('does not bump the log revision when nothing changed', async () => {
    replaceLog(ID, [user, reply]);
    const revision = state().logRevision.get();
    jest.spyOn(client, 'getConversation').mockResolvedValue(response([user, reply]));

    await client.resyncLogAfterReconnect(ID);

    expect(state().logRevision.get()).toBe(revision);
  });
});

describe('mergeReconnectSnapshot', () => {
  const opts = { generating: false, snapshotOffset: 0, snapshotHasMore: false };

  it('preserves the local timestamp spelling and client status of saved rows', () => {
    const local: Message = { ...user, timestamp: '2026-11-24T10:00:00Z', _status: 'sent' };
    const { log } = mergeReconnectSnapshot([local], [user, reply], opts);
    expect(log).toEqual([{ ...user, timestamp: local.timestamp, _status: 'sent' }, reply]);
  });

  it('does not duplicate timestamp-less rows the snapshot also contains', () => {
    const system: Message = { role: 'system', content: 'Instructions' };
    const { log } = mergeReconnectSnapshot([system, user], [system, user, reply], opts);
    expect(log).toEqual([system, user, reply]);
  });
});
