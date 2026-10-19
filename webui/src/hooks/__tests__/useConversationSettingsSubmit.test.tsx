import { observable } from '@legendapp/state';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useApi } from '@/contexts/ApiContext';
import { conversations$ } from '@/stores/conversations';
import { useConversationSettings } from '../useConversationSettings';
import type { ChatConfig } from '@/types/api';

jest.mock('@/contexts/ApiContext', () => ({
  useApi: jest.fn(),
}));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

const makeConfig = (): ChatConfig => ({
  chat: {
    name: 'test',
    model: 'openrouter/test/model',
    tools: ['shell'],
    tool_format: null,
    stream: true,
    interactive: false,
    workspace: '/tmp/workspace',
    system_prompt: 'be helpful',
  },
  env: {},
  mcp: {
    enabled: true,
    auto_start: false,
    servers: [
      {
        name: 'filesystem',
        enabled: true,
        command: 'npx',
        args: ['server.js', '--config={"a":1,"b":2}', ' padded ', ''],
        env: {},
      },
    ],
  },
});

describe('useConversationSettings submit', () => {
  const mockedUseApi = useApi as jest.MockedFunction<typeof useApi>;
  const updateChatConfig = jest.fn().mockResolvedValue(undefined);
  const getConversation = jest.fn().mockResolvedValue({ id: 'c1', name: 'test', log: [] });

  beforeEach(() => {
    updateChatConfig.mockClear();
    getConversation.mockClear();
    mockedUseApi.mockReturnValue({
      api: { updateChatConfig, getConversation } as never,
    } as never);
  });

  it('submits unchanged MCP argv and null tool format from a mounted form', async () => {
    const config = makeConfig();
    conversations$.set(
      new Map([
        ['c1', observable({ id: 'c1', data: { id: 'c1', log: [] }, chatConfig: config })],
      ]) as never
    );

    const { result } = renderHook(() => useConversationSettings('c1'));
    await waitFor(() => expect(result.current.chatConfig).toBeTruthy());

    act(() => {
      result.current.form.setValue('chat.name', 'renamed');
    });

    await act(async () => {
      await result.current.onSubmit(result.current.form.getValues());
    });

    expect(updateChatConfig).toHaveBeenCalledTimes(1);
    const submitted = updateChatConfig.mock.calls[0][1] as ChatConfig;
    expect(submitted.chat.name).toBe('renamed');
    expect(submitted.chat.tool_format).toBeNull();
    expect(submitted.mcp.servers[0].args).toEqual([
      'server.js',
      '--config={"a":1,"b":2}',
      ' padded ',
      '',
    ]);
  });
});
