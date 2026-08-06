/**
 * Tests for InlineToolConfirmation — gptme/gptme#3440
 *
 * Verifies that the "Accept All" button is directly visible (one click, not buried
 * in a dropdown), and that action callbacks fire correctly.
 */
import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
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

  it('retains the lock when reconnect restores the same pending tool (same id)', async () => {
    // Regression guard for Greptile P1: "Reconnect releases confirmation lock".
    // When SSE reconnects and the backend restores the same pending tool with a
    // fresh object reference, the lock must NOT be released — otherwise a second
    // click can submit the already-confirmed tool before the backend clears it.
    const onConfirm = jest.fn().mockResolvedValue(undefined);
    const { pendingTool$ } = renderConfirmation({ onConfirm });

    fireEvent.click(screen.getByRole('button', { name: /execute/i }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));

    // Simulate reconnect restoring the same tool with a fresh object (same id = 'tool-1')
    act(() => {
      pendingTool$.set(makePendingTool());
    });

    // Lock must NOT be released — buttons still disabled
    expect(screen.getByRole('button', { name: /execute/i })).toBeDisabled();
  });

  it('releases the lock after 15 s when tool_executing SSE is missed (safety timeout)', async () => {
    // Regression guard for Greptile P1: "Missed SSE leaves controls locked".
    // If the POST succeeds but the tool_executing SSE is lost during a disconnect,
    // the lock must eventually release so the UI doesn't stay frozen indefinitely.
    jest.useFakeTimers();
    try {
      const onConfirm = jest.fn().mockResolvedValue(undefined);
      renderConfirmation({ onConfirm });

      act(() => {
        fireEvent.click(screen.getByRole('button', { name: /execute/i }));
      });

      // Flush the Promise microtask chain so onConfirm's await resolves
      await act(async () => {
        await Promise.resolve();
      });

      expect(onConfirm).toHaveBeenCalledTimes(1);
      // Lock is held — button disabled while waiting for SSE
      expect(screen.getByRole('button', { name: /execute/i })).toBeDisabled();

      // Advance past the 15 s safety timeout (pendingTool never cleared = SSE missed)
      act(() => {
        jest.advanceTimersByTime(15_000);
      });

      // After timeout: action buttons are replaced by a "Confirmed — waiting for server" banner
      // so the UI doesn't appear interactive-but-broken (Greptile P1: "Timeout leaves inert controls").
      expect(screen.queryByRole('button', { name: /execute/i })).not.toBeInTheDocument();
      expect(screen.getByText(/confirmed.*waiting for server/i)).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it('shows confirmed banner and blocks re-submission after timeout when SSE was missed (Greptile P1: Timeout leaves inert controls)', async () => {
    // After the 15 s timeout, the action buttons are replaced by a "Confirmed — waiting"
    // banner. This prevents the card from looking interactive when clicks would silently
    // no-op (confirmedToolId guard). No second POST is possible.
    jest.useFakeTimers();
    try {
      const onConfirm = jest.fn().mockResolvedValue(undefined);
      renderConfirmation({ onConfirm });

      act(() => {
        fireEvent.click(screen.getByRole('button', { name: /execute/i }));
      });

      // Flush Promise microtasks so onConfirm's await resolves and the timeout is scheduled
      await act(async () => {
        await Promise.resolve();
      });

      expect(onConfirm).toHaveBeenCalledTimes(1);

      // Advance past the safety timeout (pendingTool never cleared = SSE missed)
      act(() => {
        jest.advanceTimersByTime(15_000);
      });

      // Action buttons replaced by banner — no interactive-but-broken state
      expect(screen.queryByRole('button', { name: /execute/i })).not.toBeInTheDocument();
      expect(screen.getByText(/confirmed.*waiting for server/i)).toBeInTheDocument();

      // No second POST possible — buttons are gone
      expect(onConfirm).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
    }
  });
});
