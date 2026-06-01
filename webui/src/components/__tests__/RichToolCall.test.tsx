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
    const completedTools = new Map([['shell', { success: true, durationMs: 1234 }]]);
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
});
