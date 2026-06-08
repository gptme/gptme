import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { SplitConversationView } from '../SplitConversationView';

// Stub ConversationContent so tests don't need full API context
jest.mock('../ConversationContent', () => ({
  ConversationContent: ({ conversationId }: { conversationId: string }) => (
    <div data-testid={`conversation-${conversationId}`}>{conversationId}</div>
  ),
}));

// Stub resizable panels to simple divs
jest.mock('@/components/ui/resizable', () => ({
  ResizablePanelGroup: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div data-testid="resizable-group" className={className}>{children}</div>
  ),
  ResizablePanel: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="resizable-panel">{children}</div>
  ),
  ResizableHandle: () => <div data-testid="resizable-handle" />,
}));

describe('SplitConversationView', () => {
  const onClose = jest.fn();

  beforeEach(() => {
    onClose.mockClear();
  });

  it('renders both conversation panes', () => {
    render(
      <SplitConversationView leftId="conv-a" rightId="conv-b" onClose={onClose} />
    );

    expect(screen.getByTestId('conversation-conv-a')).toBeInTheDocument();
    expect(screen.getByTestId('conversation-conv-b')).toBeInTheDocument();
  });

  it('shows "Split view" label', () => {
    render(
      <SplitConversationView leftId="conv-a" rightId="conv-b" onClose={onClose} />
    );

    expect(screen.getByText('Split view')).toBeInTheDocument();
  });

  it('calls onClose when close button is clicked', () => {
    render(
      <SplitConversationView leftId="conv-a" rightId="conv-b" onClose={onClose} />
    );

    fireEvent.click(screen.getByTitle('Close split view'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('uses vertical layout on mobile', () => {
    render(
      <SplitConversationView leftId="a" rightId="b" vertical onClose={onClose} />
    );

    // Both panes still rendered
    expect(screen.getByTestId('conversation-a')).toBeInTheDocument();
    expect(screen.getByTestId('conversation-b')).toBeInTheDocument();
  });
});
