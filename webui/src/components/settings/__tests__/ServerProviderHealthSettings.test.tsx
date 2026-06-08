import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ServerProviderHealthSettings } from '../ServerProviderHealthSettings';

const mockFetch = jest.fn();

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      baseUrl: 'http://127.0.0.1:5700',
      authHeader: 'Bearer test-token',
    },
  }),
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

describe('ServerProviderHealthSettings', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  it('loads and renders provider health from the server API', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        providers: {
          anthropic: { status: 'ok', latency_ms: 42, error: null },
          openai: { status: 'error', latency_ms: 17, error: 'bad key' },
        },
      }),
    });

    render(<ServerProviderHealthSettings />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('http://127.0.0.1:5700/api/v2/providers/health', {
        headers: {
          Authorization: 'Bearer test-token',
        },
      });
    });

    await waitFor(() => {
      expect(screen.getByText('anthropic')).toBeInTheDocument();
    });
    expect(screen.getByText('Reachable')).toBeInTheDocument();
    expect(screen.getByText('42 ms')).toBeInTheDocument();
    expect(screen.getByText('openai')).toBeInTheDocument();
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.getByText('bad key')).toBeInTheDocument();
  });

  it('forces a refresh when the button is clicked', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          providers: {
            anthropic: { status: 'ok', latency_ms: 10, error: null },
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          providers: {
            anthropic: { status: 'ok', latency_ms: 12, error: null },
          },
        }),
      });

    render(<ServerProviderHealthSettings />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenLastCalledWith(
        'http://127.0.0.1:5700/api/v2/providers/health?force=1',
        {
          headers: {
            Authorization: 'Bearer test-token',
          },
        }
      );
    });
  });
});
