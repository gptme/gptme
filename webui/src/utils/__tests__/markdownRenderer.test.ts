import '@testing-library/jest-dom';
import { customRenderer } from '../markdownRenderer';
import * as smd from '@/utils/smd';

function parse(markdown: string, streaming: boolean = false, log: boolean = false) {
  const div = document.createElement('div');
  const renderer = customRenderer(div, log);
  const parser = smd.parser(renderer);

  if (streaming) {
    for (const char of markdown) {
      smd.parser_write(parser, char);
    }
  } else {
    smd.parser_write(parser, markdown);
  }

  smd.parser_end(parser);
  return div;
}

function expectInlineCodeBlock(div: HTMLElement, language: string, codeHtml: string) {
  const label = div.querySelector('.inline-codeblock-label');
  expect(label).not.toBeNull();
  expect(label?.querySelector('.codeblock-icon svg')).not.toBeNull();
  expect(label?.querySelector('.codeblock-label-text')).toHaveTextContent(language);

  const code = div.querySelector(`.inline-codeblock code.language-${language}`);
  expect(code).not.toBeNull();
  expect(code?.innerHTML).toBe(codeHtml);
}

function expectThinkingBlocks(div: HTMLElement, contents: string[]) {
  const details = Array.from(div.querySelectorAll('details[type="thinking"]'));
  expect(details).toHaveLength(contents.length);

  details.forEach((block, index) => {
    const summary = block.querySelector('summary');
    expect(summary).not.toBeNull();
    expect(summary).toHaveTextContent('Thinking');
    expect(summary?.querySelector('.codeblock-icon svg.lucide-brain')).not.toBeNull();

    const content = block.querySelector('summary + div');
    expect(content).not.toBeNull();
    expect(content).toHaveTextContent(contents[index]);
    expect(content?.getAttribute('style')).toBe(
      'white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;'
    );
  });
}

describe('simple text rendering', () => {
  const markdown = 'This is a test';
  it('all at once, should render standard text', () => {
    const div = parse(markdown);

    // Output should be:
    // <div>
    //   <p>
    //     This is a test
    //   </p>
    // </div>
    expect(div.innerHTML).toBe('<p>This is a test</p>');
  });

  it('should render standard text, one character at a time', () => {
    const div = parse(markdown, true);

    // Output should be:
    // <div>
    //   <p>
    //     This is a test
    //   </p>
    // </div>
    expect(div.innerHTML).toBe('<p>This is a test</p>');
  });
});

describe('renderThinkingBlocks', () => {
  it('should handle one thinking block at start of text', () => {
    const markdown = `<thinking>This is a thinking block</thinking> some other text`;

    let div = parse(markdown);
    expectThinkingBlocks(div, ['This is a thinking block']);
    expect(div.innerHTML).toMatch(/^<p><details type="thinking">/);
    expect(div.innerHTML).toMatch(/<\/details> some other text<\/p>$/);

    div = parse(markdown, true);
    expectThinkingBlocks(div, ['This is a thinking block']);
    expect(div.innerHTML).toMatch(/^<p><details type="thinking">/);
    expect(div.innerHTML).toMatch(/<\/details> some other text<\/p>$/);
  });

  it('should handle one thinking block at end of text', () => {
    const markdown = `some other text <thinking>This is a thinking block</thinking>`;

    let div = parse(markdown);
    expectThinkingBlocks(div, ['This is a thinking block']);
    expect(div.innerHTML).toMatch(/^<p>some other text <details type="thinking">/);
    expect(div.innerHTML).toMatch(/<\/details><\/p>$/);

    div = parse(markdown, true);
    expectThinkingBlocks(div, ['This is a thinking block']);
    expect(div.innerHTML).toMatch(/^<p>some other text <details type="thinking">/);
    expect(div.innerHTML).toMatch(/<\/details><\/p>$/);
  });

  it('should handle multiple thinking blocks', () => {
    const markdown = `some other text <thinking>This is a thinking block</thinking> some other text <thinking>This is another thinking block</thinking>`;

    const div = parse(markdown);
    expectThinkingBlocks(div, ['This is a thinking block', 'This is another thinking block']);
    expect(div.innerHTML).toMatch(/^<p>some other text <details type="thinking">/);
    expect(div.innerHTML).toContain('</details> some other text <details type="thinking">');
    expect(div.innerHTML).toMatch(/<\/details><\/p>$/);

    const div2 = parse(markdown, true);
    expectThinkingBlocks(div2, ['This is a thinking block', 'This is another thinking block']);
    expect(div2.innerHTML).toMatch(/^<p>some other text <details type="thinking">/);
    expect(div2.innerHTML).toContain('</details> some other text <details type="thinking">');
    expect(div2.innerHTML).toMatch(/<\/details><\/p>$/);
  });
});

describe('renderCodeBlocks', () => {
  it('should handle one python code block at start of text', () => {
    const markdown = `\`\`\`python\nThis is a code block\n\`\`\` some other text`;
    const codeHtml = 'This <span class="hljs-keyword">is</span> a code block';

    let div = parse(markdown);
    expectInlineCodeBlock(div, 'python', codeHtml);
    expect(div.lastElementChild?.outerHTML).toBe('<p>some other text</p>');

    div = parse(markdown, true);
    expectInlineCodeBlock(div, 'python', codeHtml);
    expect(div.lastElementChild?.outerHTML).toBe('<p>some other text</p>');
  });

  it('should handle one python code block at end of text', () => {
    const markdown = `some other text\n\`\`\`python\nThis is a code block\n\`\`\``;
    const codeHtml = 'This <span class="hljs-keyword">is</span> a code block';

    let div = parse(markdown);
    expectInlineCodeBlock(div, 'python', codeHtml);
    expect(div.firstElementChild?.textContent).toContain('some other text');

    div = parse(markdown, true);
    expectInlineCodeBlock(div, 'python', codeHtml);
    expect(div.firstElementChild?.textContent).toContain('some other text');
  });
});

describe('renderMarkdownBlocks', () => {
  it('should handle one markdown block at start of text', () => {
    const markdown = `\`\`\`markdown\nThis is a markdown block\n\`\`\`\nsome other text`;
    const codeHtml = 'This is a markdown block';

    let div = parse(markdown, false, false);
    expectInlineCodeBlock(div, 'markdown', codeHtml);
    expect(div.lastElementChild?.outerHTML).toBe('<p>some other text</p>');

    div = parse(markdown, true, false);
    expectInlineCodeBlock(div, 'markdown', codeHtml);
    expect(div.lastElementChild?.outerHTML).toBe('<p>some other text</p>');
  });
});

function parseStandard(markdown: string) {
  const div = document.createElement('div');
  const renderer = customRenderer(div, false, false, true, true);
  const parser = smd.parser(renderer);
  smd.parser_write(parser, markdown);
  smd.parser_end(parser);
  return div;
}

describe('streaming performance', () => {
  it('does not rewrite innerHTML on every streaming token inside a code block', () => {
    // Regression test for gptme/gptme#3362 (O(n²) DOM writes during streaming).
    // Before the fix: add_text set data.code.innerHTML = fullEscapedText on every token →
    // O(n) per token, O(n²) total for a code block with n characters.
    // After the fix: add_text appends a text node (O(1) per token); innerHTML is set
    // exactly once in end_token when syntax highlighting runs.
    const div = document.createElement('div');
    const renderer = customRenderer(div);
    const parser = smd.parser(renderer);

    let innerHTMLWriteCount = 0;
    let maxCodeChildNodes = 0;
    let codeAppendChildCount = 0;
    const originalAppendChild = Node.prototype.appendChild;
    const appendChildSpy = jest.spyOn(Node.prototype, 'appendChild').mockImplementation(function <
      T extends Node,
    >(this: Node, node: T): T {
      const result = Reflect.apply(originalAppendChild, this, [node]) as T;
      if (this instanceof HTMLElement && this.tagName === 'CODE') {
        codeAppendChildCount++;
        maxCodeChildNodes = Math.max(maxCodeChildNodes, this.childNodes.length);
      }
      return result;
    });
    const origDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
    Object.defineProperty(Element.prototype, 'innerHTML', {
      set(value: string) {
        if (this.tagName === 'CODE') innerHTMLWriteCount++;
        origDescriptor!.set!.call(this, value);
      },
      get() {
        return origDescriptor!.get!.call(this);
      },
      configurable: true,
    });

    try {
      // Stream a 300-character code block token by token (simulating LLM streaming)
      const codeBlock = '```python\n' + 'x = 1\n'.repeat(50) + '```\n';
      for (const char of codeBlock) {
        smd.parser_write(parser, char);
      }
      smd.parser_end(parser);
    } finally {
      Object.defineProperty(Element.prototype, 'innerHTML', origDescriptor!);
      appendChildSpy.mockRestore();
    }

    // Streaming coalesces all fragments into one text node instead of retaining a
    // child per token. Only the first fragment should append a child to the code node.
    expect(maxCodeChildNodes).toBe(1);
    expect(codeAppendChildCount).toBe(1);

    // With the O(1) fix, innerHTML is written at most twice per code block:
    // once (optionally) for the inline conversion in end_token, and once for highlighting.
    // Pre-fix: it was written once per character (300+ times for this block).
    expect(innerHTMLWriteCount).toBeLessThanOrEqual(3);

    // Verify the final output is still correct
    const code = div.querySelector('code');
    expect(code).not.toBeNull();
    expect(code?.textContent).toContain('x = 1');
  });

  it('produces correct output when streaming HTML-special characters', () => {
    // Text nodes auto-escape HTML entities; verify that < > & in code are preserved
    // correctly as text (not interpreted as HTML tags) after the streaming fix.
    const markdown = '```python\nif x < 10 and y > 5:\n    print(f"x={x} & y={y}")\n```\n';
    const div = parse(markdown, true);
    const code = div.querySelector('code');
    expect(code).not.toBeNull();
    // These should appear as plain text (escaped), not raw HTML
    expect(code?.textContent).toContain('<');
    expect(code?.textContent).toContain('>');
    expect(code?.textContent).toContain('&');
  });
});

describe('standardMarkdown mode', () => {
  it('renders plain text without chrome', () => {
    const div = parseStandard('Hello world');
    expect(div.innerHTML).toBe('<p>Hello world</p>');
  });

  it('renders code blocks as plain pre+code without details/summary', () => {
    const markdown = '```python\nprint("hello")\n```';
    const div = parseStandard(markdown);

    // No <details> or <summary> wrapper
    expect(div.querySelector('details')).toBeNull();
    expect(div.querySelector('summary')).toBeNull();

    // Plain <pre><code> structure
    const pre = div.querySelector('pre');
    expect(pre).not.toBeNull();
    const code = div.querySelector('pre code');
    expect(code).not.toBeNull();
    expect(code?.textContent).toContain('print("hello")');
  });

  it('renders multi-line code blocks without inline-codeblock conversion', () => {
    // Chat mode converts short blocks (≤2 lines) to inline-codeblock; standard mode should not
    const markdown = '```sh\necho hello\n```';
    const div = parseStandard(markdown);

    expect(div.querySelector('.inline-codeblock')).toBeNull();
    expect(div.querySelector('pre')).not.toBeNull();
  });

  it('does not collapse tool-use blocks', () => {
    // In chat mode, 'shell' blocks collapse by default; in standard mode no details exist
    const markdown = '```shell\nls -la\n```';
    const div = parseStandard(markdown);

    expect(div.querySelector('details')).toBeNull();
    expect(div.querySelector('pre')).not.toBeNull();
  });
});
