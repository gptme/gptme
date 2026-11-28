jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  CLOUD_BASE_URL: 'https://gptme.ai',
}));

import { ApiClient, mergeReconnectSnapshot } from '../api';
import {
  conversations$,
  initConversation,
  prependLogPage,
  replaceLog,
  setGenerating,
  setCurrentBranch,
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

  it.each(['pending', 'failed'] as const)(
    'keeps a colliding unsaved %s prompt separate from the saved prompt',
    async (status) => {
      const draft: Message = {
        ...user,
        content: 'unsaved draft',
        _status: status,
      };
      replaceLog(ID, [draft]);
      jest.spyOn(client, 'getConversation').mockResolvedValue(response([user]));

      await client.resyncLogAfterReconnect(ID);

      expect(state().data.log.get()).toEqual([user, draft]);
    }
  );

  it('does not match a newly acknowledged prompt to a colliding peer prompt', async () => {
    const draft: Message = { ...user, content: 'unsaved draft', _status: 'pending' };
    replaceLog(ID, [draft]);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      state().data.log[0]._status.set('sent');
      return response([user]);
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user, { ...draft, _status: 'sent' }]);
  });

  it('keeps an unsaved attachment prompt separate from a colliding text-only prompt', async () => {
    const draft: Message = { ...user, files: ['draft.txt'], _status: 'pending' };
    replaceLog(ID, [draft]);
    jest.spyOn(client, 'getConversation').mockResolvedValue(response([user]));

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user, draft]);
  });

  it('recovers a reply and tool output persisted while the stream was down', async () => {
    replaceLog(ID, [user, streaming]);
    setGenerating(ID, false);
    const get = jest
      .spyOn(client, 'getConversation')
      .mockResolvedValue(response([user, reply, toolOutput]));

    await client.resyncLogAfterReconnect(ID);

    expect(get).toHaveBeenCalledWith(
      ID,
      { limit: 50 },
      expect.objectContaining({ detached: true, signal: expect.any(AbortSignal) })
    );
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

  it('resets the window when the anchor row moved to a different server index', async () => {
    // A peer deleted an earlier message during the outage: `user` was server
    // index 8 locally (offset 7 + 1) but the snapshot now starts it at 7.
    const older: Message = { role: 'user', content: 'old', timestamp: '2026-11-24T09:00:00Z' };
    replaceLog(ID, [older, user]);
    state().logOffset.set(7);
    state().hasMoreBefore.set(true);
    jest
      .spyOn(client, 'getConversation')
      .mockResolvedValue(response([user, reply], { has_more: true, before: 7 }));

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user, reply]);
    expect(state().logOffset.get()).toBe(7);
  });

  it('keeps rows that arrived on the new stream while the request was in flight', async () => {
    const peerPrompt: Message = {
      role: 'user',
      content: 'peer',
      timestamp: '2026-11-24T10:00:09Z',
    };
    replaceLog(ID, [user]);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      conversations$.get(ID)!.data.log.push(peerPrompt); // SSE message_added
      return response([reply, toolOutput], { has_more: true, before: 60 });
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([reply, toolOutput, peerPrompt]);
  });

  it('does not count an older page loaded during the request as stream arrivals', async () => {
    const older: Message = { role: 'user', content: 'old', timestamp: '2026-11-24T09:00:00Z' };
    replaceLog(ID, [user]);
    state().logOffset.set(7);
    state().hasMoreBefore.set(true);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      prependLogPage(ID, [older], 6, true); // "Load older messages" finished first
      return response([reply, toolOutput], { has_more: true, before: 60 });
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([reply, toolOutput]);
    expect(state().logOffset.get()).toBe(60);
  });

  it('keeps a stream arrival when an older page is shorter than the cursor move', async () => {
    // Another tab shortened the chat: the page holds 1 row but the cursor moved 2.
    const older: Message = { role: 'user', content: 'old', timestamp: '2026-11-24T09:00:00Z' };
    const peerPrompt: Message = {
      role: 'user',
      content: 'peer',
      timestamp: '2026-11-24T10:00:09Z',
    };
    replaceLog(ID, [user]);
    state().logOffset.set(7);
    state().hasMoreBefore.set(true);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      prependLogPage(ID, [older], 5, true);
      conversations$.get(ID)!.data.log.push(peerPrompt); // SSE message_added
      return response([reply, toolOutput], { has_more: true, before: 60 });
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([reply, toolOutput, peerPrompt]);
  });

  it('discards an in-flight snapshot once the stream is closed', async () => {
    replaceLog(ID, [user]);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      // The chat moved to another server: this client no longer owns it.
      client.closeEventStream(ID);
      return response([user, reply]);
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user]);
  });

  it('drops a queued resync when the stream is closed', async () => {
    replaceLog(ID, [user]);
    let releaseFirst!: () => void;
    const get = jest.spyOn(client, 'getConversation').mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseFirst = () => resolve(response([user, reply]));
        })
    );

    const first = client.resyncLogAfterReconnect(ID);
    const second = client.resyncLogAfterReconnect(ID);
    client.closeEventStream(ID);
    releaseFirst();
    await Promise.all([first, second]);

    expect(get).toHaveBeenCalledTimes(1);
    expect(state().data.log.get()).toEqual([user]);
  });

  it('serializes overlapping resyncs so the newer reconnect is not discarded', async () => {
    replaceLog(ID, [user]);
    let releaseFirst!: () => void;
    const get = jest
      .spyOn(client, 'getConversation')
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseFirst = () => resolve(response([user, reply]));
          })
      )
      .mockResolvedValueOnce(response([user, reply, toolOutput]));

    const first = client.resyncLogAfterReconnect(ID);
    const second = client.resyncLogAfterReconnect(ID);
    expect(get).toHaveBeenCalledTimes(1);
    releaseFirst();
    await Promise.all([first, second]);

    expect(get).toHaveBeenCalledTimes(2);
    expect(state().data.log.get()).toEqual([user, reply, toolOutput]);
  });

  it('runs a queued resync even if the first request failed', async () => {
    replaceLog(ID, [user]);
    let rejectFirst!: (error: Error) => void;
    jest
      .spyOn(client, 'getConversation')
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectFirst = reject;
          })
      )
      .mockResolvedValueOnce(response([user, reply]));

    const first = client.resyncLogAfterReconnect(ID);
    const second = client.resyncLogAfterReconnect(ID);
    rejectFirst(new Error('offline'));
    await Promise.all([first, second]);

    expect(state().data.log.get()).toEqual([user, reply]);
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

  it('keeps a prompt that was pending when the request started (saved after the snapshot)', async () => {
    const prompt: Message = {
      role: 'user',
      content: 'new prompt',
      timestamp: '2026-11-24T10:00:05Z',
      _status: 'pending',
    };
    replaceLog(ID, [user, reply, prompt]);
    jest.spyOn(client, 'getConversation').mockImplementation(async () => {
      // The send succeeded while the GET was in flight; its snapshot predates it.
      const log = conversations$.get(ID)!.data.log;
      log[2]._status.set('sent');
      return response([user, reply]);
    });

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()).toEqual([user, reply, { ...prompt, _status: 'sent' }]);
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
  const opts = {
    generating: false,
    localOffset: 0,
    snapshotOffset: 0,
    snapshotHasMore: false,
    arrivedCount: 0,
  };

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

  it('folds a partial reply into its saved copy instead of duplicating it', () => {
    // The GET saw the saved reply before generation_complete reached the page.
    const partial = { ...streaming, content: 'Hello' };
    const { log } = mergeReconnectSnapshot([user, partial], [user, reply], {
      ...opts,
      generating: true,
    });
    // The row takes the saved text (generation_complete is not replayed after
    // a reconnect) and keeps the stream's timestamp, so a late
    // generation_complete still targets this row instead of adding another.
    expect(log).toEqual([user, { ...reply, timestamp: partial.timestamp }]);
  });

  it('drops a partial whose saved reply the server already finished', () => {
    // The reply finished and a tool ran during the outage, so the server is
    // generating a later message whose `generation_started` is never replayed.
    // Folding the stale prefix into the finished reply would hand it the
    // stream's timestamp and let the new reply overwrite it.
    const partial = { ...streaming, content: 'Hel' };
    const { log } = mergeReconnectSnapshot([user, partial], [user, reply, toolOutput], {
      ...opts,
      generating: true,
    });
    expect(log).toEqual([user, reply, toolOutput]);
  });

  it('keeps client-synthesized rows the server never stores', () => {
    const notice: Message = {
      role: 'system',
      content: 'Subagent finished',
      timestamp: '2026-11-24T10:00:04.567Z',
      _synthetic: true,
    };
    const { log } = mergeReconnectSnapshot([user, notice], [user, reply], opts);
    expect(log).toEqual([user, reply, notice]);
  });

  it('keeps a reply that started while the fetch was in flight', () => {
    // The new reply's empty placeholder is a prefix of the older saved reply,
    // so the supersede heuristic would drop it and strand every later token.
    const savedReply: Message = {
      role: 'assistant',
      content: 'Hello world',
      timestamp: '2026-11-24T10:00:02Z',
    };
    const started = {
      role: 'assistant' as const,
      content: '',
      timestamp: '2026-11-24T10:00:09.000Z',
      isComplete: false,
    };
    const { log } = mergeReconnectSnapshot([user, started], [user, savedReply, toolOutput], {
      ...opts,
      generating: true,
      startRows: [user],
    });
    expect(log).toEqual([user, savedReply, toolOutput, started]);
  });

  it('drops a failed send the server persisted and a peer deleted', () => {
    // Persistence 'saved' makes it a server row: keeping it after a peer
    // deleted it resurrects it at a fabricated position.
    const failedSaved: Message = {
      role: 'user',
      content: 'q2',
      timestamp: '2026-11-24T10:00:05Z',
      _status: 'failed',
      _retry: { persistence: 'saved', options: {} },
    };
    const { log } = mergeReconnectSnapshot([failedSaved, reply], [reply], opts);
    expect(log).toEqual([reply]);
  });

  it('keeps a failed send the server never persisted', () => {
    const failedDraft: Message = {
      role: 'user',
      content: 'q2',
      timestamp: '2026-11-24T10:00:05Z',
      _status: 'failed',
      _retry: { persistence: 'unknown', options: {} },
    };
    const { log } = mergeReconnectSnapshot([failedDraft, reply], [reply], opts);
    expect(log).toEqual([reply, failedDraft]);
  });

  it('drops a row a peer deleted during the outage instead of resurrecting it', () => {
    const victim: Message = {
      role: 'assistant',
      content: 'deleted by a peer',
      timestamp: '2026-11-24T10:00:01Z',
    };
    // Aligned overlap: the snapshot still starts at the same message, but the
    // peer deleted `victim`, so it must not be appended as if it arrived live.
    const { log } = mergeReconnectSnapshot([user, victim, reply], [user, reply], opts);
    expect(log).toEqual([user, reply]);
  });
});

describe('resyncLogAfterReconnect in-flight races (#5145)', () => {
  let client: ApiClient;
  const state = () => conversations$.get(ID)!;

  beforeEach(() => {
    conversations$.set(new Map());
    initConversation(ID);
    updateConversationData(ID, response([user]));
    client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
  });

  it('is not aborted by the next step() cancelling the step controller', async () => {
    replaceLog(ID, [user]);
    const stepController = new AbortController();
    (client as unknown as { controller: AbortController | null }).controller = stepController;
    let release!: () => void;
    jest
      .spyOn(client as unknown as { fetchJson: () => Promise<unknown> }, 'fetchJson')
      .mockImplementation(
        (...args: unknown[]) =>
          new Promise((resolve, reject) => {
            const signal = (args[1] as RequestInit | undefined)?.signal;
            signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError'))
            );
            release = () => resolve(response([user, reply]));
          })
      );

    const resync = client.resyncLogAfterReconnect(ID);
    stepController.abort(); // what the next step() does first
    release();
    await resync;

    expect(state().data.log.get()).toEqual([user, reply]);
  });

  it('keeps a reply completed by generation_complete while the GET is in flight', async () => {
    replaceLog(ID, [user, streaming]);
    setGenerating(ID, true);
    let release!: () => void;
    jest.spyOn(client, 'getConversation').mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(response([user]));
        })
    );

    const resync = client.resyncLogAfterReconnect(ID);
    // onMessageComplete: full content, server timestamp, isComplete (no revision bump).
    const completed = { ...reply, isComplete: true } as Message;
    state().data.log[1].set(completed);
    setGenerating(ID, false);
    release();
    await resync;

    expect(state().data.log.get()).toEqual([user, completed]);
  });

  it('cancels a stalled history request instead of blocking later reconnects', async () => {
    replaceLog(ID, [user, streaming]);
    setGenerating(ID, false);
    let calls = 0;
    const get = jest
      .spyOn(client, 'getConversation')
      .mockImplementation(
        (
          _logfile: string,
          _params?: { limit?: number; before?: number },
          options?: { detached?: boolean; signal?: AbortSignal }
        ) => {
          calls += 1;
          if (calls === 1) {
            // Stalls forever unless the resync's own controller cancels it.
            return new Promise((_resolve, reject) => {
              options?.signal?.addEventListener('abort', () =>
                reject(new DOMException('aborted', 'AbortError'))
              );
            });
          }
          return Promise.resolve(response([user, reply]));
        }
      );

    jest.useFakeTimers();
    try {
      const resync = client.resyncLogAfterReconnect(ID);
      // A reconnect while the first request is stalled queues a rerun behind it.
      const queued = client.resyncLogAfterReconnect(ID);
      await jest.advanceTimersByTimeAsync(60_000);
      await resync;
      await queued;
    } finally {
      jest.useRealTimers();
    }

    expect(get).toHaveBeenCalledTimes(2);
    expect(state().data.log.get()).toEqual([user, reply]);
  });

  it('refreshes the branch arrays so a recovered reply survives a branch switch', async () => {
    replaceLog(ID, [user]);
    setGenerating(ID, false);
    jest
      .spyOn(client, 'getConversation')
      .mockResolvedValue(response([user, reply], { branches: { main: [user, reply] } }));

    await client.resyncLogAfterReconnect(ID);

    // Switching away and back replaces the window with `branches[branch]`; a
    // stale array would hide the recovered reply and mark the gap hydrated.
    expect(state().data.branches.get().main).toEqual([user, reply]);
  });

  it('keeps full branch history when reconnect refreshes only a window', async () => {
    const older: Message = { role: 'user', content: 'older', timestamp: '2026-11-24T09:00:00Z' };
    updateConversationData(ID, response([user], { before: 1, has_more: true }));
    jest.spyOn(client, 'getConversation').mockResolvedValue(
      response([user, reply], {
        before: 1,
        has_more: true,
        branches: { main: [older, user, reply], alt: [older] },
      })
    );

    await client.resyncLogAfterReconnect(ID);
    expect(state().logOffset.get()).toBe(1);
    setCurrentBranch(ID, 'alt');
    setCurrentBranch(ID, 'main');
    expect(state().data.log.get()).toEqual([older, user, reply]);
    expect(state().logOffset.get()).toBe(0);
    expect(state().data.log.get()[1]).toEqual(user);
  });

  it('retries a timed-out history request instead of losing the recovery', async () => {
    replaceLog(ID, [user, streaming]);
    setGenerating(ID, false);
    let calls = 0;
    jest.spyOn(client, 'getConversation').mockImplementation(() => {
      calls += 1;
      if (calls === 1) {
        return new Promise((_resolve, reject) => {
          setTimeout(() => reject(new DOMException('timed out', 'AbortError')), 10_000);
        });
      }
      return Promise.resolve(response([user, reply]));
    });

    jest.useFakeTimers();
    try {
      const resync = client.resyncLogAfterReconnect(ID);
      await jest.advanceTimersByTimeAsync(10_500);
      await resync;
    } finally {
      jest.useRealTimers();
    }

    expect(calls).toBe(2);
    expect(state().data.log.get()).toEqual([user, reply]);
  });

  it('drops an attachment removed while the stream was down', async () => {
    const withFile: Message = { ...user, files: ['a.png'] };
    replaceLog(ID, [withFile, reply]);
    jest.spyOn(client, 'getConversation').mockResolvedValue(response([user, reply]));

    await client.resyncLogAfterReconnect(ID);

    expect(state().data.log.get()[0]).toEqual(user);
  });
});
