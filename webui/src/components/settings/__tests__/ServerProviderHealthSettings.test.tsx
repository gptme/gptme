import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ServerProviderHealthSettings } from '../ServerProviderHealthSettings';

const mockFetch = jest.fn();

type MockResponse = {
  ok: boolean;
  json: () => Promise<{
    providers: Record<string, { status: string; latency_ms: number | null; error: string | null }>;
  }>;
};

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });

  return { promise, resolve };
}

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
      expect(screen.getByText('10 ms')).toBeInTheDocument();
    });

    const refreshButton = screen.getByRole('button', { name: /refresh/i });
    expect(refreshButton).not.toBeDisabled();

    fireEvent.click(refreshButton);

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

  it('prevents duplicate forced refreshes while loading', async () => {
    const refreshResponse = deferred<MockResponse>();

    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          providers: {
            anthropic: { status: 'ok', latency_ms: 10, error: null },
          },
        }),
      })
      .mockReturnValueOnce(refreshResponse.promise);

    render(<ServerProviderHealthSettings />);

    await waitFor(() => {
      expect(screen.getByText('10 ms')).toBeInTheDocument();
    });

    const refreshButton = screen.getByRole('button', { name: /refresh/i });
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    expect(refreshButton).toBeDisabled();
    fireEvent.click(refreshButton);
    expect(mockFetch).toHaveBeenCalledTimes(2);

    await act(async () => {
      refreshResponse.resolve({
        ok: true,
        json: async () => ({
          providers: {
            anthropic: { status: 'ok', latency_ms: 12, error: null },
          },
        }),
      });
      await refreshResponse.promise;
    });

    await waitFor(() => {
      expect(screen.getByText('12 ms')).toBeInTheDocument();
    });
  });
});
