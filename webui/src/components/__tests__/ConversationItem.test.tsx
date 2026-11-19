import { act, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ConversationItem, type ConversationItemProps } from '../ConversationItem';
import { TooltipProvider } from '@/components/ui/tooltip';
import { conversations$, initConversation, setPendingTool } from '@/stores/conversations';

const conversationId = 'pending-tool-icon-test';

function renderItem(serverId?: string) {
  const props: ConversationItemProps = {
    conv: {
      id: conversationId,
      name: 'Pending tool test',
      modified: Date.now() / 1000,
      readonly: false,
      serverId,
    },
    onSelect: jest.fn(),
    renamingId: null,
    renameValue: '',
    setRenameValue: jest.fn(),
    onRenameSubmit: jest.fn().mockResolvedValue(undefined),
    onRenameCancel: jest.fn(),
    setDeleteTarget: jest.fn(),
    getIsStarred: () => false,
    toggleStar: jest.fn().mockResolvedValue(true),
    onExportMarkdown: jest.fn(),
    onExportJSON: jest.fn(),
    onStartRename: jest.fn(),
    demoIds: new Set(),
    normalizedFilter: '',
    setOptimisticStars: jest.fn(),
  };
  return render(
    <TooltipProvider>
      <ConversationItem {...props} />
    </TooltipProvider>
  );
}

beforeEach(() => {
  conversations$.set(new Map());
  initConversation(conversationId);
});

afterEach(() => {
  conversations$.set(new Map());
});

it('reactively shows an accessible SVG icon while a tool awaits confirmation', () => {
  const { container } = renderItem();
  expect(screen.queryByLabelText(/Pending tool:/)).not.toBeInTheDocument();

  act(() => {
    setPendingTool(conversationId, 'tool-1', { tool: 'shell', args: [], content: 'pwd' });
  });

  const indicator = screen.getByLabelText('Pending tool: shell');
  const icon = indicator.querySelector('svg');
  expect(icon).toHaveClass('lucide-wrench', 'h-3', 'w-3');
  expect(icon).toHaveAttribute('aria-hidden', 'true');
  expect(container).not.toHaveTextContent('⚙');

  // The existing hover/focus tooltip still names the tool.
  act(() => indicator.focus());
  expect(within(screen.getByRole('tooltip')).getByText('Pending tool: shell')).toBeInTheDocument();

  act(() => {
    setPendingTool(conversationId, null, null);
  });
  expect(screen.queryByLabelText(/Pending tool:/)).not.toBeInTheDocument();
});

it('does not show a pending tool from a same-ID conversation on another server', () => {
  setPendingTool(conversationId, 'tool-1', { tool: 'shell', args: [], content: 'pwd' });
  renderItem('another-server');
  expect(screen.queryByLabelText(/Pending tool:/)).not.toBeInTheDocument();
});
