import { chatConfigToFormValues, formValuesToChatConfig } from '../useConversationSettings';
import type { ChatConfig } from '@/types/api';

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
