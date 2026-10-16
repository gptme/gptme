import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { InlineToolExecution } from '../InlineToolExecution';
import type { ExecutingTool } from '@/stores/conversations';

function renderExecution(args: ExecutingTool['tooluse']['args']) {
  const executingTool$ = observable<ExecutingTool | null>({
    id: 'tool-1',
    tooluse: { tool: 'shell', args, content: 'regression-content' },
    startedAt: Date.now(),
    partialOutput: 'regression-output',
  });
  render(<InlineToolExecution executingTool$={executingTool$} />);
}

describe('InlineToolExecution — nullable arguments', () => {
  it.each([null, []])('renders content and output with args=%j', (args) => {
    renderExecution(args);

    expect(screen.getByText('regression-content')).toBeVisible();
    expect(screen.getByText('regression-output')).toBeVisible();
    expect(screen.queryByText('Arguments:')).not.toBeInTheDocument();
  });

  it('keeps non-empty arguments, content, and output visible', () => {
    renderExecution(['--verbose']);

    expect(screen.getByText('Arguments:')).toBeVisible();
    expect(screen.getByText('--verbose')).toBeVisible();
    expect(screen.getByText('regression-content')).toBeVisible();
    expect(screen.getByText('regression-output')).toBeVisible();
  });
});
