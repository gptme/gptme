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
