/**
 * Tests for InlineToolConfirmation — gptme/gptme#3440
 *
 * Verifies that the "Accept All" button is directly visible (one click, not buried
 * in a dropdown), and that action callbacks fire correctly.
 */
import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { InlineToolConfirmation } from '../InlineToolConfirmation';
import type { PendingTool } from '@/stores/conversations';

function makePendingTool(overrides: Partial<PendingTool['tooluse']> = {}): PendingTool {
  return {
    id: 'tool-1',
    tooluse: {
      tool: 'shell',
      args: [],
      content: 'ls -la',
      ...overrides,
    },
  };
}

function renderConfirmation({
  pendingTool = makePendingTool(),
  onConfirm = jest.fn().mockResolvedValue(undefined),
  onEdit = jest.fn().mockResolvedValue(undefined),
  onSkip = jest.fn().mockResolvedValue(undefined),
  onAuto = jest.fn().mockResolvedValue(undefined),
}: {
  pendingTool?: PendingTool | null;
  onConfirm?: jest.Mock;
  onEdit?: jest.Mock;
  onSkip?: jest.Mock;
  onAuto?: jest.Mock;
} = {}) {
  const pendingTool$ = observable<PendingTool | null>(pendingTool);
  render(
    <InlineToolConfirmation
      pendingTool$={pendingTool$}
      onConfirm={onConfirm}
      onEdit={onEdit}
      onSkip={onSkip}
      onAuto={onAuto}
    />
  );
  return { pendingTool$, onConfirm, onEdit, onSkip, onAuto };
}

describe('InlineToolConfirmation — Accept All UX (gptme#3440)', () => {
  it('renders nothing when pendingTool$ is null', () => {
    renderConfirmation({ pendingTool: null });
    expect(screen.queryByText(/accept all/i)).toBeNull();
    expect(screen.queryByText(/execute/i)).toBeNull();
  });

  it('shows the tool name in the header', () => {
    renderConfirmation();
    expect(screen.getByText(/shell/i)).toBeInTheDocument();
  });

  it('shows "Accept All" as a directly visible button (not buried in dropdown)', () => {
    // The regression: "accept all" was hidden behind a ChevronDown dropdown,
    // requiring 2 clicks. It must now be a first-class visible button.
    renderConfirmation();
    const acceptAllBtn = screen.getByRole('button', { name: /accept all/i });
    expect(acceptAllBtn).toBeInTheDocument();
    // It must be visible — not inside a collapsed dropdown
    expect(acceptAllBtn).toBeVisible();
  });

  it('calls onAuto(999999) when "Accept All" is clicked', async () => {
    const onAuto = jest.fn().mockResolvedValue(undefined);
    renderConfirmation({ onAuto });

    fireEvent.click(screen.getByRole('button', { name: /accept all/i }));

    await waitFor(() => {
      expect(onAuto).toHaveBeenCalledWith(999999);
    });
  });

  it('calls onConfirm when "Execute" is clicked', async () => {
    const onConfirm = jest.fn().mockResolvedValue(undefined);
    renderConfirmation({ onConfirm });

    fireEvent.click(screen.getByRole('button', { name: /execute/i }));

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalled();
    });
  });

  it('calls onSkip when "Skip" is clicked', async () => {
    const onSkip = jest.fn().mockResolvedValue(undefined);
    renderConfirmation({ onSkip });

    fireEvent.click(screen.getByRole('button', { name: /skip/i }));

    await waitFor(() => {
      expect(onSkip).toHaveBeenCalled();
    });
  });

  it('"Accept All" button is absent in edit mode (while editing tool content)', () => {
    renderConfirmation();

    // Switch to edit mode
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));

    // In edit mode the Accept All button should disappear (saving & executing has
    // different semantics — accepting all after an edit would be confusing)
    expect(screen.queryByRole('button', { name: /accept all/i })).toBeNull();
    // The primary button changes to "Save & Execute"
    expect(screen.getByRole('button', { name: /save & execute/i })).toBeInTheDocument();
  });

  it('calls onEdit with edited content when "Save & Execute" is clicked', async () => {
    const onEdit = jest.fn().mockResolvedValue(undefined);
    renderConfirmation({ onEdit });

    // Enter edit mode
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));

    // Edit the content
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'echo hello' } });

    fireEvent.click(screen.getByRole('button', { name: /save & execute/i }));

    await waitFor(() => {
      expect(onEdit).toHaveBeenCalledWith('echo hello');
    });
  });

  it('shows the hint that Enter key executes the tool', () => {
    renderConfirmation();
    expect(screen.getByText(/press enter to execute/i)).toBeInTheDocument();
  });

  it('prevents duplicate submissions between POST resolve and pendingTool SSE clear', async () => {
    // Regression guard for Greptile P1: "Confirmation lock releases too early".
    // The POST may resolve before the SSE event clears pendingTool. Without the
    // fix, a second click in that window would submit the already-confirmed tool.
    const onAuto = jest.fn().mockResolvedValue(undefined);
    renderConfirmation({ onAuto });

    const acceptAllBtn = screen.getByRole('button', { name: /accept all/i });
    fireEvent.click(acceptAllBtn);
    await waitFor(() => expect(onAuto).toHaveBeenCalledTimes(1));

    // Second click before pendingTool clears (SSE hasn't fired yet) — must be ignored
    fireEvent.click(acceptAllBtn);
    expect(onAuto).toHaveBeenCalledTimes(1);
  });
});
