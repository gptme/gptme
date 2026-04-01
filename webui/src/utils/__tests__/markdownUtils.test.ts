import {
  processNestedCodeBlocks,
  transformThinkingTags,
  parseMarkdownContent,
} from '../markdownUtils';
import '@testing-library/jest-dom';

// TODO: use these tests for streaming markdown logic in smd.js (no longer using transformThinkingTags)

describe('processNestedCodeBlocks', () => {
  it('should handle nested code blocks', () => {
    const input = `\`\`\`markdown
Here's a nested block
\`\`\`python
print("hello")
\`\`\`
\`\`\``;

    const expected = `\`\`\`markdown
Here's a nested block
\`\`\`python
print("hello")
\`\`\`
\`\`\``;

    const result = processNestedCodeBlocks(input);
    expect(result.processedContent).toBe(expected);
    expect(result.langtags.filter(Boolean)).toEqual(['markdown', 'python']);
  });

  it('should not modify single code blocks', () => {
    const input = `\`\`\`python
print("hello")
\`\`\``;

    const result = processNestedCodeBlocks(input);
    expect(result.processedContent).toBe(input);
    expect(result.langtags.filter(Boolean)).toEqual(['python']);
  });

  it('should handle multiple nested blocks', () => {
    const input = `\`\`\`markdown
First block
\`\`\`python
print("hello")
\`\`\`
Second block
\`\`\`javascript
console.log("world")
\`\`\`
\`\`\``;

    const expected = `\`\`\`markdown
First block
\`\`\`python
print("hello")
\`\`\`
Second block
\`\`\`javascript
console.log("world")
\`\`\`
\`\`\``;

    const result = processNestedCodeBlocks(input);
    expect(result.processedContent).toBe(expected);
    expect(result.langtags.filter(Boolean)).toEqual(['markdown', 'python', 'javascript']);
  });

  it('returns original content when no code blocks', () => {
    const input = 'Hello world';
    const result = processNestedCodeBlocks(input);
    expect(result.processedContent).toBe(input);
    expect(result.langtags).toEqual([]);
  });
});

describe('transformThinkingTags', () => {
  it('should transform <thinking> tags to details/summary', () => {
    const input = 'Before <thinking>Some thoughts</thinking> After';
    const expected =
      'Before <details type="thinking"><summary>💭 Thinking</summary>\n\nSome thoughts\n\n</details> After';
    expect(transformThinkingTags(input)).toBe(expected);
  });

  it('should transform <think> tags to details/summary', () => {
    const input = 'Before <think>Some thoughts</think> After';
    const expected =
      'Before <details type="thinking"><summary>💭 Thinking</summary>\n\nSome thoughts\n\n</details> After';
    expect(transformThinkingTags(input)).toBe(expected);
  });

  it('should handle multiple thinking tags', () => {
    const input = '<thinking>First thought</thinking> Middle <thinking>Second thought</thinking>';
    const expected =
      '<details type="thinking"><summary>💭 Thinking</summary>\n\nFirst thought\n\n</details> Middle <details type="thinking"><summary>💭 Thinking</summary>\n\nSecond thought\n\n</details>';
    expect(transformThinkingTags(input)).toBe(expected);
  });

  it('should not transform thinking tags within code blocks', () => {
    const input = '`<thinking>Code block</thinking>`';
    expect(transformThinkingTags(input)).toBe(input);
  });

  it('preserves content outside thinking tags', () => {
    const input = 'Before <thinking>thinking</thinking> after';
    const expected =
      'Before <details type="thinking"><summary>💭 Thinking</summary>\n\nthinking\n\n</details> after';
    expect(transformThinkingTags(input)).toBe(expected);
  });
});

describe('parseMarkdownContent', () => {
  it('parses basic markdown', () => {
    const input = '# Hello\n\nThis is a test';
    const result = parseMarkdownContent(input);
    expect(result).toContain('<h1>Hello</h1>');
    expect(result).toContain('<p>This is a test</p>');
  });

  it('handles code blocks with language tags', () => {
    const input = "```python\nprint('hello')\n```";
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>💻 python</summary>');
    expect(result).toContain('<span class="hljs-built_in">print</span>');
    expect(result).toContain('<span class="hljs-string">&#x27;hello&#x27;</span>');
  });

  it('detects file paths in code blocks', () => {
    const input = "```src/test.py\nprint('hello')\n```";
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>📄 src/test.py</summary>');
  });

  it('detects tool commands in code blocks', () => {
    const input = '```shell\nls -la\n```';
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>🛠️ shell</summary>');
  });

  it('detects output blocks', () => {
    const input = '```stdout\nHello world\n```';
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>📤 stdout</summary>');
  });

  it('detects write operations in code blocks', () => {
    const input = '```save test.txt\nHello world\n```';
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>📝 save test.txt</summary>');
  });

  it('handles thinking tags', () => {
    const input = '<thinking>Some thought</thinking>';
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>💭 Thinking</summary>');
    expect(result).toContain('Some thought');
  });

  it('handles nested code blocks', () => {
    const input = "```markdown\nHere's a nested block\n```python\nprint('hello')\n```\n```";
    const result = parseMarkdownContent(input);
    expect(result).toContain('<summary>💻 markdown</summary>');
    expect(result).toContain('<span class="hljs-code">```python');
    expect(result).toContain('print(&#x27;hello&#x27;)');
  });

  it('handles complex message with multiple content types', () => {
    const input = `The gptme web UI offers several advantages over the CLI interface:

1. **Rich Message Display**:
   - Syntax highlighted code blocks
   - Collapsible sections for code and thinking
   - Different styles for user/assistant/system messages
   - Emoji indicators for different types of content:
     - 📄 File paths
     - 🛠️ Tool usage
     - 📤 Command output
     - 💻 Code blocks

2. **Interactive Features**:
   - Real-time streaming of responses
   - Easy navigation between conversations
   - Ability to view and restore conversation history

3. **Integration with gptme-server**:
   - Connects to your local gptme instance
   - Access to all local tools and capabilities
   - Secure local execution of commands

Here's an example showing different types of content:

\`\`\`/path/to/file.py
# This shows as a file path
\`\`\`

\`\`\`shell
# This shows as a tool
ls -la
\`\`\`

\`\`\`stdout
# This shows as command output
total 0
drwxr-xr-x 2 user user 4096 Jan 29 10:48 .
\`\`\`

<thinking>
Thinking blocks are collapsible and help show my reasoning process
</thinking>

You can try the web UI by:
1. Starting a local gptme-server: \`gptme-server --cors-origin='http://localhost:5701'\`
2. Running the web UI: \`npm run dev\`
3. Opening http://localhost:5701 in your browser`;

    const result = parseMarkdownContent(input);

    // Check markdown formatting is preserved
    expect(result).toContain(
      '<p>The gptme web UI offers several advantages over the CLI interface:</p>'
    );
    expect(result).toContain('<li><p><strong>Rich Message Display</strong>:</p>');

    // Check list items are preserved
    expect(result).toContain('<li>Syntax highlighted code blocks</li>');
    expect(result).toContain('<li>📄 File paths</li>');

    // Check code blocks with correct emoji indicators
    expect(result).toContain('<summary>📄 /path/to/file.py</summary>');
    expect(result).toContain('<summary>🛠️ shell</summary>');
    expect(result).toContain('<summary>📤 stdout</summary>');

    // Check code block content with syntax highlighting
    expect(result).toContain('<span class="hljs-comment"># This shows as a file path</span>');
    expect(result).toContain(
      '<code class="hljs language-shell"><span class="hljs-comment"># This shows as a tool</span>'
    );
    expect(result).toContain('# This shows as command output');

    // Check for the directory listing - with HTML tags stripped
    expect(result.replace(/<[^>]*>/g, '')).toContain('drwxr-xr-x 2 user user');

    // Check final content is included
    expect(result).toContain('You can try the web UI by:');
    expect(result).toContain('<code>gptme-server --cors-origin=');
    expect(result).toContain('<code>npm run dev</code>');
    expect(result).toContain('http://localhost:5701');

    // Check thinking block
    expect(result).toContain('<details type="thinking"><summary>💭 Thinking</summary>');
    expect(result).toContain('Thinking blocks are collapsible');
  });
});
