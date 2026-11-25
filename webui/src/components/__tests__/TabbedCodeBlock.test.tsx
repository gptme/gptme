import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { TabbedCodeBlock } from '../TabbedCodeBlock';

const HTML = '<script>fetch("https://attacker.example/")</script><h1>Hi</h1>';

const block = (codeText: string) => (
  <TabbedCodeBlock language="html" codeText={codeText} code={document.createElement('code')} />
);

const frame = () => screen.getByTitle('HTML Preview');
const runButton = () => screen.queryByRole('button', { name: /run scripts/i });

describe('TabbedCodeBlock HTML preview', () => {
  it('mounts no iframe until the Preview tab is opened', () => {
    render(block(HTML));

    expect(screen.queryByTitle('HTML Preview')).not.toBeInTheDocument();
  });

  it('opens as a static preview with an empty sandbox', () => {
    render(block(HTML));
    fireEvent.click(screen.getByRole('button', { name: /preview/i }));

    expect(frame().getAttribute('sandbox')).toBe('');
    expect(frame().getAttribute('srcdoc')).toContain('Content-Security-Policy');
  });

  it('runs scripts only after the explicit click, and asks again for new content', () => {
    const { rerender } = render(block(HTML));
    fireEvent.click(screen.getByRole('button', { name: /preview/i }));
    fireEvent.click(runButton()!);

    expect(frame().getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame().getAttribute('srcdoc')).toBe(HTML);
    expect(runButton()).not.toBeInTheDocument();

    rerender(block(`${HTML}<p>changed</p>`));

    expect(frame().getAttribute('sandbox')).toBe('');
    expect(runButton()).toBeInTheDocument();
  });
});
