import { act, renderHook } from '@testing-library/react';
import { useMessageQueue } from '../useMessageQueue';

describe('durable message queue', () => {
  beforeEach(() => localStorage.clear());

  it('persists immediately and restores only the owning conversation', () => {
    const first = renderHook(() => useMessageQueue('owner-a'));
    act(() => first.result.current[1]([{ text: 'keep me', options: { temperature: 0.5 } }]));
    expect(JSON.parse(localStorage.getItem('owner-a:queue')!)[0].text).toBe('keep me');
    first.unmount();
    const restored = renderHook(() => useMessageQueue('owner-a'));
    expect(restored.result.current[0][0]).toMatchObject({
      text: 'keep me',
      options: { temperature: 0.5 },
    });
    const other = renderHook(() => useMessageQueue('owner-b'));
    expect(other.result.current[0]).toEqual([]);
  });

  it('retains File objects in memory, but blocks restored prompts needing reattachment', () => {
    const file = new File(['bytes'], 'report.txt');
    const first = renderHook(() => useMessageQueue('owner-a'));
    act(() => first.result.current[1]([{ text: 'read this', options: { pendingFiles: [file] } }]));
    expect(first.result.current[0][0].options.pendingFiles![0]).toBe(file);
    first.unmount();
    const restored = renderHook(() => useMessageQueue('owner-a'));
    expect(restored.result.current[0][0]).toMatchObject({
      uploadFailed: true,
      missingFiles: ['report.txt'],
    });
    expect(restored.result.current[0][0].options.pendingFiles).toBeUndefined();
  });

  it('preserves pending work if browser storage is full', () => {
    const state = renderHook(() => useMessageQueue('owner-a'));
    const write = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    expect(state.result.current[1]([{ text: 'draft survives', options: {} }])).toBe(false);
    expect(state.result.current[0]).toEqual([]);
    write.mockRestore();
  });

  it('shares updates between split-pane composers', () => {
    const first = renderHook(() => useMessageQueue('split'));
    const second = renderHook(() => useMessageQueue('split'));
    act(() => first.result.current[1]((prev) => [...prev, { text: 'first', options: {} }]));
    act(() => second.result.current[1]((prev) => [...prev, { text: 'second', options: {} }]));
    expect(first.result.current[0].map((item) => item.text)).toEqual(['first', 'second']);
    expect(second.result.current[0]).toEqual(first.result.current[0]);
    expect(JSON.parse(localStorage.getItem('split:queue')!)).toHaveLength(2);
  });

  it('starts from storage when a pending setter outlives every composer', () => {
    const state = renderHook(() => useMessageQueue('owner-a'));
    const setQueue = state.result.current[1];
    act(() =>
      setQueue([
        { id: 'head', text: 'sending', sending: true, options: {} },
        { id: 'next', text: 'still queued', options: {} },
      ])
    );
    state.unmount();
    act(() => setQueue((prev) => prev.filter((item) => item.id !== 'head')));
    expect(
      JSON.parse(localStorage.getItem('owner-a:queue')!).map((i: { id: string }) => i.id)
    ).toEqual(['next']);
  });

  it('marks a send interrupted by reload as an unconfirmed save', () => {
    const first = renderHook(() => useMessageQueue('owner-a'));
    act(() => first.result.current[1]([{ text: 'in flight', sending: true, options: {} }]));
    first.unmount();
    const restored = renderHook(() => useMessageQueue('owner-a'));
    expect(restored.result.current[0][0]).toMatchObject({
      sending: false,
      uploadFailed: true,
      saveUnknown: true,
    });
  });

  it('keeps async callbacks scoped to their original owner', () => {
    const state = renderHook(({ owner }) => useMessageQueue(owner), {
      initialProps: { owner: 'a' },
    });
    const setA = state.result.current[1];
    state.rerender({ owner: 'b' });
    act(() => setA([{ text: 'belongs to a', options: {} }]));
    expect(state.result.current[0]).toEqual([]);
    state.rerender({ owner: 'a' });
    expect(state.result.current[0][0].text).toBe('belongs to a');
  });
});
