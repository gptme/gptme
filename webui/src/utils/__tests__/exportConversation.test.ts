import {
  formatConversationAsMarkdown,
  downloadAsFile,
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
  parseConversationImportJSON,
  copyConversationToClipboard,
} from '../exportConversation';
import type { Message } from '@/types/conversation';

const sampleMessages: Message[] = [
  { role: 'system', content: 'You are a helpful assistant.', timestamp: '2026-03-28T10:00:00Z' },
  { role: 'user', content: 'Hello, how are you?', timestamp: '2026-03-28T10:01:00Z' },
  {
    role: 'assistant',
    content: 'I am doing well! How can I help you today?',
    timestamp: '2026-03-28T10:01:05Z',
  },
  { role: 'user', content: 'Tell me a joke.', timestamp: '2026-03-28T10:02:00Z' },
  {
    role: 'assistant',
    content: "Why did the programmer quit? Because they didn't get arrays.",
    timestamp: '2026-03-28T10:02:10Z',
  },
];

async function readBlobAsText(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read blob'));
    reader.readAsText(blob);
  });
}

describe('getExportableMessages', () => {
  it('excludes system and hidden messages by default', () => {
    const result = getExportableMessages(sampleMessages);
    expect(result).toHaveLength(4);
    expect(result.every((msg) => msg.role !== 'system')).toBe(true);
    expect(result.every((msg) => !msg.hide)).toBe(true);
  });

  it('includes system messages when requested', () => {
    const result = getExportableMessages(sampleMessages, { includeSystem: true });
    expect(result).toHaveLength(5);
    expect(result.some((msg) => msg.role === 'system')).toBe(true);
  });

  it('returns an empty array when all messages are hidden or system-only', () => {
    const result = getExportableMessages([
      { role: 'system', content: 'system only' },
      { role: 'assistant', content: 'hidden assistant', hide: true },
    ]);
    expect(result).toEqual([]);
  });

  it('excludes tool messages when includeTools is false', () => {
    const messages: Message[] = [
      { role: 'user', content: 'run ls' },
      { role: 'assistant', content: 'sure' },
      { role: 'tool', content: '$ ls\nfile1.txt\nfile2.txt' },
    ];
    const result = getExportableMessages(messages, { includeTools: false });
    expect(result).toHaveLength(2);
    expect(result.every((msg) => msg.role !== 'tool')).toBe(true);
  });

  it('includes tool messages by default', () => {
    const messages: Message[] = [
      { role: 'user', content: 'run ls' },
      { role: 'tool', content: '$ ls\nfile1.txt' },
    ];
    const result = getExportableMessages(messages);
    expect(result.some((msg) => msg.role === 'tool')).toBe(true);
  });

  it('excludes tool_result messages when includeTools is false', () => {
    const messages: Message[] = [
      { role: 'user', content: 'run ls' },
      { role: 'assistant', content: 'sure' },
      { role: 'tool_result', content: '$ ls\nfile1.txt\nfile2.txt' },
    ];
    const result = getExportableMessages(messages, { includeTools: false });
    expect(result).toHaveLength(2);
    expect(result.every((msg) => msg.role !== 'tool_result')).toBe(true);
  });

  it('includes tool_result messages by default', () => {
    const messages: Message[] = [
      { role: 'user', content: 'run ls' },
      { role: 'tool_result', content: '$ ls\nfile1.txt' },
    ];
    const result = getExportableMessages(messages);
    expect(result.some((msg) => msg.role === 'tool_result')).toBe(true);
  });
});

describe('formatConversationAsMarkdown', () => {
  it('formats messages as markdown excluding system by default', () => {
    const result = formatConversationAsMarkdown('Test Chat', sampleMessages);
    expect(result).toContain('# Test Chat');
    expect(result).not.toContain('You are a helpful assistant.');
    expect(result).toContain('## User');
    expect(result).toContain('Hello, how are you?');
    expect(result).toContain('## Assistant');
    expect(result).toContain('I am doing well!');
  });

  it('includes system messages when requested', () => {
    const result = formatConversationAsMarkdown('Test Chat', sampleMessages, {
      includeSystem: true,
    });
    expect(result).toContain('## System');
    expect(result).toContain('You are a helpful assistant.');
  });

  it('includes timestamps by default', () => {
    const result = formatConversationAsMarkdown('Test Chat', sampleMessages);
    expect(result).toContain('2026-03-28T10:01:00Z');
  });

  it('excludes timestamps when requested', () => {
    const result = formatConversationAsMarkdown('Test Chat', sampleMessages, {
      includeTimestamps: false,
    });
    expect(result).not.toContain('2026-03-28T10:01:00Z');
  });

  it('skips hidden messages', () => {
    const messages: Message[] = [
      { role: 'user', content: 'visible message' },
      { role: 'assistant', content: 'hidden message', hide: true },
      { role: 'assistant', content: 'another visible' },
    ];
    const result = formatConversationAsMarkdown('Chat', messages);
    expect(result).toContain('visible message');
    expect(result).not.toContain('hidden message');
    expect(result).toContain('another visible');
  });

  it('handles empty messages array', () => {
    const result = formatConversationAsMarkdown('Empty Chat', []);
    expect(result).toContain('# Empty Chat');
    expect(result.trim()).toBe('# Empty Chat');
  });

  it('handles messages without timestamps', () => {
    const messages: Message[] = [{ role: 'user', content: 'no timestamp message' }];
    const result = formatConversationAsMarkdown('Chat', messages);
    expect(result).toContain('## User');
    expect(result).toContain('no timestamp message');
    expect(result).not.toContain('*undefined*');
  });

  it('capitalizes role names', () => {
    const messages: Message[] = [
      { role: 'user', content: 'user msg' },
      { role: 'assistant', content: 'assistant msg' },
      { role: 'tool', content: 'tool msg' },
    ];
    const result = formatConversationAsMarkdown('Chat', messages);
    expect(result).toContain('## User');
    expect(result).toContain('## Assistant');
    expect(result).toContain('## Tool');
  });

  describe('thinking blocks (includeThinking option)', () => {
    const thinkingMessages: Message[] = [
      {
        role: 'assistant',
        content:
          '<thinking>\nI need to think about this carefully.\n</thinking>\n\nThe answer is 42.',
      },
      {
        role: 'assistant',
        content: '<think>quick note</think>Here is the result.',
      },
    ];

    it('strips <thinking> blocks by default (includeThinking defaults to false)', () => {
      const result = formatConversationAsMarkdown('Chat', thinkingMessages);
      expect(result).not.toContain('<thinking>');
      expect(result).not.toContain('I need to think about this carefully.');
      expect(result).toContain('The answer is 42.');
    });

    it('strips <think> short-form blocks by default', () => {
      const result = formatConversationAsMarkdown('Chat', thinkingMessages);
      expect(result).not.toContain('<think>');
      expect(result).not.toContain('quick note');
      expect(result).toContain('Here is the result.');
    });

    it('includes thinking blocks when includeThinking is true', () => {
      const result = formatConversationAsMarkdown('Chat', thinkingMessages, {
        includeThinking: true,
      });
      expect(result).toContain('<thinking>');
      expect(result).toContain('I need to think about this carefully.');
      expect(result).toContain('The answer is 42.');
    });

    it('does not strip thinking from non-assistant messages', () => {
      const messages: Message[] = [
        { role: 'user', content: 'Use <thinking> tags to reason step-by-step.' },
      ];
      const result = formatConversationAsMarkdown('Chat', messages);
      // User messages should never have thinking stripped
      expect(result).toContain('<thinking>');
    });
  });

  describe('tool messages (includeTools option)', () => {
    const messagesWithTools: Message[] = [
      { role: 'user', content: 'list files' },
      { role: 'assistant', content: 'sure' },
      { role: 'tool', content: '```shell\nls\n```\n\n```stdout\nfile.txt\n```' },
    ];

    it('includes tool messages by default', () => {
      const result = formatConversationAsMarkdown('Chat', messagesWithTools);
      expect(result).toContain('## Tool');
      expect(result).toContain('file.txt');
    });

    it('excludes tool messages when includeTools is false', () => {
      const result = formatConversationAsMarkdown('Chat', messagesWithTools, {
        includeTools: false,
      });
      expect(result).not.toContain('## Tool');
      expect(result).not.toContain('file.txt');
      expect(result).toContain('## Assistant');
    });
  });
});

describe('copyConversationToClipboard', () => {
  it('writes formatted markdown to the clipboard', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    await copyConversationToClipboard('Test Chat', sampleMessages);

    expect(writeText).toHaveBeenCalledTimes(1);
    const written = writeText.mock.calls[0][0] as string;
    expect(written).toContain('# Test Chat');
    expect(written).toContain('Hello, how are you?');
  });

  it('strips thinking blocks by default', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const messages: Message[] = [
      {
        role: 'assistant',
        content: '<thinking>internal reasoning</thinking>Public answer.',
      },
    ];

    await copyConversationToClipboard('Chat', messages);

    const written = writeText.mock.calls[0][0] as string;
    expect(written).not.toContain('internal reasoning');
    expect(written).toContain('Public answer.');
  });

  it('includes thinking blocks when requested', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const messages: Message[] = [
      {
        role: 'assistant',
        content: '<thinking>internal reasoning</thinking>Public answer.',
      },
    ];

    await copyConversationToClipboard('Chat', messages, { includeThinking: true });

    const written = writeText.mock.calls[0][0] as string;
    expect(written).toContain('internal reasoning');
  });

  it('excludes tool messages when includeTools is false', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const messages: Message[] = [
      { role: 'user', content: 'run it' },
      { role: 'tool', content: 'tool output here' },
    ];

    await copyConversationToClipboard('Chat', messages, { includeTools: false });

    const written = writeText.mock.calls[0][0] as string;
    expect(written).not.toContain('tool output here');
  });
});

describe('downloadAsFile', () => {
  it('creates a blob URL, triggers click, and revokes URL', () => {
    const mockUrl = 'blob:test-url';
    const createObjectURL = jest.fn().mockReturnValue(mockUrl);
    const revokeObjectURL = jest.fn();
    global.URL.createObjectURL = createObjectURL;
    global.URL.revokeObjectURL = revokeObjectURL;

    const clickSpy = jest.fn();
    const createElement = jest.spyOn(document, 'createElement');
    jest.spyOn(document.body, 'appendChild').mockImplementation((node) => {
      if (node instanceof HTMLAnchorElement) {
        node.click = clickSpy;
      }
      return node;
    });
    jest.spyOn(document.body, 'removeChild').mockImplementation((node) => node);

    downloadAsFile('test content', 'test.md');

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith(mockUrl);

    // Check the anchor element was configured correctly
    const anchor = createElement.mock.results[0]?.value as HTMLAnchorElement;
    expect(anchor.download).toBe('test.md');
    expect(anchor.href).toBe(mockUrl);

    createElement.mockRestore();
  });
});

describe('exportConversationAsMarkdown', () => {
  beforeEach(() => {
    global.URL.createObjectURL = jest.fn().mockReturnValue('blob:test');
    global.URL.revokeObjectURL = jest.fn();
    jest.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    jest.spyOn(document.body, 'removeChild').mockImplementation((node) => node);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('generates a safe filename from conversation name', () => {
    const createElementSpy = jest.spyOn(document, 'createElement');
    exportConversationAsMarkdown('test-id', 'My Chat / with special: chars!', sampleMessages);

    const anchor = createElementSpy.mock.results[0]?.value as HTMLAnchorElement;
    expect(anchor.download).toBe('My-Chat-with-special-chars-.md');
    createElementSpy.mockRestore();
  });

  it('uses conversationId when name is empty', () => {
    const createElementSpy = jest.spyOn(document, 'createElement');
    exportConversationAsMarkdown('conv-123', '', sampleMessages);

    const anchor = createElementSpy.mock.results[0]?.value as HTMLAnchorElement;
    expect(anchor.download).toBe('conv-123.md');
    createElementSpy.mockRestore();
  });
});

describe('exportConversationAsJSON', () => {
  beforeEach(() => {
    global.URL.createObjectURL = jest.fn().mockReturnValue('blob:test');
    global.URL.revokeObjectURL = jest.fn();
    jest.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    jest.spyOn(document.body, 'removeChild').mockImplementation((node) => node);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('creates JSON blob with conversation metadata', () => {
    let capturedBlob: Blob | undefined;
    (global.URL.createObjectURL as jest.Mock).mockImplementation((blob: Blob) => {
      capturedBlob = blob;
      return 'blob:test';
    });

    exportConversationAsJSON('conv-123', 'Test Chat', sampleMessages);

    expect(capturedBlob).toBeDefined();
    expect(capturedBlob!.type).toBe('application/json');
    expect(capturedBlob!.size).toBeGreaterThan(0);
  });

  it('filters tool messages so exported JSON can be re-imported', async () => {
    let capturedBlob: Blob | undefined;
    (global.URL.createObjectURL as jest.Mock).mockImplementation((blob: Blob) => {
      capturedBlob = blob;
      return 'blob:test';
    });

    exportConversationAsJSON('conv-123', 'Test Chat', [
      ...sampleMessages,
      { role: 'tool', content: 'Tool output' },
    ]);

    const exportedText = await readBlobAsText(capturedBlob!);
    const exported = JSON.parse(exportedText) as {
      messages: Array<{ role: string; content: string }>;
    };
    expect(exported.messages.some((message) => message.role === 'tool')).toBe(false);

    const imported = parseConversationImportJSON(exportedText);
    expect(imported.messages).toEqual(sampleMessages);
  });

  it('generates a .json filename', () => {
    const createElementSpy = jest.spyOn(document, 'createElement');
    exportConversationAsJSON('conv-123', 'Test Chat', sampleMessages);

    const anchor = createElementSpy.mock.results[0]?.value as HTMLAnchorElement;
    expect(anchor.download).toBe('Test-Chat.json');
    createElementSpy.mockRestore();
  });
});

describe('parseConversationImportJSON', () => {
  it('parses a valid exported conversation', () => {
    const result = parseConversationImportJSON(
      JSON.stringify({
        id: 'conv-123',
        name: 'Imported Chat',
        exported_at: '2026-03-28T10:03:00Z',
        messages: sampleMessages,
      })
    );

    expect(result.name).toBe('Imported Chat');
    expect(result.messages).toEqual(sampleMessages);
  });

  it('throws for invalid JSON', () => {
    expect(() => parseConversationImportJSON('{not json')).toThrow('Invalid JSON file');
  });

  it('throws when messages is missing', () => {
    expect(() => parseConversationImportJSON(JSON.stringify({ name: 'Missing messages' }))).toThrow(
      'Conversation import must include a messages array'
    );
  });

  it('throws when a message is missing content', () => {
    expect(() =>
      parseConversationImportJSON(
        JSON.stringify({
          name: 'Broken import',
          messages: [{ role: 'user' }],
        })
      )
    ).toThrow('Imported message 1 is missing a string content field');
  });

  it('skips tool messages from older exports', () => {
    const result = parseConversationImportJSON(
      JSON.stringify({
        name: 'Imported Chat',
        messages: [
          { role: 'user', content: 'Hello' },
          { role: 'tool', content: 'Tool output' },
          { role: 'assistant', content: 'Hi' },
        ],
      })
    );

    expect(result.messages).toEqual([
      { role: 'user', content: 'Hello' },
      { role: 'assistant', content: 'Hi' },
    ]);
  });

  it('throws when a message uses an unknown role', () => {
    expect(() =>
      parseConversationImportJSON(
        JSON.stringify({
          name: 'Broken import',
          messages: [{ role: 'critic', content: 'Nope' }],
        })
      )
    ).toThrow(
      'Imported message 1 has unsupported role "critic". Only system, user, and assistant messages can be restored.'
    );
  });

  it('preserves an explicitly empty name', () => {
    const result = parseConversationImportJSON(
      JSON.stringify({
        id: 'conv-123',
        name: '',
        messages: [{ role: 'user', content: 'Hello' }],
      })
    );

    expect(result.name).toBe('');
  });
});
