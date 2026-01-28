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

    // Output should be:
    // <div>
    //   <p>
    //     <details type="thinking" open="true">
    //       <summary>💭 Thinking</summary>
    //       <div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is a thinking block</div>
    //     </details>
    //     some other text
    //   </p>
    // </div>
    const expected =
      '<p><details type="thinking"><summary>💭 Thinking</summary><div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is a thinking block</div></details> some other text</p>';

    let div = parse(markdown);
    expect(div.innerHTML).toBe(expected);

    div = parse(markdown, true);
    expect(div.innerHTML).toBe(expected);
  });

  it('should handle one thinking block at end of text', () => {
    const markdown = `some other text <thinking>This is a thinking block</thinking>`;

    // Output should be:
    // <div>
    //   <p>
    //     some other text
    //     <details type="thinking" open="true">
    //       <summary>💭 Thinking</summary>
    //       <div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is a thinking block</div>
    //     </details>
    //   </p>
    // </div>
    const expected =
      '<p>some other text <details type="thinking"><summary>💭 Thinking</summary><div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is a thinking block</div></details></p>';

    let div = parse(markdown);
    expect(div.innerHTML).toBe(expected);

    div = parse(markdown, true);
    expect(div.innerHTML).toBe(expected);
  });

  it('should handle multiple thinking blocks', () => {
    const markdown = `some other text <thinking>This is a thinking block</thinking> some other text <thinking>This is another thinking block</thinking>`;

    // Output should be:
    // <div>
    //   <p>
    //     some other text
    //     <details type="thinking" open="true">
    //       <summary>💭 Thinking</summary>
    //       <div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is a thinking block</div>
    //     </details>
    //     some other text
    //     <details type="thinking" open="true">
    //       <summary>💭 Thinking</summary>
    //       <div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is another thinking block</div>
    //     </details>
    //   </p>
    // </div>
    const expected =
      '<p>some other text <details type="thinking"><summary>💭 Thinking</summary><div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is a thinking block</div></details> some other text <details type="thinking"><summary>💭 Thinking</summary><div style="white-space: pre-wrap; padding-top: 0px; padding-bottom: 0.5rem;">This is another thinking block</div></details></p>';

    const div = parse(markdown);
    expect(div.innerHTML).toBe(expected);

    const div2 = parse(markdown, true);
    expect(div2.innerHTML).toBe(expected);
  });
});

describe('renderCodeBlocks', () => {
  it('should handle one python code block at start of text', () => {
    const markdown = `\`\`\`python\nThis is a code block\n\`\`\` some other text`;

    const expected =
      '<details open="true"><summary>💻 python</summary><pre><code class="hljs language-python">This <span class="hljs-keyword">is</span> a code block</code></pre></details><p>some other text</p>';

    let div = parse(markdown);
    expect(div.innerHTML).toBe(expected);

    div = parse(markdown, true);
    expect(div.innerHTML).toBe(expected);
  });

  it('should handle one python code block at end of text', () => {
    const markdown = `some other text\n\`\`\`python\nThis is a code block\n\`\`\``;

    const expected =
      '<p>some other text<details open="true"><summary>💻 python</summary><pre><code class="hljs language-python">This <span class="hljs-keyword">is</span> a code block</code></pre></details></p>';

    let div = parse(markdown);
    expect(div.innerHTML).toBe(expected);

    div = parse(markdown, true);
    expect(div.innerHTML).toBe(expected);
  });
});

describe('renderMarkdownBlocks', () => {
  it('should handle one markdown block at start of text', () => {
    const markdown = `\`\`\`markdown\nThis is a markdown block\n\`\`\`\nsome other text`;

    const expected =
      '<details open="true"><summary>💻 markdown</summary><pre><code class="hljs language-markdown">This is a markdown block</code></pre></details><p>some other text</p>';

    let div = parse(markdown, false, false);
    expect(div.innerHTML).toBe(expected);

    div = parse(markdown, true, false);
    expect(div.innerHTML).toBe(expected);
  });
});
