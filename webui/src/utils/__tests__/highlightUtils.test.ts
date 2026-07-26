import hljs from 'highlight.js';
import { highlightCode } from '../highlightUtils';

describe('highlightCode', () => {
  const code = 'x = 1\ny = 2\n';

  it('handles empty input', () => {
    expect(highlightCode('')).toEqual({ code: '' });
  });

  it('highlights known languages correctly', () => {
    const result = highlightCode(code, 'python');
    expect(result.language).toBe('python');
    expect(result.code).toContain('class');
  });

  // gptme tool invocation tag mappings
  it('maps ipython → python', () => {
    const result = highlightCode(code, 'ipython');
    expect(result.language).toBe('python');
  });

  it('maps tmux → bash', () => {
    const result = highlightCode('ls -la', 'tmux');
    expect(result.language).toBe('bash');
  });

  it('maps patch → diff', () => {
    const patchContent = '<<<<<<< ORIGINAL\nold line\n=======\nnew line\n>>>>>>> UPDATED';
    const result = highlightCode(patchContent, 'patch');
    expect(result.language).toBe('diff');
  });

  it('maps morph → diff', () => {
    // morph invocations use "// ... existing code ..." markers (morphllm.com format)
    const morphContent =
      '// ... existing code ...\ndef updated_fn():\n    return True\n// ... existing code ...';
    const result = highlightCode(morphContent, 'morph');
    expect(result.language).toBe('diff');
  });

  // gptme tool output tags: must return plain escaped text without auto-detection
  it('returns escaped plain text for stdout without running hljs.highlightAuto', () => {
    const highlightAutoSpy = jest.spyOn(hljs, 'highlightAuto');
    const output = 'Hello, world!\n<not-html>\n';
    const result = highlightCode(output, 'stdout');
    expect(result.language).toBe('stdout');
    // HTML entities escaped
    expect(result.code).toContain('&lt;not-html&gt;');
    // No hljs span tags
    expect(result.code).not.toContain('<span');
    // Performance guarantee: auto-detection must be bypassed entirely
    expect(highlightAutoSpy).not.toHaveBeenCalled();
    highlightAutoSpy.mockRestore();
  });

  it('returns escaped plain text for stderr', () => {
    const highlightAutoSpy = jest.spyOn(hljs, 'highlightAuto');
    const output = 'Error: something went wrong\n<traceback>\n';
    const result = highlightCode(output, 'stderr');
    expect(result.language).toBe('stderr');
    expect(result.code).toContain('&lt;traceback&gt;');
    expect(result.code).not.toContain('<span');
    expect(highlightAutoSpy).not.toHaveBeenCalled();
    highlightAutoSpy.mockRestore();
  });

  it('returns escaped plain text for output', () => {
    const highlightAutoSpy = jest.spyOn(hljs, 'highlightAuto');
    const result = highlightCode('plain output', 'output');
    expect(result.language).toBe('output');
    expect(result.code).not.toContain('<span');
    expect(highlightAutoSpy).not.toHaveBeenCalled();
    highlightAutoSpy.mockRestore();
  });

  it('escapes HTML entities in plain text output', () => {
    const result = highlightCode('<b>bold</b> & "quoted"', 'stdout');
    expect(result.code).toBe('&lt;b&gt;bold&lt;/b&gt; &amp; "quoted"');
  });
});
