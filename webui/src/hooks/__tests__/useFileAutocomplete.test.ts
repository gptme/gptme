import { renderHook, act } from '@testing-library/react';
import type React from 'react';
import { useFileAutocomplete } from '../useFileAutocomplete';
import type { FileType } from '@/types/workspace';

const mockListWorkspace = jest.fn();

jest.mock('@/utils/workspaceApi', () => ({
  useWorkspaceApi: () => ({ listWorkspace: mockListWorkspace }),
}));

function file(name: string): FileType {
  return {
    name,
    path: name,
    type: 'file',
    size: 1,
    modified: '2026-10-19T12:00:00Z',
    mime_type: 'text/plain',
  };
}

describe('useFileAutocomplete listing freshness', () => {
  let serverFiles: FileType[];

  beforeEach(() => {
    serverFiles = [file('old.txt')];
    mockListWorkspace.mockReset();
    mockListWorkspace.mockImplementation(async () => [...serverFiles]);
  });

  function setup() {
    return renderHook(() => useFileAutocomplete({ conversationId: 'conv-1' }));
  }

  async function type(
    result: ReturnType<typeof setup>['result'],
    value: string,
    cursor = value.length
  ) {
    await act(async () => {
      await result.current.handleInputChange(value, cursor);
    });
  }

  it('reuses one listing while the popup stays open', async () => {
    const { result } = setup();
    await type(result, '@o');
    await type(result, '@ol');
    await type(result, '@old');

    expect(mockListWorkspace).toHaveBeenCalledTimes(1);
    expect(result.current.state.files.map((f) => f.name)).toEqual(['old.txt']);
  });

  it('discovers a file created after the popup closed', async () => {
    const { result } = setup();
    await type(result, '@old');
    expect(result.current.state.files.map((f) => f.name)).toEqual(['old.txt']);

    await type(result, '');
    serverFiles = [file('old.txt'), file('new.txt')];
    await type(result, '@new');

    expect(mockListWorkspace).toHaveBeenCalledTimes(2);
    expect(result.current.state.files.map((f) => f.name)).toEqual(['new.txt']);
  });

  it('refreshes after Escape dismisses the popup', async () => {
    const { result } = setup();
    await type(result, '@o');
    expect(result.current.state.files.map((f) => f.name)).toEqual(['old.txt']);

    act(() => {
      result.current.handleKeyDown({
        key: 'Escape',
        preventDefault: () => {},
      } as unknown as React.KeyboardEvent);
    });
    serverFiles = [file('old.txt'), file('new.txt')];
    await type(result, '@new');

    expect(result.current.state.files.map((f) => f.name)).toEqual(['new.txt']);
  });

  it('ignores a listing that resolves after the popup closed', async () => {
    const { result } = setup();
    let resolveFirst!: (files: FileType[]) => void;
    mockListWorkspace.mockImplementationOnce(
      () =>
        new Promise<FileType[]>((resolve) => {
          resolveFirst = resolve;
        })
    );

    // Start a request, then close the popup before it resolves.
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.handleInputChange('@old', 4) as unknown as Promise<void>;
    });
    act(() => result.current.close());

    serverFiles = [file('old.txt'), file('new.txt')];
    await act(async () => {
      resolveFirst([file('old.txt')]);
      await pending;
    });

    await type(result, '@new');

    expect(mockListWorkspace).toHaveBeenCalledTimes(2);
    expect(result.current.state.files.map((f) => f.name)).toEqual(['new.txt']);
  });

  it('drops a deleted file after the popup closed', async () => {
    const { result } = setup();
    await type(result, '@old');
    act(() => result.current.close());
    serverFiles = [];
    await type(result, '@old');

    expect(result.current.state.files).toEqual([]);
  });
});
