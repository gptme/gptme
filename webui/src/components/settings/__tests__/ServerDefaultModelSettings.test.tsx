import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ServerDefaultModelSettings } from '../ServerDefaultModelSettings';

const mockSuccess = jest.fn();
const mockError = jest.fn();
const mockFetch = jest.fn();
const mockOnSelect = jest.fn();
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
    defaultModel: 'anthropic/claude-sonnet-4-7',
    recommendedModels: ['anthropic/claude-sonnet-4-7'],
    isLoading: false,
    error: null,
  }),
}));

jest.mock('@/hooks/useUserSettings', () => ({
  useUserSettings: () => ({
    settings: {
      providers_configured: ['anthropic', 'openai'],
      provider_sources: {
        anthropic: {
          auth_source: 'ANTHROPIC_API_KEY',
          effective_source: 'config.local.toml',
        },
        openai: {
          auth_source: 'OPENAI_API_KEY',
          effective_source: 'config.toml',
        },
      },
      default_model: 'anthropic/claude-sonnet-4-7',
      default_model_source: 'config.toml',
      config_files: {
        config_path: '~/.config/gptme/config.toml',
        local_config_path: '~/.config/gptme/config.local.toml',
        local_config_exists: true,
        write_target: '~/.config/gptme/config.toml',
        local_overrides_main: true,
      },
    },
    isLoading: false,
    error: null,
    refetch: mockRefetch,
  }),
}));

jest.mock('@/components/ModelPicker', () => ({
  ModelPickerButton: ({
    value,
    onSelect,
    disabled,
  }: {
    value?: string;
    onSelect: (value: string) => void;
    disabled?: boolean;
  }) => (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        mockOnSelect();
        onSelect('openai/gpt-4.1');
      }}
    >
      {value || 'Select model'}
    </button>
  ),
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

jest.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockSuccess(...args),
    error: (...args: unknown[]) => mockError(...args),
  },
}));

describe('ServerDefaultModelSettings', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockSuccess.mockReset();
    mockError.mockReset();
    mockOnSelect.mockReset();
    mockRefetch.mockReset();
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  it('saves the selected default model through the server API', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ok',
        model: 'openai/gpt-4.1',
        restart_required: false,
      }),
    });

    render(<ServerDefaultModelSettings />);

    fireEvent.click(screen.getByRole('button', { name: /anthropic\/claude-sonnet-4-7/i }));
    fireEvent.click(screen.getByRole('button', { name: /save default model/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('http://127.0.0.1:5700/api/v2/user/default-model', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer test-token',
        },
        body: JSON.stringify({ model: 'openai/gpt-4.1' }),
      });
    });

    expect(mockOnSelect).toHaveBeenCalled();
    expect(mockSuccess).toHaveBeenCalledWith('Default model updated.');
    expect(mockRefetch).toHaveBeenCalled();
  });

  it('shows configured providers from user settings', () => {
    render(<ServerDefaultModelSettings />);

    expect(screen.getByText('anthropic')).toBeInTheDocument();
    expect(screen.getByText('openai')).toBeInTheDocument();
  });

  it('shows server-authoritative default model', () => {
    render(<ServerDefaultModelSettings />);

    expect(screen.getByText(/current default:/i).closest('p')).toHaveTextContent(
      'anthropic/claude-sonnet-4-7 from config.toml'
    );
  });
});
