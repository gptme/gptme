import { describe, it, expect } from '@jest/globals';
import { renderToolCallsFromContent } from '../RichToolCall';

describe('renderToolCallsFromContent', () => {
  it('returns original content when no tool calls', () => {
    const result = renderToolCallsFromContent('just some text\nwithout any codeblocks');
    expect(result.content).toBe('just some text\nwithout any codeblocks');
    expect(result.toolCalls).toHaveLength(0);
  });

  it('extracts tool calls from markdown codeblocks', () => {
    const content = `Let me save this file:

\`\`\`save
/home/user/test.ts
const x = 1;
\`\`\``;
    const result = renderToolCallsFromContent(content);
    expect(result.content).toContain('Let me save this file');
    expect(result.toolCalls.length).toBeGreaterThan(0);
    // The codeblock should be removed from content
    expect(result.content).not.toContain('```save');
  });

  it('handles multiple tool calls', () => {
    const content = `First a shell command:

\`\`\`shell
echo hello
\`\`\`

Then save the file:

\`\`\`save
output.txt
hello
\`\`\``;
    const result = renderToolCallsFromContent(content);
    expect(result.toolCalls.length).toBeGreaterThanOrEqual(2);
  });

  it('attaches completion metadata when provided', () => {
    const content = `\`\`\`shell
ls -la
\`\`\``;
    const completedTools = new Map([[0, { success: true, durationMs: 1234 }]]);
    const result = renderToolCallsFromContent(content, completedTools);
    expect(result.toolCalls.length).toBeGreaterThan(0);
  });

  it('handles empty content gracefully', () => {
    const result = renderToolCallsFromContent('');
    expect(result.content).toBe('');
    expect(result.toolCalls).toHaveLength(0);
  });

  it('preserves non-codeblock text', () => {
    const content = `I will now run:

\`\`\`shell
npm test
\`\`\`

The tests should pass.`;
    const result = renderToolCallsFromContent(content);
    expect(result.content).toContain('I will now run:');
    expect(result.content).toContain('The tests should pass.');
  });

  it('ignores non-tool language codeblocks (e.g. js, python)', () => {
    const content = `Here is some code:

\`\`\`js
const x = 1;
\`\`\`

And a real tool call:

\`\`\`shell
echo hi
\`\`\``;
    const result = renderToolCallsFromContent(content);
    expect(result.toolCalls).toHaveLength(1);
    // The js block should remain in the cleaned content
    expect(result.content).toContain('```js');
    expect(result.content).toContain('const x = 1;');
    // The shell block should be removed (rendered as RichToolCall)
    expect(result.content).not.toContain('```shell');
  });

  it('uses per-index completion metadata, not per-tool-name', () => {
    const content = `\`\`\`shell
echo first
\`\`\`

\`\`\`shell
echo second
\`\`\``;
    const completedTools = new Map([
      [0, { success: true, durationMs: 100 }],
      [1, { success: false, durationMs: 200 }],
    ]);
    const result = renderToolCallsFromContent(content, completedTools);
    expect(result.toolCalls).toHaveLength(2);
    // Keys should be unique per occurrence, not per tool name
    const keys = result.toolCalls.map((node: any) => node?.key);
    expect(new Set(keys).size).toBe(2);
    expect(keys[0]).not.toBe(keys[1]);
  });
});
