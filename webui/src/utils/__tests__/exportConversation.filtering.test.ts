import type { Message } from '@/types/conversation';
import {
  copyConversationToClipboard,
  exportConversationAsMarkdown,
  formatConversationAsMarkdown,
  type ExportMarkdownOptions,
} from '../exportConversation';

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: jest.fn(() => false),
}));

const title = 'Filtering regression';
const modes = [
  { label: 'full', includeThinking: true, includeTools: true },
  { label: 'hide thinking only', includeThinking: false, includeTools: true },
  { label: 'hide tools only', includeThinking: true, includeTools: false },
  { label: 'hide both', includeThinking: false, includeTools: false },
];

// These examples are literal because of their syntax, not because surrounding
// prose calls them examples. No parser/helper internals are mocked or imported.
const literals = [
  {
    label: 'inline thinking and native JSON',
    content: 'Use `<thinking>literal reasoning</thinking>` and `@shell: {"command":"literal"}`.',
  },
  {
    label: 'multi-backtick inline code',
    content: 'Use ``<think redacted>literal</think redacted> and `@save: {"path":"x"}` `` here.',
  },
  {
    label: 'XML example fence',
    content:
      '```xml\n<thinking>literal XML</thinking>\n<think redacted>opaque example</think redacted>\n```',
  },
  {
    label: 'text example fence with native JSON',
    content:
      '```text\n@memory(call_example): {\n  "content": "literal native call"\n}\n<think>literal text</think>\n```',
  },
  {
    label: 'nested gptme markdown example fences',
    content: [
      '```markdown',
      '```shell',
      'echo literal shell',
      '```',
      '@watch: {"path":"literal after inner fence"}',
      '<thinking>literal after inner fence</thinking>',
      '```',
    ].join('\n'),
  },
  {
    label: 'longer enclosing backtick example fence',
    content: [
      '````markdown',
      '```save example.md',
      '<thinking>literal saved example</thinking>',
      '```',
      '@shell: {"command":"literal after shorter fence"}',
      '````',
    ].join('\n'),
  },
  {
    label: 'tilde example fence',
    content: [
      '~~~~text',
      '```memory',
      'literal memory example',
      '```',
      '@request_tool_change: {"tools":["literal"]}',
      '<think>literal tilde example</think>',
      '~~~~',
    ].join('\n'),
  },
  {
    label: 'interrupted thinking tag inside an example fence',
    content: '```xml\n<thinking>literal unfinished tag\n```\nVisible after example.',
  },
  {
    label: 'interrupted native JSON inside an example fence',
    content:
      '```text\n@shell(call_example): {"command":"literal unfinished\n```\nVisible after example.',
  },
  {
    label: 'ordinary non-JSON mentions and non-tool code',
    content:
      'Contact @alice: not a call.\n@shell: not JSON\n```python\nprint("ordinary code")\n```',
  },
];

const invocations = [
  { label: 'memory fence', content: '```memory\nprivate memory payload\n```', kind: 'tool' },
  { label: 'watch fence', content: '```watch\nprivate watch payload\n```', kind: 'tool' },
  {
    label: 'request_tool_change fence',
    content: '```request_tool_change\nprivate tool change payload\n```',
    kind: 'tool',
  },
  {
    label: 'MCP server tool fence',
    content: '```github.create_issue\n{"title":"private MCP payload"}\n```',
    kind: 'tool',
  },
  {
    label: 'nested markdown inside a real save invocation',
    content:
      '```save note.md\n# private saved payload\n```python\nprint("private inner payload")\n```\n```',
    kind: 'tool',
  },
  {
    label: 'interrupted save invocation',
    content: '```save note.md\nprivate interrupted save payload',
    kind: 'tool',
    interrupted: true,
  },
  {
    label: 'native JSON with escaped quotes, backslashes and braces',
    content: `@shell(call_1): ${JSON.stringify({
      command: 'echo "} {"; echo \\; private native payload',
      options: { cwd: 'private nested payload' },
    })}`,
    kind: 'tool',
  },
  {
    label: 'multiline native JSON',
    content:
      '@github.create_issue(call_2): {\n  "title": "private multiline payload",\n  "labels": ["private label"]\n}',
    kind: 'tool',
  },
  {
    label: 'interrupted native JSON',
    content:
      '@save(call_3): {\n  "path": "private.txt",\n  "content": "private interrupted native payload',
    kind: 'tool',
    interrupted: true,
  },
  {
    label: 'thinking block',
    content: '<thinking>private reasoning payload</thinking>',
    kind: 'thinking',
  },
  { label: 'think block', content: '<think>private think payload</think>', kind: 'thinking' },
  {
    label: 'redacted thinking block',
    content: '<think redacted>private opaque payload</think redacted>',
    kind: 'thinking',
  },
  {
    label: 'interrupted thinking block',
    content: '<thinking>private interrupted reasoning payload',
    kind: 'thinking',
    interrupted: true,
  },
  {
    label: 'interrupted redacted thinking block',
    content: '<think redacted>private interrupted opaque payload',
    kind: 'thinking',
    interrupted: true,
  },
];

async function readBlobAsText(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read export blob'));
    reader.readAsText(blob);
  });
}

// Exercise the real wrappers, faking only browser side effects. Comparing each
// consumer against fixture-owned expectations (not against another consumer)
// prevents the formatter and wrappers from agreeing on the same wrong output.
const consumers = ['formatter', 'clipboard', 'download'] as const;
type Consumer = (typeof consumers)[number];

async function consume(
  consumer: Consumer,
  messages: Message[],
  options?: ExportMarkdownOptions
): Promise<string> {
  if (consumer === 'formatter') {
    return formatConversationAsMarkdown(title, messages, options);
  }
  if (consumer === 'clipboard') {
    await copyConversationToClipboard(title, messages, options);
    expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1);
    return (navigator.clipboard.writeText as jest.Mock).mock.calls[0][0] as string;
  }

  await exportConversationAsMarkdown('filtering-fixture', title, messages, options);
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
  expect(blob).toBeInstanceOf(Blob);
  expect(blob.type).toBe('text/markdown');
  return await readBlobAsText(blob);
}

describe.each(consumers)('%s filtered conversation consumer', (consumer) => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: jest.fn().mockResolvedValue(undefined) },
    });
    // jsdom has no object URL implementation. Restore descriptors after each
    // test instead of leaking these fakes to other export suites.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: jest.fn(() => 'blob:filtering-fixture'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: jest.fn(),
    });
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  const originalCreateURL = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const originalRevokeURL = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');

  afterEach(() => {
    jest.restoreAllMocks();
    for (const [object, key, descriptor] of [
      [navigator, 'clipboard', originalClipboard],
      [URL, 'createObjectURL', originalCreateURL],
      [URL, 'revokeObjectURL', originalRevokeURL],
    ] as const) {
      if (descriptor) Object.defineProperty(object, key, descriptor);
      else Reflect.deleteProperty(object, key);
    }
  });

  describe.each(modes)('$label', ({ includeThinking, includeTools }) => {
    const options = { includeThinking, includeTools };

    it.each(literals)('preserves literal bytes: $label', async ({ content }) => {
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toBe(`# ${title}\n\n## Assistant\n\n${content}\n`);
    });

    it.each(invocations)(
      'classifies actual invocation: $label',
      async ({ content, kind, interrupted }) => {
        const prefix = 'Public before.';
        const suffix = interrupted ? '' : '\n\nPublic after.';
        const result = await consume(
          consumer,
          [{ role: 'assistant', content: `${prefix}\n\n${content}${suffix}` }],
          options
        );
        const included = kind === 'tool' ? includeTools : includeThinking;
        expect(result).toContain(prefix);
        if (!interrupted) expect(result).toContain('Public after.');
        if (included) {
          expect(result).toContain(content);
        } else {
          expect(result).not.toContain('private');
          expect(result).not.toContain(content);
        }
      }
    );

    it('treats top-level tool syntax as invocations even when prose calls it an example', async () => {
      const result = await consume(
        consumer,
        [
          {
            role: 'assistant',
            content:
              'Here is an example:\n```shell\necho private fence example\n```\nAnother example:\n@custom_plugin: {"value":"private native example"}\nPublic ending.',
          },
        ],
        options
      );
      expect(result).toContain('Here is an example:');
      expect(result).toContain('Public ending.');
      if (includeTools) {
        expect(result).toContain('private fence example');
        expect(result).toContain('private native example');
      } else {
        expect(result).not.toContain('private');
      }
    });

    it('separates examples, real thinking and real tools in the same assistant message', async () => {
      const example =
        '```text\n<think>literal reasoning</think>\n@memory: {"content":"literal call"}\n```';
      const result = await consume(
        consumer,
        [
          {
            role: 'assistant',
            content: `Public start.\n<thinking>private reasoning</thinking>\n${example}\n\n\`\`\`shell\necho private command\n\`\`\`\nPublic end.`,
          },
        ],
        options
      );
      expect(result).toContain(example);
      expect(result).toContain('Public start.');
      expect(result).toContain('Public end.');
      expect(result.includes('private reasoning')).toBe(includeThinking);
      expect(result.includes('private command')).toBe(includeTools);
    });

    it('keeps retained native-call and thinking payloads opaque', async () => {
      const native =
        '@save: {\n"content":"private native\n```text\n<think>native literal</think>"\n}';
      const thinking =
        '<think>private reasoning\n```text\n@save: {"content":"reasoning literal"}\n</think>';
      const content = `${native}\n${thinking}\n<think>independent private reasoning</think>\n@save: {"content":"independent private tool"}\nPublic answer.`;
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toContain('Public answer.');
      expect(result.includes(native)).toBe(includeTools);
      expect(result.includes(thinking)).toBe(includeThinking);
      expect(result.includes('independent private reasoning')).toBe(includeThinking);
      expect(result.includes('independent private tool')).toBe(includeTools);
    });

    it('does not manufacture tool syntax by deleting thinking', async () => {
      const content =
        '@save<think>private</think>: {"content":"literal split prefix"}\nPublic answer.';
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toContain('literal split prefix');
      expect(result).toContain('Public answer.');
      expect(result.includes('<think>private</think>')).toBe(includeThinking);
    });

    it('does not manufacture thinking syntax by deleting a tool block', async () => {
      const content =
        '<thi\n```shell\necho private\n```\nnk>literal split tag</think>\nPublic answer.';
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toContain('literal split tag');
      expect(result).toContain('Public answer.');
      expect(result.includes('echo private')).toBe(includeTools);
    });

    it('preserves multiline inline-code bytes including invalid fence openers', async () => {
      // Backticks in the info string make this an inline literal, not a
      // real fence interrupting the paragraph before the two-backtick closer.
      const content =
        'Use ``literal\n```text```\n<think>example</think>\n`` then <think>private reasoning</think>Public answer.';
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toContain('``literal\n```text```\n<think>example</think>\n``');
      expect(result).toContain('Public answer.');
      expect(result.includes('private reasoning')).toBe(includeThinking);
    });

    it('does not let escaped backticks hide actual thinking', async () => {
      const content = '\\`<think>private reasoning</think>` Public answer.';
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toContain('Public answer.');
      expect(result.includes('private reasoning')).toBe(includeThinking);
    });

    it('strips a tool payload with longer tagged nested fences through the real closer', async () => {
      const content =
        '```save x.md\n````markdown\nprivate inner\n````\nprivate tail\n```\nPublic answer.';
      const result = await consume(consumer, [{ role: 'assistant', content }], options);
      expect(result).toContain('Public answer.');
      expect(result.includes('private inner')).toBe(includeTools);
      expect(result.includes('private tail')).toBe(includeTools);
    });

    it('preserves user/system content and independently switches tool-role output', async () => {
      const ordinary =
        'Ordinary <thinking>user text</thinking>\n@shell: {"command":"user text"}\n```memory\nuser text\n```';
      const result = await consume(
        consumer,
        [
          { role: 'system', content: ordinary },
          { role: 'user', content: ordinary },
          { role: 'assistant', content: 'Public answer.' },
          { role: 'tool', content: 'Tool-role output.' },
        ],
        { ...options, includeSystem: true }
      );
      expect(result).toContain(`## System\n\n${ordinary}\n`);
      expect(result).toContain(`## User\n\n${ordinary}\n`);
      expect(result).toContain('Public answer.');
      expect(result.includes('Tool-role output.')).toBe(includeTools);
    });
  });

  it.each([undefined, { includeThinking: true, includeTools: true }])(
    'keeps default/explicit full-mode document bytes (%j)',
    async (options) => {
      const content = `  Leading whitespace\n${literals.map((item) => item.content).join('\n\n')}\n${invocations.map((item) => item.content).join('\n\n')}\nTrailing whitespace  `;
      const messages: Message[] = [
        { role: 'user', content: 'Question', timestamp: '2026-01-01T00:00:00Z' },
        { role: 'assistant', content },
        { role: 'tool', content: 'Raw tool result' },
      ];
      const result = await consume(consumer, messages, options);
      expect(result).toBe(
        `# ${title}\n\n## User  \n*2026-01-01T00:00:00Z*\n\nQuestion\n\n## Assistant\n\n${content}\n\n## Tool\n\nRaw tool result\n`
      );
    }
  );
});
