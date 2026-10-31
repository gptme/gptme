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

  it('can stop after the first outer block without classifying later content', () => {
    const content = '```text\nliteral\n```\n```shell\ncommand\n```';
    expect(scanMarkdownFences(content, true)).toHaveLength(1);
    expect(scanMarkdownFences(content)).toHaveLength(2);
  });
});
