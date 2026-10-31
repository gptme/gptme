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

const title = 'Line-start inline literals regression';
const consumers = ['formatter', 'clipboard', 'download'] as const;
type Consumer = (typeof consumers)[number];
const modes = [
  { includeThinking: true, includeTools: true },
  { includeThinking: false, includeTools: true },
  { includeThinking: true, includeTools: false },
  { includeThinking: false, includeTools: false },
];

// A backtick in a backtick fence's info string makes it an invalid opener.
// These are complete inline code spans, not unfinished fences extending to EOF.
const inlineLiterals = [
  { label: 'three backticks at message start', literal: '```literal```', newline: '\n' },
  { label: 'four backticks', literal: '````literal````', newline: '\n' },
  { label: 'six backticks', literal: '``````literal``````', newline: '\n' },
  { label: 'space indentation', literal: '  ```literal```', newline: '\n' },
  { label: 'tab indentation', literal: '\t````literal````', newline: '\n' },
  { label: 'CRLF and indentation', literal: '   `````literal`````', newline: '\r\n' },
  {
    label: 'literal filtering syntax inside the span',
    literal: '```literal <think>literal reasoning</think> @shell: {"command":"literal"}```',
    newline: '\n',
  },
  {
    label: 'line start after ordinary prose',
    literal: 'Public preface.\r\n````literal````',
    newline: '\r\n',
  },
];

const thinking = '<think>private reasoning</think>';
const toolCall = '@shell: {"command":"echo private tool payload"}';

// Keep the public suffix on the thinking line so expectations don't depend on
// whether deleting a reasoning block also removes its following empty line.
function withFollowingPayloads(
  literal: string,
  newline: string,
  includeThinking: boolean,
  includeTools: boolean
): string {
  return (
    literal +
    newline +
    (includeThinking ? thinking : '') +
    'Public bridge.' +
    newline +
    (includeTools ? toolCall + newline : '') +
    'Public answer.'
  );
}

async function readBlobAsText(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read export blob'));
    reader.readAsText(blob);
  });
}

// Only browser side effects are faked. In particular, don't mock the formatter,
// scanner, or Blob: clipboard text and actual download bytes must be correct.
async function consume(
  consumer: Consumer,
  content: string,
  options: ExportMarkdownOptions
): Promise<string> {
  const messages: Message[] = [{ role: 'assistant', content }];
  if (consumer === 'formatter') {
    return formatConversationAsMarkdown(title, messages, options);
  }
  if (consumer === 'clipboard') {
    await copyConversationToClipboard(title, messages, options);
    expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1);
    return (navigator.clipboard.writeText as jest.Mock).mock.calls[0][0] as string;
  }

  await exportConversationAsMarkdown('review-fixture', title, messages, options);
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
  expect(blob).toBeInstanceOf(Blob);
  expect(blob.type).toBe('text/markdown');
  return await readBlobAsText(blob);
}

function documentFor(content: string): string {
  return `# ${title}\n\n## Assistant\n\n${content}\n`;
}

describe.each(consumers)('%s line-start inline code export (PR 4671 P1)', (consumer) => {
  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  const originalCreateURL = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const originalRevokeURL = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');

  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: jest.fn().mockResolvedValue(undefined) },
    });
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: jest.fn(() => 'blob:review-fixture'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: jest.fn(),
    });
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

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

  describe.each(modes)('includeThinking=$includeThinking includeTools=$includeTools', (options) => {
    it.each(inlineLiterals)(
      'preserves $label exactly while independently filtering subsequent payloads',
      async ({ literal, newline }) => {
        const source = withFollowingPayloads(literal, newline, true, true);
        const expected = withFollowingPayloads(
          literal,
          newline,
          options.includeThinking,
          options.includeTools
        );
        const result = await consume(consumer, source, options);
        expect(result).toContain(literal);
        expect(result).toContain('Public answer.');
        expect(result.includes('private reasoning')).toBe(options.includeThinking);
        expect(result.includes('private tool payload')).toBe(options.includeTools);
        expect(result).toBe(documentFor(expected));
      }
    );

    it.each([
      {
        label: 'backtick example fence containing a line-start inline span',
        literal:
          '```text\n```literal```\n<think>literal reasoning</think>\n@shell: {"command":"literal"}\n```',
      },
      {
        label: 'longer indented CRLF example fence',
        literal:
          '  `````text\r\n```literal```\r\n<think>literal reasoning</think>\r\n@shell: {"command":"literal"}\r\n  `````',
      },
      {
        label: 'tilde example fence',
        literal: '~~~text\n<think>literal reasoning</think>\n@shell: {"command":"literal"}\n~~~',
      },
    ])('keeps real $label opaque but filters after its closer', async ({ literal }) => {
      const source = withFollowingPayloads(literal, '\n', true, true);
      const expected = withFollowingPayloads(
        literal,
        '\n',
        options.includeThinking,
        options.includeTools
      );
      expect(await consume(consumer, source, options)).toBe(documentFor(expected));
    });

    it('still classifies an actual tool fence as a tool invocation', async () => {
      const fence = '```shell\necho private fenced tool payload\n```\n';
      const source = withFollowingPayloads(fence + 'Public middle.', '\n', true, true);
      const expected = withFollowingPayloads(
        (options.includeTools ? fence : '') + 'Public middle.',
        '\n',
        options.includeThinking,
        options.includeTools
      );
      expect(await consume(consumer, source, options)).toBe(documentFor(expected));
    });

    it.each([
      { label: 'example', tag: 'text', tool: false },
      { label: 'tool', tag: 'shell', tool: true },
    ])(
      'still treats an unfinished real $label fence as extending to EOF',
      async ({ tag, tool }) => {
        const prefix = 'Public answer.\n';
        const fence = `\`\`\`${tag}\n<think>literal reasoning</think>\n@shell: {"command":"literal"}`;
        const expected = prefix + (!tool || options.includeTools ? fence : '');
        expect(await consume(consumer, prefix + fence, options)).toBe(documentFor(expected));
      }
    );

    it.each([
      { label: 'LF blank lines', newline: '\n' },
      { label: 'CRLF blank lines', newline: '\r\n' },
    ])(
      'does not treat unmatched backticks across $label as a code span covering later payloads',
      async ({ newline }) => {
        const source =
          `An unmatched \` tick.${newline}${newline}` +
          `${thinking}${newline}${newline}` +
          `${toolCall}${newline}${newline}` +
          'Here is `code`.';
        const result = await consume(consumer, source, options);
        expect(result).toContain('An unmatched ` tick.');
        expect(result).toContain('Here is `code`.');
        expect(result.includes('private reasoning')).toBe(options.includeThinking);
        expect(result.includes('private tool payload')).toBe(options.includeTools);
      }
    );

    it.each([
      { label: 'backtick fence', block: '```shell\necho private fenced payload\n```' },
      { label: 'tilde fence', block: '~~~shell\necho private fenced payload\n~~~' },
      {
        label: 'indented transcript fence',
        block: '\t```shell\necho private fenced payload\n\t```',
      },
      { label: 'heading', block: '# Public heading\n<think>private reasoning</think>' },
      { label: 'blockquote', block: '> Public quote\n<think>private reasoning</think>' },
      { label: 'list', block: '- Public list\n<think>private reasoning</think>' },
    ])('does not pair backticks across an interrupting $label', async ({ block }) => {
      for (const newline of ['\n', '\r\n']) {
        const source = ['An unmatched ` tick.', block, 'Here is `code`.'].join('\n');
        const result = await consume(consumer, source.replace(/\n/g, newline), options);
        expect(result).toContain('An unmatched ` tick.');
        expect(result).toContain('Here is `code`.');
        if (block.includes('private fenced payload')) {
          expect(result.includes('private fenced payload')).toBe(options.includeTools);
        } else {
          expect(result.includes('private reasoning')).toBe(options.includeThinking);
        }
        // Each consumer's mocks are per invocation, rather than per newline.
        jest.clearAllMocks();
      }
    });

    it('does not interrupt a multiline span at a line-start inline literal', async () => {
      const literal = '`span\n```inline literal```\n<think>literal reasoning</think>\nspan`';
      const source = `${literal}\n${thinking}\n${toolCall}\nPublic answer.`;
      const result = await consume(consumer, source, options);
      expect(result).toContain(literal);
      expect(result.includes('private reasoning')).toBe(options.includeThinking);
      expect(result.includes('private tool payload')).toBe(options.includeTools);
    });

    it('still treats a CommonMark multiline code span without a blank line as opaque', async () => {
      const literal = '`span\n<think>literal reasoning</think>\nspan`';
      const source = `${literal}\n${thinking}\n${toolCall}\nPublic answer.`;
      const result = await consume(consumer, source, options);
      expect(result).toContain(literal);
      expect(result.includes('private reasoning')).toBe(options.includeThinking);
      expect(result.includes('private tool payload')).toBe(options.includeTools);
    });
  });
});

describe('inline paragraph boundary search work', () => {
  it('reuses each paragraph boundary across many short code spans', () => {
    const content = '`x` '.repeat(2000) + '\n\n' + '`y` '.repeat(2000);
    const originalSearch = String.prototype.search;
    let searchedCharacters = 0;
    const search = jest.spyOn(String.prototype, 'search').mockImplementation(function (
      this: string,
      pattern: Parameters<typeof originalSearch>[0]
    ) {
      searchedCharacters += this.length;
      return originalSearch.call(this, pattern);
    });
    try {
      const result = formatConversationAsMarkdown(title, [{ role: 'assistant', content }], {
        includeThinking: false,
        includeTools: false,
      });
      expect(result).toBe(documentFor(content));
      // Deterministic work bound, not a machine-dependent wall-clock limit.
      expect(searchedCharacters).toBeLessThanOrEqual(content.length * 3);
    } finally {
      search.mockRestore();
    }
  });
});
