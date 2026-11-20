import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import CreateAgentDialog from '../CreateAgentDialog';
import { selectedAgent$ } from '@/stores/sidebar';
import { matchesAgent } from '@/utils/workspaceUtils';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

function Location() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

describe('CreateAgentDialog server ownership', () => {
  afterEach(() => selectedAgent$.set(null));

  it.each(['creation-server', undefined])(
    'keeps the new agent and its chat scoped to the creation server (%s)',
    async (serverId) => {
      const onAgentCreated = jest.fn().mockResolvedValue({
        status: 'success',
        message: 'Created',
        initial_conversation_id: 'new-chat',
        agent_path: '/agents/alice',
      });
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={['/agents']}>
            <CreateAgentDialog
              open
              serverId={serverId}
              onOpenChange={jest.fn()}
              onAgentCreated={onAgentCreated}
            />
            <Location />
          </MemoryRouter>
        </QueryClientProvider>
      );
      fireEvent.change(screen.getByLabelText('Agent Name *'), { target: { value: 'Alice' } });
      fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }));

      await waitFor(() => expect(onAgentCreated).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(screen.getByTestId('location')).toHaveTextContent(
          `/chat/new-chat${serverId ? '?server=creation-server' : ''}`
        )
      );
      const agent = selectedAgent$.get()!;
      expect(agent).toMatchObject({ path: '/agents/alice', serverId });
      const conversation = {
        id: 'new-chat',
        name: 'Alice chat',
        modified: 100,
        agent_path: '/agents/alice',
        serverId,
      };
      expect(matchesAgent(conversation, agent)).toBe(true);
      expect(matchesAgent({ ...conversation, serverId: 'other-server' }, agent)).toBe(false);
    }
  );
});
