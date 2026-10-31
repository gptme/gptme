import { toast } from 'sonner';
import { listen } from '@tauri-apps/api/event';
import { isTauriEnvironment } from '@/utils/tauri';
import {
  formatConversationAsMarkdown,
  downloadAsFile,
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
  parseConversationImportJSON,
  reportDownloadOutcome,
  stripThinkingBlocks,
  copyConversationToClipboard,
  type DownloadOutcome,
} from '../exportConversation';
import type { Message } from '@/types/conversation';

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: jest.fn(() => false),
}));

jest.mock('@tauri-apps/api/event', () => ({
  listen: jest.fn(),
}));

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

    const outcome = downloadAsFile('test content', 'test.md');

    expect(outcome).toEqual({ kind: 'browser' });
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

describe('downloadAsFile in Tauri', () => {
  async function flushAsyncWork() {
    // Let the dynamic import and the awaited `listen` registration settle.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  beforeEach(() => {
    (isTauriEnvironment as jest.Mock).mockReturnValue(true);
    global.URL.createObjectURL = jest.fn().mockReturnValue('blob:tauri-export');
    global.URL.revokeObjectURL = jest.fn();
    jest.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    jest.spyOn(document.body, 'removeChild').mockImplementation((node) => node);
  });

  afterEach(() => {
    (isTauriEnvironment as jest.Mock).mockReturnValue(false);
    (listen as jest.Mock).mockReset();
    jest.restoreAllMocks();
  });

  it('waits for the finished event before resolving the saved path', async () => {
    let handler: ((event: { payload: unknown }) => void) | undefined;
    const unlisten = jest.fn();
    (listen as jest.Mock).mockImplementation(
      async (_event: string, callback: (event: { payload: unknown }) => void) => {
        handler = callback;
        return unlisten;
      }
    );

    const outcomePromise = downloadAsFile('content', 'Export.md') as Promise<DownloadOutcome>;
    await flushAsyncWork();
    expect(handler).toBeDefined();

    // Nothing may be reported before the native download actually finishes.
    const settledEarly = await Promise.race([
      outcomePromise.then(() => 'resolved'),
      Promise.resolve('pending'),
    ]);
    expect(settledEarly).toBe('pending');

    handler!({
      payload: { url: 'blob:tauri-export', path: '/home/user/Downloads/Export.md', success: true },
    });

    await expect(outcomePromise).resolves.toEqual({
      kind: 'tauri',
      success: true,
      path: '/home/user/Downloads/Export.md',
    });
    expect(unlisten).toHaveBeenCalledTimes(1);
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:tauri-export');
  });

  it('ignores events for other downloads', async () => {
    let handler: ((event: { payload: unknown }) => void) | undefined;
    (listen as jest.Mock).mockImplementation(
      async (_event: string, callback: (event: { payload: unknown }) => void) => {
        handler = callback;
        return jest.fn();
      }
    );

    const outcomePromise = downloadAsFile('content', 'Export.md') as Promise<DownloadOutcome>;
    await flushAsyncWork();

    handler!({ payload: { url: 'blob:someone-else', path: '/tmp/other.md', success: true } });

    const settledEarly = await Promise.race([
      outcomePromise.then(() => 'resolved'),
      Promise.resolve('pending'),
    ]);
    expect(settledEarly).toBe('pending');

    handler!({ payload: { url: 'blob:tauri-export', path: null, success: true } });
    await expect(outcomePromise).resolves.toEqual({ kind: 'tauri', success: true, path: null });
  });

  it('distinguishes a failed download from a successful one', async () => {
    let handler: ((event: { payload: unknown }) => void) | undefined;
    (listen as jest.Mock).mockImplementation(
      async (_event: string, callback: (event: { payload: unknown }) => void) => {
        handler = callback;
        return jest.fn();
      }
    );

    const outcomePromise = downloadAsFile('content', 'Export.md') as Promise<DownloadOutcome>;
    await flushAsyncWork();

    handler!({
      payload: { url: 'blob:tauri-export', path: null, success: false, error: 'disk full' },
    });

    await expect(outcomePromise).resolves.toEqual({
      kind: 'tauri',
      success: false,
      error: 'disk full',
    });
  });

  it('cleans up when the native completion event never arrives', async () => {
    const unlisten = jest.fn();
    (listen as jest.Mock).mockResolvedValue(unlisten);
    const timeoutSpy = jest.spyOn(window, 'setTimeout');
    const pending = downloadAsFile('content', 'Other.md') as Promise<DownloadOutcome>;
    const timeout = timeoutSpy.mock.calls.find(([, delay]) => delay === 60_000)![0];
    await flushAsyncWork();
    (timeout as () => void)();
    await expect(pending).resolves.toEqual({
      kind: 'tauri',
      success: false,
      error: 'Timed out waiting for the file to finish saving.',
    });
    expect(unlisten).toHaveBeenCalledTimes(1);
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:tauri-export');
  });

  it('does not start a download when listener registration fails', async () => {
    (listen as jest.Mock).mockRejectedValue(new Error('event listener unavailable'));
    const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation();
    await expect(downloadAsFile('content', 'Export.md')).resolves.toEqual({
      kind: 'tauri',
      success: false,
      error: 'event listener unavailable',
    });
    expect(click).not.toHaveBeenCalled();
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:tauri-export');
  });

  it('treats a successful event without a path as success (macOS)', async () => {
    let handler: ((event: { payload: unknown }) => void) | undefined;
    (listen as jest.Mock).mockImplementation(
      async (_event: string, callback: (event: { payload: unknown }) => void) => {
        handler = callback;
        return jest.fn();
      }
    );

    const outcomePromise = downloadAsFile('content', 'Export.md') as Promise<DownloadOutcome>;
    await flushAsyncWork();

    handler!({ payload: { url: 'blob:tauri-export', path: null, success: true } });

    await expect(outcomePromise).resolves.toEqual({ kind: 'tauri', success: true, path: null });
  });
});

describe('reportDownloadOutcome', () => {
  beforeEach(() => {
    (toast.success as jest.Mock).mockClear();
    (toast.error as jest.Mock).mockClear();
  });

  it('reports browser exports with the existing success message', () => {
    reportDownloadOutcome({ kind: 'browser' }, 'Exported as Markdown');
    expect(toast.success).toHaveBeenCalledWith('Exported as Markdown');
  });

  it('names the actual saved path for Tauri exports', () => {
    reportDownloadOutcome(
      { kind: 'tauri', success: true, path: '/home/user/Downloads/Export.md' },
      'Exported as Markdown'
    );
    expect(toast.success).toHaveBeenCalledWith('Saved to /home/user/Downloads/Export.md');
  });

  it('does not turn a pathless macOS success into an error', () => {
    reportDownloadOutcome({ kind: 'tauri', success: true, path: null }, 'Exported as Markdown');
    expect(toast.success).toHaveBeenCalledWith(
      'Download complete (this platform does not report the saved path)'
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('reports Tauri failures distinctly', () => {
    reportDownloadOutcome(
      { kind: 'tauri', success: false, error: 'disk full' },
      'Exported as Markdown'
    );
    expect(toast.error).toHaveBeenCalledWith('Export failed: disk full');
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('waits for an asynchronous outcome before reporting', async () => {
    let resolveOutcome: (outcome: DownloadOutcome) => void = () => {};
    const pending = new Promise<DownloadOutcome>((resolve) => {
      resolveOutcome = resolve;
    });

    reportDownloadOutcome(pending, 'Exported as Markdown');
    expect(toast.success).not.toHaveBeenCalled();

    resolveOutcome({ kind: 'tauri', success: true, path: '/tmp/Export.md' });
    await pending;
    await Promise.resolve();

    expect(toast.success).toHaveBeenCalledWith('Saved to /tmp/Export.md');
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

describe('stripThinkingBlocks', () => {
  it('removes <thinking> blocks', () => {
    const result = stripThinkingBlocks('Before\n<thinking>hidden</thinking>\nAfter');
    expect(result).toBe('Before\nAfter');
    expect(result).not.toContain('hidden');
  });

  it('removes <think> blocks', () => {
    const result = stripThinkingBlocks('<think>hidden</think>\nVisible text');
    expect(result).toBe('Visible text');
    expect(result).not.toContain('hidden');
  });

  it('removes multi-line thinking blocks', () => {
    const result = stripThinkingBlocks('Start\n<thinking>\nline 1\nline 2\n</thinking>\nEnd');
    expect(result).toBe('Start\nEnd');
  });

  it('handles content with no thinking blocks', () => {
    expect(stripThinkingBlocks('Just regular text')).toBe('Just regular text');
  });

  it('removes multiple thinking blocks', () => {
    const result = stripThinkingBlocks(
      '<thinking>first</thinking>middle<thinking>second</thinking>'
    );
    expect(result).toBe('middle');
  });

  it('removes <think redacted> blocks (Anthropic RedactedThinkingBlock)', () => {
    const result = stripThinkingBlocks(
      '<think redacted>\nopaque payload\n</think redacted>\n\nVisible text'
    );
    expect(result).toBe('Visible text');
    expect(result).not.toContain('opaque payload');
  });

  it('removes an unclosed trailing <think> block (generation interrupted mid-thinking)', () => {
    const result = stripThinkingBlocks('Visible text\n<think>\nreasoning that never closed');
    expect(result).toBe('Visible text');
    expect(result).not.toContain('reasoning that never closed');
  });

  it('removes an unclosed trailing <thinking> block with no preceding text', () => {
    const result = stripThinkingBlocks('<thinking>\ninterrupted reasoning');
    expect(result).toBe('');
    expect(result).not.toContain('interrupted reasoning');
  });

  it('keeps literal <think> tags inside a fenced example', () => {
    const result = stripThinkingBlocks('Example:\n```xml\n<think>KEEP_LITERAL</think>\n```\nAfter');
    expect(result).toContain('KEEP_LITERAL');
    expect(result).toContain('```xml');
    expect(result).toContain('After');
  });

  it('keeps literal <think> tags inside inline code', () => {
    const result = stripThinkingBlocks('Use `<think>KEEP_LITERAL</think>` here.');
    expect(result).toBe('Use `<think>KEEP_LITERAL</think>` here.');
  });

  it('does not let a backtick inside thinking pair with one in the answer', () => {
    const result = stripThinkingBlocks(
      '<think>\nDiscuss the ` character.\n</think>\n\nUse `x` now.'
    );
    expect(result).toBe('Use `x` now.');
    expect(result).not.toContain('Discuss');
  });
});

describe('formatConversationAsMarkdown - tool invocation stripping', () => {
  it('strips fenced tool codeblocks embedded in assistant content when includeTools is false', () => {
    const messages: Message[] = [
      { role: 'user', content: 'List files' },
      {
        role: 'assistant',
        content: 'Sure, checking now.\n\n```shell\nls -la /secret/path\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('/secret/path');
    expect(result).toContain('Sure, checking now.');
    expect(result).toContain('Done.');
  });

  it('keeps fenced codeblocks with non-tool langtags when includeTools is false', () => {
    const messages: Message[] = [
      { role: 'assistant', content: 'Example:\n\n```python\nprint("hi")\n```' },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).toContain('print("hi")');
  });

  it('strips @tool: {...} invocation lines when includeTools is false', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Running it.\n@shell(call_1): {\n  "command": "rm -rf /secret"\n}\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('/secret');
    expect(result).toContain('Running it.');
    expect(result).toContain('Done.');
  });

  it('strips @tool: {...} invocations containing a closing brace inside a quoted string', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content:
          'Running it.\n@shell(call_1): {\n  "command": "echo \\"}\\" && cat /etc/shadow",\n  "cwd": "/secret-path"\n}\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('/etc/shadow');
    expect(result).not.toContain('/secret-path');
    expect(result).toContain('Running it.');
    expect(result).toContain('Done.');
  });

  it('strips dynamically named MCP fenced invocations when includeTools is false', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Checking.\n\n```github.create_issue\n{"title": "secret bug"}\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('secret bug');
    expect(result).toContain('Checking.');
    expect(result).toContain('Done.');
  });

  it('preserves non-JSON @-prefixed lines when includeTools is false', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Note: @shell: this is just a note, not a call\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).toContain('@shell: this is just a note');
    expect(result).toContain('Done.');
  });

  it('strips @tool calls whose JSON arguments contain backticks', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Running it.\n@save: {"content":"Use `x` now."}\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('Use `x` now.');
    expect(result).not.toContain('now."}');
    expect(result).toContain('Running it.');
    expect(result).toContain('Done.');
  });

  it('does not duplicate fenced examples when a prose backtick sits before a fence', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'See `this\n```xml\nKEEP_ONCE\n```\nand `that`.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result.match(/KEEP_ONCE/g)).toEqual(['KEEP_ONCE']);
  });

  it('preserves trailing text after closing brace of an @tool call when includeTools is false', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Running it.\n@shell(call_1): {"command": "ls"} and more\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('"command"');
    expect(result).toContain('and more');
    expect(result).toContain('Running it.');
    expect(result).toContain('Done.');
  });
});

describe('getExportableMessages - includeTools option', () => {
  const messagesWithTools: Message[] = [
    { role: 'user', content: 'Run a command' },
    { role: 'assistant', content: 'Sure' },
    { role: 'tool', content: 'tool output' },
  ];

  it('includes tool messages by default', () => {
    const result = getExportableMessages(messagesWithTools);
    expect(result.some((m) => m.role === 'tool')).toBe(true);
  });

  it('excludes tool messages when includeTools is false', () => {
    const result = getExportableMessages(messagesWithTools, { includeTools: false });
    expect(result.some((m) => m.role === 'tool')).toBe(false);
    expect(result).toHaveLength(2);
  });
});

describe('formatConversationAsMarkdown - example-preserving filters', () => {
  it('keeps a literal <think> example inside an xml fence when thinking is disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Example:\n\n```xml\n<think>KEEP_LITERAL</think>\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeThinking: false });
    expect(result).toContain('KEEP_LITERAL');
    expect(result).toContain('```xml');
    expect(result).toContain('Done.');
  });

  it('keeps a literal <think> example in inline code when thinking is disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'The tag is `<think>KEEP_LITERAL</think>` in the prompt.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeThinking: false });
    expect(result).toContain('KEEP_LITERAL');
    expect(result).toContain('`<think>KEEP_LITERAL</think>`');
  });

  it('keeps a literal @save example inside multiline inline code when tools are disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Example:\n\n`example\n@save: {"content":"KEEP_LITERAL"}\nend`\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).toContain('KEEP_LITERAL');
    expect(result).toContain('@save:');
    expect(result).toContain('Done.');
  });

  it('keeps a literal @save example inside a text fence when tools are disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Example:\n\n```text\n@save: {"content":"KEEP_LITERAL"}\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).toContain('KEEP_LITERAL');
    expect(result).toContain('@save:');
    expect(result).toContain('Done.');
  });

  it('strips actual memory tool fences when tools are disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Searching.\n\n```memory\nKEEP_SHOULD_GO query\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('KEEP_SHOULD_GO');
    expect(result).not.toContain('```memory');
    expect(result).toContain('Searching.');
    expect(result).toContain('Done.');
  });

  it('strips interrupted/unclosed save fences when tools are disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content: 'Writing.\n\n```save secret.txt\nKEEP_SHOULD_GO\nstill open',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('KEEP_SHOULD_GO');
    expect(result).not.toContain('secret.txt');
    expect(result).toContain('Writing.');
  });

  it('strips watch and request_tool_change fences when tools are disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content:
          'Working.\n\n```watch\nKEEP_WATCH\n```\n\n```request_tool_change\nKEEP_CHANGE\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).not.toContain('KEEP_WATCH');
    expect(result).not.toContain('KEEP_CHANGE');
    expect(result).toContain('Working.');
    expect(result).toContain('Done.');
  });

  it('keeps a nested save example inside a markdown fence when tools are disabled', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content:
          'Docs:\n\n```markdown\nUse this:\n```save example.txt\nKEEP_LITERAL\n```\n```\n\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, { includeTools: false });
    expect(result).toContain('KEEP_LITERAL');
    expect(result).toContain('```save example.txt');
    expect(result).toContain('Done.');
  });

  it('does not filter user or system messages when thinking and tools are disabled', () => {
    const messages: Message[] = [
      { role: 'system', content: '<think>KEEP_SYSTEM</think>\n```save x\nKEEP_SYSTEM_SAVE\n```' },
      { role: 'user', content: '<think>KEEP_USER</think>\n@save: {"content":"KEEP_USER_SAVE"}' },
      { role: 'assistant', content: '<think>DROP_ASSISTANT</think>\nVisible.' },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, {
      includeSystem: true,
      includeThinking: false,
      includeTools: false,
    });
    expect(result).toContain('KEEP_SYSTEM');
    expect(result).toContain('KEEP_SYSTEM_SAVE');
    expect(result).toContain('KEEP_USER');
    expect(result).toContain('KEEP_USER_SAVE');
    expect(result).not.toContain('DROP_ASSISTANT');
    expect(result).toContain('Visible.');
  });

  it('retains thinking and tool syntax in full mode', () => {
    const messages: Message[] = [
      {
        role: 'assistant',
        content:
          '<think>KEEP_THINK</think>\n```save secret.txt\nKEEP_SAVE\n```\n@save: {"content":"KEEP_AT"}\nDone.',
      },
    ];
    const result = formatConversationAsMarkdown('Chat', messages, {
      includeThinking: true,
      includeTools: true,
    });
    expect(result).toContain('KEEP_THINK');
    expect(result).toContain('KEEP_SAVE');
    expect(result).toContain('KEEP_AT');
    expect(result).toContain('Done.');
  });
});

describe('formatConversationAsMarkdown - thinking and tools options', () => {
  const messagesWithThinkingAndTools: Message[] = [
    { role: 'user', content: 'Question' },
    {
      role: 'assistant',
      content: '<thinking>internal reasoning</thinking>\nThe answer is 42.',
    },
    { role: 'tool', content: 'tool result' },
  ];

  it('strips thinking blocks when includeThinking is false', () => {
    const result = formatConversationAsMarkdown('Chat', messagesWithThinkingAndTools, {
      includeThinking: false,
    });
    expect(result).not.toContain('internal reasoning');
    expect(result).toContain('The answer is 42.');
  });

  it('includes thinking blocks when includeThinking is true', () => {
    const result = formatConversationAsMarkdown('Chat', messagesWithThinkingAndTools, {
      includeThinking: true,
    });
    expect(result).toContain('internal reasoning');
    expect(result).toContain('The answer is 42.');
  });

  it('excludes tool messages when includeTools is false', () => {
    const result = formatConversationAsMarkdown('Chat', messagesWithThinkingAndTools, {
      includeTools: false,
    });
    expect(result).not.toContain('tool result');
    expect(result).toContain('The answer is 42.');
  });

  it('includes tool messages when includeTools is true', () => {
    const result = formatConversationAsMarkdown('Chat', messagesWithThinkingAndTools, {
      includeTools: true,
    });
    expect(result).toContain('tool result');
  });
});

describe('copyConversationToClipboard', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: jest.fn().mockResolvedValue(undefined) },
      writable: true,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('writes markdown to the clipboard', async () => {
    await copyConversationToClipboard('Test Chat', sampleMessages);
    expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1);
    const written = (navigator.clipboard.writeText as jest.Mock).mock.calls[0][0] as string;
    expect(written).toContain('# Test Chat');
    expect(written).toContain('## User');
    expect(written).toContain('Hello, how are you?');
  });

  it('applies thinking and tools options', async () => {
    const messages: Message[] = [
      { role: 'user', content: 'Q' },
      { role: 'assistant', content: '<thinking>hidden</thinking>\nAnswer' },
      { role: 'tool', content: 'tool output' },
    ];
    await copyConversationToClipboard('Chat', messages, {
      includeThinking: false,
      includeTools: false,
    });
    const written = (navigator.clipboard.writeText as jest.Mock).mock.calls[0][0] as string;
    expect(written).not.toContain('hidden');
    expect(written).not.toContain('tool output');
    expect(written).toContain('Answer');
  });
});
