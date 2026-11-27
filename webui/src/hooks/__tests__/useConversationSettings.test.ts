import { chatConfigToFormValues, formValuesToChatConfig } from '../useConversationSettings';
import type { ChatConfig } from '@/types/api';
import { formSchema } from '@/schemas/conversationSettings';

const makeConfig = (overrides: Partial<ChatConfig['chat']> = {}): ChatConfig => ({
  chat: {
    name: 'test',
    model: 'openrouter/test/model',
    tools: ['shell', 'save'],
    tool_format: null,
    stream: true,
    interactive: false,
    workspace: '/tmp/workspace',
    system_prompt: 'be helpful',
    temperature: 0.5,
    ...overrides,
  },
  env: { FOO: 'bar' },
  mcp: {
    enabled: true,
    auto_start: false,
    servers: [
      {
        name: 'filesystem',
        enabled: true,
        command: 'npx',
        args: ['server.js', '--config={"a":1,"b":2}', ' padded ', ''],
        env: { MCP_DEBUG: '1' },
      },
    ],
  },
});

describe('conversation settings round trip', () => {
  const makeRemoteConfig = (): ChatConfig => {
    const config = makeConfig();
    const remote = {
      ...config.mcp.servers[0],
      url: 'https://mcp.example.com/events',
      headers: { Authorization: 'Bearer test-token' },
      transport: 'sse',
      extra_option: { timeout: 30 },
    };
    config.mcp.servers = [remote];
    return config;
  };

  it('preserves all MCP server fields through validation on an unrelated save', () => {
    const original = makeRemoteConfig();
    const values = chatConfigToFormValues(original);
    values.chat.name = 'renamed';

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.chat.name).toBe('renamed');
    expect(config.mcp.servers).toEqual(original.mcp.servers);
  });

  it('round-trips a remote-only server without requiring a stdio command', () => {
    const original = makeRemoteConfig();
    original.mcp.servers[0].command = '';
    const values = chatConfigToFormValues(original);

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.mcp.servers).toEqual(original.mcp.servers);
  });

  it('persists edited remote fields instead of their original snapshot', () => {
    const original = makeRemoteConfig();
    const values = chatConfigToFormValues(original);
    values.mcp.servers![0].url = 'https://updated.example.com/mcp';
    values.mcp.servers![0].headers = {};

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.mcp.servers[0]).toEqual({
      ...original.mcp.servers[0],
      url: 'https://updated.example.com/mcp',
      headers: {},
    });
  });

  it('does not invent blank remote fields when loading a stdio server', () => {
    const original = makeConfig();
    const values = chatConfigToFormValues(original);
    const server = values.mcp.servers![0];

    expect(server).not.toHaveProperty('url');
    expect(server).not.toHaveProperty('headers');
    expect(formValuesToChatConfig(formSchema.parse(values), original).mcp.servers).toEqual(
      original.mcp.servers
    );
  });

  it('retains the original remote fields when optional row fields are absent', () => {
    const original = makeRemoteConfig();
    const values = chatConfigToFormValues(original);
    delete values.mcp.servers![0].url;
    delete values.mcp.servers![0].headers;

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.mcp.servers).toEqual(original.mcp.servers);
  });

  it('persists explicitly cleared URL and headers instead of restoring the snapshot', () => {
    const original = makeRemoteConfig();
    const values = chatConfigToFormValues(original);
    values.mcp.servers![0].url = '';
    values.mcp.servers![0].headers = {};

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.mcp.servers[0]).toEqual({
      ...original.mcp.servers[0],
      url: '',
      headers: {},
    });
  });

  it.each([
    '',
    'not-a-url',
    'file:///tmp/server',
    'HTTPS://mcp.example.com/events',
    ' https://mcp.example.com/events',
  ])('still requires a command for a server without an HTTP URL (%s)', (url) => {
    const values = chatConfigToFormValues(makeConfig());
    values.mcp.servers![0].command = '';
    values.mcp.servers![0].url = url;

    expect(formSchema.safeParse(values).success).toBe(false);
  });

  it('keeps remote metadata with its row through rename, reorder, and removal', () => {
    const original = makeRemoteConfig();
    original.mcp.servers.unshift(makeConfig().mcp.servers[0]);
    original.mcp.servers.push({
      ...makeConfig().mcp.servers[0],
      name: 'removed',
    });
    const values = chatConfigToFormValues(original);
    values.mcp.servers = [values.mcp.servers![1], values.mcp.servers![0]];
    values.mcp.servers[0].name = 'remote-renamed';

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.mcp.servers).toEqual([
      { ...original.mcp.servers[1], name: 'remote-renamed' },
      original.mcp.servers[0],
    ]);
  });

  it('applies edited stdio fields while retaining remote metadata', () => {
    const original = makeRemoteConfig();
    const values = chatConfigToFormValues(original);
    values.mcp.servers![0].command = 'updated-command';
    values.mcp.servers![0].args = '--new, value';
    values.mcp.servers![0].env = [{ key: ' NEW ', value: 'kept' }];

    const config = formValuesToChatConfig(formSchema.parse(values), original);

    expect(config.mcp.servers[0]).toEqual({
      ...original.mcp.servers[0],
      command: 'updated-command',
      args: ['--new', 'value'],
      env: { NEW: 'kept' },
    });
  });

  it('preserves automatic (null) tool format in form values', () => {
    const values = chatConfigToFormValues(makeConfig());
    expect(values.chat.tool_format).toBeNull();
  });

  it('preserves an explicit tool format', () => {
    const values = chatConfigToFormValues(makeConfig({ tool_format: 'tool' as never }));
    expect(values.chat.tool_format).toBe('tool');
  });

  it('preserves exact MCP argv when only an unrelated field changes', () => {
    const original = makeConfig();
    const values = chatConfigToFormValues(original);
    values.chat.name = 'renamed';

    const config = formValuesToChatConfig(values, original);

    expect(config.chat.name).toBe('renamed');
    expect(config.mcp.servers[0].args).toEqual([
      'server.js',
      '--config={"a":1,"b":2}',
      ' padded ',
      '',
    ]);
  });

  it('preserves null tool format when only an unrelated field changes', () => {
    const original = makeConfig();
    const values = chatConfigToFormValues(original);
    values.chat.temperature = 0.9;

    const config = formValuesToChatConfig(values, original);

    expect(config.chat.tool_format).toBeNull();
    expect(config.chat.temperature).toBe(0.9);
  });

  it('keeps an explicit tool format on save', () => {
    const original = makeConfig({ tool_format: 'markdown' as never });
    const values = chatConfigToFormValues(original);

    const config = formValuesToChatConfig(values, original);

    expect(config.chat.tool_format).toBe('markdown');
  });

  it('parses a deliberately edited argv field', () => {
    const original = makeConfig();
    const values = chatConfigToFormValues(original);
    values.mcp.servers![0].args = 'server.js, --verbose';

    const config = formValuesToChatConfig(values, original);

    expect(config.mcp.servers[0].args).toEqual(['server.js', '--verbose']);
  });

  it('preserves argv when a server is renamed but its args are untouched', () => {
    const original = makeConfig();
    const values = chatConfigToFormValues(original);
    values.mcp.servers![0].name = 'filesystem-renamed';

    const config = formValuesToChatConfig(values, original);

    expect(config.mcp.servers[0].args).toEqual([
      'server.js',
      '--config={"a":1,"b":2}',
      ' padded ',
      '',
    ]);
  });

  it("preserves each server's own argv when one is removed", () => {
    const original = makeConfig();
    original.mcp.servers.unshift({
      name: 'first',
      enabled: false,
      command: 'first-cmd',
      args: ['x', 'y'],
      env: {},
    });
    const values = chatConfigToFormValues(original);
    values.mcp.servers = values.mcp.servers!.slice(1);

    const config = formValuesToChatConfig(values, original);

    expect(config.mcp.servers).toHaveLength(1);
    expect(config.mcp.servers[0].args).toEqual([
      'server.js',
      '--config={"a":1,"b":2}',
      ' padded ',
      '',
    ]);
  });

  it('keeps the original argv when the list is reordered', () => {
    const original = makeConfig();
    original.mcp.servers.push({
      name: 'other',
      enabled: false,
      command: 'other-cmd',
      args: ['a', 'b'],
      env: {},
    });
    const values = chatConfigToFormValues(original);
    values.mcp.servers = [values.mcp.servers![1], values.mcp.servers![0]];

    const config = formValuesToChatConfig(values, original);

    expect(config.mcp.servers[0].args).toEqual(['a', 'b']);
    expect(config.mcp.servers[1].args).toEqual([
      'server.js',
      '--config={"a":1,"b":2}',
      ' padded ',
      '',
    ]);
  });
});
