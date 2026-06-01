import {
  formatConversationAsMarkdown,
  downloadAsFile,
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
  parseConversationImportJSON,
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
