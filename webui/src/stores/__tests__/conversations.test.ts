import { describe, it, expect, beforeEach } from '@jest/globals';
import {
  conversations$,
  initConversation,
  updateConversationData,
  replaceLog,
  prependLogPage,
  toAbsoluteIndex,
  toLocalIndex,
} from '../conversations';
import type { ConversationResponse } from '@/types/api';
import type { Message } from '@/types/conversation';

function makeResponse(overrides: Partial<ConversationResponse> = {}): ConversationResponse {
  return {
    id: 'test-conv',
    name: 'Test',
    log: [],
    logfile: 'test-conv',
    branches: {},
    workspace: '/workspace',
    ...overrides,
  };
}

function msg(role: Message['role'], content = ''): Message {
  return { role, content };
}

describe('index translation helpers', () => {
  it('toAbsoluteIndex adds offset to local index', () => {
    expect(toAbsoluteIndex(150, 0)).toBe(150);
    expect(toAbsoluteIndex(150, 5)).toBe(155);
    expect(toAbsoluteIndex(0, 3)).toBe(3);
  });

  it('toLocalIndex subtracts offset from absolute index', () => {
    expect(toLocalIndex(150, 150)).toBe(0);
    expect(toLocalIndex(150, 155)).toBe(5);
    expect(toLocalIndex(0, 3)).toBe(3);
  });

  it('round-trips: toLocalIndex(offset, toAbsoluteIndex(offset, local)) === local', () => {
    const offset = 42;
    for (const local of [0, 1, 10, 99]) {
      expect(toLocalIndex(offset, toAbsoluteIndex(offset, local))).toBe(local);
    }
  });
});

describe('updateConversationData window metadata', () => {
  const id = 'test-conv';

  beforeEach(() => {
    initConversation(id);
  });

  it('sets logOffset=0 and hasMoreBefore=false for full-log response', () => {
    const data = makeResponse({ log: [msg('user', 'hi'), msg('assistant', 'hello')] });
    updateConversationData(id, data);

    const conv = conversations$.get(id);
    expect(conv?.logOffset.get()).toBe(0);
    expect(conv?.hasMoreBefore.get()).toBe(false);
    expect(conv?.isWindowHydrated.get()).toBe(true);
  });

  it('extracts logOffset from "before" cursor when has_more=true', () => {
    // Server returns has_more=true, before=150 (start of the window).
    // log[0] is at absolute index 150.
    const data = makeResponse({
      log: [msg('user', 'page start')],
      has_more: true,
      before: 150,
      total_messages: 200,
    });
    updateConversationData(id, data);

    const conv = conversations$.get(id);
    expect(conv?.logOffset.get()).toBe(150);
    expect(conv?.hasMoreBefore.get()).toBe(true);
    expect(conv?.isWindowHydrated.get()).toBe(true);
  });

  it('sets logOffset=0 when has_more=false (no cursor needed)', () => {
    const data = makeResponse({
      log: [msg('user', 'first message')],
      has_more: false,
      total_messages: 1,
    });
    updateConversationData(id, data);

    const conv = conversations$.get(id);
    expect(conv?.logOffset.get()).toBe(0);
    expect(conv?.hasMoreBefore.get()).toBe(false);
  });

  it('marks conversation as hydrated after update', () => {
    initConversation(id); // isWindowHydrated starts false for placeholder
    expect(conversations$.get(id)?.isWindowHydrated.get()).toBe(false);

    updateConversationData(id, makeResponse());
    expect(conversations$.get(id)?.isWindowHydrated.get()).toBe(true);
  });
});

describe('replaceLog window metadata reset', () => {
  const id = 'test-conv-2';

  beforeEach(() => {
    initConversation(id);
    // Simulate a windowed state (e.g., after paginated open)
    updateConversationData(id, makeResponse({ has_more: true, before: 100, total_messages: 200 }));
  });

  it('resets logOffset and hasMoreBefore to full-log defaults after server mutation', () => {
    const fullLog = [msg('user', 'hi'), msg('assistant', 'hello')];
    replaceLog(id, fullLog);

    const conv = conversations$.get(id);
    expect(conv?.logOffset.get()).toBe(0);
    expect(conv?.hasMoreBefore.get()).toBe(false);
    expect(conv?.isWindowHydrated.get()).toBe(true);
  });
});

describe('prependLogPage', () => {
  const id = 'test-conv-3';

  beforeEach(() => {
    initConversation(id);
    // Start with last 50 messages loaded (logOffset=150, total=200)
    const currentPage = [msg('user', 'page 2')];
    updateConversationData(
      id,
      makeResponse({ log: currentPage, has_more: true, before: 150, total_messages: 200 })
    );
  });

  it('prepends older messages and updates logOffset', () => {
    const olderPage = [msg('system', 'old message')];
    const olderOffset = 100; // absolute index of olderPage[0]
    prependLogPage(id, olderPage, olderOffset, true);

    const conv = conversations$.get(id);
    const log = conv?.data.log.get();
    expect(log?.length).toBe(2); // 1 older + 1 current
    expect(log?.[0].role).toBe('system'); // older first
    expect(log?.[1].role).toBe('user'); // current second
    expect(conv?.logOffset.get()).toBe(100);
    expect(conv?.hasMoreBefore.get()).toBe(true);
  });

  it('sets hasMoreBefore=false when prepending the first page', () => {
    const firstPage = [msg('system', 'very first message')];
    prependLogPage(id, firstPage, 0, false);

    const conv = conversations$.get(id);
    expect(conv?.logOffset.get()).toBe(0);
    expect(conv?.hasMoreBefore.get()).toBe(false);
  });
});
