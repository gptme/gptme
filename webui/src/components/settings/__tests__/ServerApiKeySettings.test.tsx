import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ServerApiKeySettings } from '../ServerApiKeySettings';

const mockSuccess = jest.fn();
const mockError = jest.fn();
const mockFetch = jest.fn();
const mockRefetch = jest.fn();

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      baseUrl: 'http://127.0.0.1:5700',
      authHeader: 'Bearer test-token',
    },
  }),
}));

jest.mock('@/hooks/useModels', () => ({
  useModels: () => ({
    models: [
      {
        id: 'anthropic/claude-sonnet-4-7',
        provider: 'anthropic',
        model: 'claude-sonnet-4-7',
      },
      {
        id: 'openai/gpt-4.1',
        provider: 'openai',
        model: 'gpt-4.1',
      },
    ],
    recommendedModels: ['anthropic/claude-sonnet-4-7'],
    isLoading: false,
    error: null,
  }),
}));

jest.mock('@/hooks/useUserSettings', () => ({
  useUserSettings: () => ({
    settings: {
      providers_configured: ['anthropic'],
      default_model: 'anthropic/claude-sonnet-4-7',
    },
    isLoading: false,
    error: null,
    refetch: mockRefetch,
  }),
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

jest.mock('@/components/ui/input', () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

jest.mock('@/components/ui/label', () => ({
  Label: ({
    children,
    ...props
  }: React.LabelHTMLAttributes<HTMLLabelElement> & { children: React.ReactNode }) => (
    <label {...props}>{children}</label>
  ),
}));

jest.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockSuccess(...args),
    error: (...args: unknown[]) => mockError(...args),
  },
}));

describe('ServerApiKeySettings', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockSuccess.mockReset();
    mockError.mockReset();
    mockRefetch.mockReset();
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  it('saves provider API keys through the server API', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ok',
        provider: 'anthropic',
        env_var: 'ANTHROPIC_API_KEY',
        restart_required: true,
      }),
    });

    render(<ServerApiKeySettings />);

    fireEvent.change(screen.getByLabelText(/api key/i), {
      target: { value: '  sk-ant-test-key  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save api key/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('http://127.0.0.1:5700/api/v2/user/api-key', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer test-token',
        },
        body: JSON.stringify({
          provider: 'anthropic',
          api_key: 'sk-ant-test-key',
          model: 'anthropic/claude-sonnet-4-7',
        }),
      });
    });

    expect(mockSuccess).toHaveBeenCalledWith('API key saved. Restart the server to apply it.');
    expect(mockRefetch).toHaveBeenCalled();
  });
});
