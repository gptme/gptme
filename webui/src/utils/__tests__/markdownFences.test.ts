import { scanMarkdownFences } from '../markdownFences';
import { processNestedCodeBlocks } from '../markdownUtils';

describe('shared transcript fence boundaries', () => {
  it('records same-length tagged nesting, preserving exact source ranges', () => {
    const content = 'Before\n```save x.md\n```python\nprint(1)\n```\n```\nAfter';
    const blocks = scanMarkdownFences(content);
    expect(blocks.map(({ tag, depth, closerLine }) => ({ tag, depth, closerLine }))).toEqual([
      { tag: 'save x.md', depth: 0, closerLine: 5 },
      { tag: 'python', depth: 1, closerLine: 4 },
    ]);
    expect(content.slice(blocks[0].start, blocks[0].end)).toBe(
      '```save x.md\n```python\nprint(1)\n```\n```\n'
    );
  });

  it.each(['````', '~~~~'])(
    'keeps shorter and different-marker fences literal inside %s',
    (marker) => {
      const content = `${marker}markdown\n\x60\x60\x60shell\nprivate-looking example\n\x60\x60\x60\n${marker}\nAfter`;
      const blocks = scanMarkdownFences(content);
      expect(blocks).toHaveLength(1);
      expect(blocks[0].closerLine).toBe(4);
      expect(content.slice(blocks[0].end)).toBe('After');
      expect(processNestedCodeBlocks(content).processedContent).toBe(content);
    }
  );

  it.each(['```', '````'])(
    'records a longer nested closer and the matching %s outer closer',
    (outerMarker) => {
      const content = [
        `${outerMarker}save test.md`,
        '````python',
        'x',
        '`````',
        'tail',
        outerMarker,
        'Public answer.',
      ].join('\n');
      const blocks = scanMarkdownFences(content);
      expect(
        blocks.map(({ depth, closerLine, closerWidth }) => ({ depth, closerLine, closerWidth }))
      ).toEqual([
        { depth: 0, closerLine: 5, closerWidth: outerMarker.length },
        { depth: 1, closerLine: 3, closerWidth: 5 },
      ]);
      expect(content.slice(blocks[0].end)).toBe('Public answer.');
      expect(processNestedCodeBlocks(content).processedContent).toBe(
        '``````save test.md\n````python\nx\n`````\ntail\n``````\nPublic answer.'
      );
    }
  );

  it('retains the deepest descendant across shallower sibling blocks', () => {
    const blocks = scanMarkdownFences('```a\n```b\n```c\n```\n```\n```d\n```\n```');
    expect(blocks[0].maxDescendantDepth).toBe(2);
  });

  it('records open literal and nested blocks through EOF', () => {
    const content = '```markdown\n```shell\nunfinished';
    const blocks = scanMarkdownFences(content);
    expect(blocks).toHaveLength(2);
    expect(blocks.every((block) => block.closerLine === null && block.end === content.length)).toBe(
      true
    );
  });

  it('preserves CRLF offsets and indentation', () => {
    const content = 'Before\r\n  ```text\r\n<think>literal</think>\r\n  ```\r\nAfter';
    const [block] = scanMarkdownFences(content);
    expect(content.slice(block.start, block.end)).toBe(
      '  ```text\r\n<think>literal</think>\r\n  ```\r\n'
    );
    expect(content.slice(block.end)).toBe('After');
  });

  it('widens unequal tagged descendants without carrying widths across roots', () => {
    const content =
      '```markdown\n````text\n`````xml\nliteral\n`````\n````\n```text\nsibling\n```\n```\n```python\nordinary\n```';
    expect(processNestedCodeBlocks(content).processedContent).toBe(
      '```````markdown\n``````text\n`````xml\nliteral\n`````\n``````\n```text\nsibling\n```\n```````\n```python\nordinary\n```'
    );
  });

  it('does not mistake a line-start inline span for an unfinished fence', () => {
    expect(scanMarkdownFences('```literal```\n<think>reasoning</think>')).toEqual([]);
  });

  it('does not let invalid nested backtick tags capture the outer closer', () => {
    const content = '```text\n```literal```\n```\nPublic answer.';
    const [block, ...rest] = scanMarkdownFences(content);
    expect(rest).toEqual([]);
    expect(block.closerLine).toBe(2);
    expect(content.slice(block.end)).toBe('Public answer.');
  });

  it('scans a first block from a source offset without splitting the remaining text', () => {
    const prefix = '```text\nprevious block\n```\n';
    const first = '  ```shell\r\ncommand\r\n  ```\r\n';
    const content = prefix + first + '```text\nremaining block\n```\n'.repeat(100);
    const split = jest.spyOn(String.prototype, 'split');
    let blocks: ReturnType<typeof scanMarkdownFences>;
    try {
      blocks = scanMarkdownFences(content, true, prefix.length);
      expect(split).not.toHaveBeenCalled();
    } finally {
      split.mockRestore();
    }
    expect(blocks).toHaveLength(1);
    expect(blocks[0].start).toBe(prefix.length);
    expect(blocks[0].end).toBe(prefix.length + first.length);
    expect(content.slice(blocks[0].start, blocks[0].end)).toBe(first);
  });

  it('does not search past an inline span for a later fence in first-block mode', () => {
    expect(scanMarkdownFences('```literal```\n```shell\ncommand\n```', true)).toEqual([]);
  });

  it('maps a nested quoted CRLF fence to original offsets including quote-only blank lines', () => {
    const prefix = 'Before\r\n';
    const literal = '> > ```xml\r\n> >\r\n> > <think>literal</think>\r\n> > ```\r\n';
    const content = prefix + literal + '<think>private</think>';
    const [block] = scanMarkdownFences(content, true, prefix.length, 2);
    expect(block.start).toBe(prefix.length);
    expect(block.closerLine).toBe(3);
    expect(content.slice(block.start, block.end)).toBe(literal);
    expect(content.slice(block.end)).toBe('<think>private</think>');
  });

  it.each(['<think>private</think>', '> <think>private</think>'])(
    'ends an interrupted nested quote before its depth decreases: %s',
    (suffix) => {
      const literal = '> > ```xml\n> > <think>literal</think>\n';
      const [block] = scanMarkdownFences(literal + suffix, true, 0, 2);
      expect(block.closerLine).toBeNull();
      expect(block.end).toBe(literal.length);
    }
  );

  it('keeps an interrupted quoted fence through EOF', () => {
    const content = '> ~~~xml\n> <think>literal</think>';
    const [block] = scanMarkdownFences(content, true, 0, 1);
    expect(block.closerLine).toBeNull();
    expect(block.end).toBe(content.length);
  });

  it('can stop after the first outer block without classifying later content', () => {
    const content = '```text\nliteral\n```\n```shell\ncommand\n```';
    expect(scanMarkdownFences(content, true)).toHaveLength(1);
    expect(scanMarkdownFences(content)).toHaveLength(2);
  });
});
