import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { SplitConversationView } from '../SplitConversationView';

// Stub ConversationContent so tests don't need full API context
jest.mock('../ConversationContent', () => ({
  ConversationContent: ({
    conversationId,
    isReadOnly,
  }: {
    conversationId: string;
    isReadOnly?: boolean;
  }) => (
    <div data-testid={`conversation-${conversationId}`} data-readonly={String(!!isReadOnly)}>
      {conversationId}
    </div>
  ),
}));

// Stub resizable panels to simple divs
jest.mock('@/components/ui/resizable', () => ({
  ResizablePanelGroup: ({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }) => (
    <div data-testid="resizable-group" className={className}>
      {children}
    </div>
  ),
  ResizablePanel: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="resizable-panel">{children}</div>
  ),
  ResizableHandle: () => <div data-testid="resizable-handle" />,
}));

// Stub Popover + Command so the conversation selector renders in tests
jest.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popover">{children}</div>
  ),
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popover-trigger">{children}</div>
  ),
  PopoverContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popover-content">{children}</div>
  ),
}));

jest.mock('@/components/ui/command', () => ({
  Command: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="command">{children}</div>
  ),
  CommandEmpty: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="command-empty">{children}</div>
  ),
  CommandGroup: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="command-group">{children}</div>
  ),
  CommandInput: () => (
    <div data-testid="command-input" />
  ),
  CommandItem: ({
    children,
    onSelect,
  }: {
    children: React.ReactNode;
    onSelect?: () => void;
  }) => (
    <div data-testid="command-item" onClick={onSelect}>
      {children}
    </div>
  ),
  CommandList: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="command-list">{children}</div>
  ),
}));

describe('SplitConversationView', () => {
  const onClose = jest.fn();
  const onNavigateLeft = jest.fn();
  const onNavigateRight = jest.fn();
  const mockConversations = [
    { id: 'conv-a', name: 'Conversation A', modified: 1000, messages: 5, workspace: '.' },
    { id: 'conv-b', name: 'Conversation B', modified: 2000, messages: 3, workspace: '.' },
    { id: 'conv-c', name: 'Conversation C', modified: 3000, messages: 8, workspace: '.' },
  ];

  beforeEach(() => {
    onClose.mockClear();
    onNavigateLeft.mockClear();
    onNavigateRight.mockClear();
  });

  it('renders both conversation panes', () => {
    render(<SplitConversationView leftId="conv-a" rightId="conv-b" onClose={onClose} />);

    expect(screen.getByTestId('conversation-conv-a')).toBeInTheDocument();
    expect(screen.getByTestId('conversation-conv-b')).toBeInTheDocument();
  });

  it('shows "Split view" label', () => {
    render(<SplitConversationView leftId="conv-a" rightId="conv-b" onClose={onClose} />);

    expect(screen.getByText('Split view')).toBeInTheDocument();
  });

  it('forwards readonly state to each conversation pane', () => {
    render(
      <SplitConversationView
        leftId="conv-a"
        rightId="conv-b"
        leftIsReadOnly
        rightIsReadOnly={false}
        onClose={onClose}
      />
    );

    expect(screen.getByTestId('conversation-conv-a')).toHaveAttribute('data-readonly', 'true');
    expect(screen.getByTestId('conversation-conv-b')).toHaveAttribute('data-readonly', 'false');
  });

  it('calls onClose when close button is clicked', () => {
    render(<SplitConversationView leftId="conv-a" rightId="conv-b" onClose={onClose} />);

    fireEvent.click(screen.getByTitle('Close split view'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps close button available while loading conversations', () => {
    render(<SplitConversationView leftId="conv-a" rightId="conv-b" isLoading onClose={onClose} />);

    expect(screen.getByText('Loading split view...')).toBeInTheDocument();
    expect(screen.queryByTestId('conversation-conv-a')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Close split view'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('uses vertical layout on mobile', () => {
    render(<SplitConversationView leftId="a" rightId="b" vertical onClose={onClose} />);

    expect(screen.getByTestId('conversation-a')).toBeInTheDocument();
    expect(screen.getByTestId('conversation-b')).toBeInTheDocument();
  });

  it('renders conversation selectors for both panes', () => {
    render(
      <SplitConversationView
        leftId="conv-a"
        rightId="conv-b"
        allConversations={mockConversations}
        onClose={onClose}
      />
    );

    const triggers = screen.getAllByTestId('popover-trigger');
    expect(triggers).toHaveLength(2);
  });

  it('renders all conversations in the selector lists', () => {
    render(
      <SplitConversationView
        leftId="conv-a"
        rightId="conv-b"
        allConversations={mockConversations}
        onClose={onClose}
      />
    );

    const items = screen.getAllByTestId('command-item');
    // Both selectors render all 3 conversations, so 6 items total
    expect(items).toHaveLength(6);
  });

  it('calls navigation callbacks when selecting from conversation selectors', () => {
    render(
      <SplitConversationView
        leftId="conv-a"
        rightId="conv-b"
        allConversations={mockConversations}
        onClose={onClose}
        onNavigateLeft={onNavigateLeft}
        onNavigateRight={onNavigateRight}
      />
    );

    const items = screen.getAllByTestId('command-item');
    // Click one item from left selector (first 3 items)
    fireEvent.click(items[0]);
    expect(onNavigateLeft).toHaveBeenCalledTimes(1);
    expect(onNavigateRight).not.toHaveBeenCalled();

    // Click one item from right selector (last 3 items)
    onNavigateLeft.mockClear();
    fireEvent.click(items[3]);
    expect(onNavigateRight).toHaveBeenCalledTimes(1);
    expect(onNavigateLeft).not.toHaveBeenCalled();
  });
});
