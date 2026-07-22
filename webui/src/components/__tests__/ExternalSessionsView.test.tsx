import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ExternalSessionsView } from '../ExternalSessionsView';

const renderInRouter = (initialEntry = '/external-sessions') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ExternalSessionsView />
    </MemoryRouter>
  );

const mockUseQuery = jest.fn();

jest.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => mockUseQuery(...args),
  useMutation: jest.fn(() => ({
    mutate: jest.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  })),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: jest.fn(() => true),
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      isConnected$: {},
      getExternalSessions: jest.fn(),
      getExternalSession: jest.fn(),
    },
  }),
}));

describe('ExternalSessionsView', () => {
  beforeEach(() => {
    mockUseQuery.mockReset();
    mockUseQuery.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => {
      if (queryKey[0] === 'external-sessions') {
        return {
          data: [
            {
              id: 'session-1',
              session_id: 'session-1',
              harness: 'codex',
              session_name: 'Imported session',
              project: 'bob',
              model: 'gpt-5.4',
              started_at: '2026-05-31T12:00:00Z',
              last_activity: '2026-05-31T12:05:00Z',
              capabilities: ['shell'],
              trajectory_path: '/tmp/trajectory.jsonl',
            },
          ],
          isLoading: false,
          error: null,
        };
      }

      return {
        data: { transcript: { hello: 'world' } },
        isLoading: false,
        error: null,
      };
    });
  });

  it('labels the search field and detail close button', async () => {
    const user = userEvent.setup();
    renderInRouter();

    expect(screen.getByRole('textbox', { name: 'Search external sessions' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Imported session/i }));

    expect(screen.getByRole('button', { name: 'Close session details' })).toBeInTheDocument();
  });

  it('pre-selects a session when ?selected param is present in URL', async () => {
    renderInRouter('/external-sessions?selected=session-1');

    // Detail panel should open immediately without a click
    expect(
      await screen.findByRole('button', { name: 'Close session details' })
    ).toBeInTheDocument();
  });

  it('renders normalized transcript messages with a collapsed system prelude', async () => {
    mockUseQuery.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => {
      if (queryKey[0] === 'external-sessions') {
        return {
          data: [
            {
              id: 'session-1',
              session_id: 'session-1',
              harness: 'codex',
              session_name: 'Imported session',
              project: 'bob',
              model: 'gpt-5.4',
              started_at: '2026-05-31T12:00:00Z',
              last_activity: '2026-05-31T12:05:00Z',
              capabilities: ['shell'],
              trajectory_path: '/tmp/trajectory.jsonl',
            },
          ],
          isLoading: false,
          error: null,
        };
      }

      return {
        data: {
          transcript: {
            messages: [
              { role: 'system', content: 'system prompt' },
              { role: 'user', content: 'do the thing' },
              {
                role: 'assistant',
                content: 'running it',
                tool_name: 'shell',
                tool_input: { command: 'ls' },
              },
              { role: 'tool_result', content: '', tool_result: 'boom', is_error: true },
            ],
          },
        },
        isLoading: false,
        error: null,
      };
    });

    const user = userEvent.setup();
    renderInRouter();
    await user.click(screen.getByRole('button', { name: /Imported session/i }));

    expect(screen.getByRole('button', { name: /Show 1 system message/i })).toBeInTheDocument();
    expect(screen.getByText('do the thing')).toBeInTheDocument();
    expect(screen.getByText('running it')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
    expect(screen.getByText('error')).toBeInTheDocument();
  });
});
