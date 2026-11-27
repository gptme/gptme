import { codeBlockLabelHtml, displayLangtag, iconForLangtag } from '../codeBlockIcons';

describe('code block labels for native tool calls', () => {
  it('shows the tool, not the internal "tool-call" fence prefix', () => {
    expect(displayLangtag('tool-call shell')).toBe('shell');
    expect(displayLangtag('tool-call save /tmp/x.py')).toBe('save /tmp/x.py');
    expect(displayLangtag('shell')).toBe('shell');
    expect(displayLangtag('python')).toBe('python');
  });

  it('renders "tool-call shell" like a markdown-format shell block', () => {
    const html = codeBlockLabelHtml('tool-call shell');
    expect(html).toContain('<span class="codeblock-label-text">shell</span>');
    expect(html).not.toContain('tool-call');
    expect(iconForLangtag('tool-call shell')).toBe(iconForLangtag('shell'));
  });
});
