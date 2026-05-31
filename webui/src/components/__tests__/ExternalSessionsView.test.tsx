import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { ExternalSessionsView } from '../ExternalSessionsView';

const mockUseQuery = jest.fn();

jest.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => mockUseQuery(...args),
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

  it('labels the search field and detail close button', () => {
    render(<ExternalSessionsView />);

    expect(screen.getByRole('textbox', { name: 'Search external sessions' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Imported session/i }));

    expect(screen.getByRole('button', { name: 'Close session details' })).toBeInTheDocument();
  });
});
