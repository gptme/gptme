import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { DeleteConversationConfirmationDialog } from '../DeleteConversationConfirmationDialog';
import {
  conversations$ as mockConversations$,
  selectedConversation$ as mockSelectedConversation$,
} from '@/stores/conversations';

const mockDeleteConversation = jest.fn().mockResolvedValue(undefined);
const testConversations$ = mockConversations$ as unknown as {
  set: (value: Map<string, { data: object }>) => void;
};

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { deleteConversation: mockDeleteConversation },
    connectionConfig: { baseUrl: 'http://localhost:5700' },
  }),
}));

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}));

jest.mock('@/stores/conversations', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable } = require('@legendapp/state');
  return {
    conversations$: observable(new Map()),
    selectedConversation$: observable(''),
  };
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderDialog(
  conversationName = 'selected-chat',
  currentRoute = `/chat/${conversationName}`
) {
  const onDelete = jest.fn();
  render(
    <MemoryRouter initialEntries={[currentRoute]}>
      <LocationProbe />
      <DeleteConversationConfirmationDialog
        conversationName={conversationName}
        open={true}
        onOpenChange={jest.fn()}
        onDelete={onDelete}
      />
    </MemoryRouter>
  );
  return { onDelete };
}

describe('DeleteConversationConfirmationDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    testConversations$.set(
      new Map([
        ['selected-chat', { data: {} }],
        ['other-chat', { data: {} }],
      ])
    );
    mockSelectedConversation$.set('selected-chat');
  });

  it('redirects to the chat home after deleting the selected conversation', async () => {
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat$/));
    expect(mockDeleteConversation).toHaveBeenCalledWith('selected-chat');
    expect(mockSelectedConversation$.get()).toBe('');
  });

  it('keeps the current route and selection when deleting another conversation', async () => {
    renderDialog('other-chat', '/chat/selected-chat');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteConversation).toHaveBeenCalledWith('other-chat'));
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat\/selected-chat$/);
    expect(mockSelectedConversation$.get()).toBe('selected-chat');
  });
});
