import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { ServerDefaultModelSettings } from '../ServerDefaultModelSettings';

const mockSuccess = jest.fn();
const mockError = jest.fn();
const mockFetch = jest.fn();
const mockOnSelect = jest.fn();
const mockRefetch = jest.fn();
let mockDefaultModelSource = 'config.toml';
let mockApi = { baseUrl: 'http://127.0.0.1:5700', authHeader: 'Bearer test-token' };
const mockConnected = observable(true);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: mockApi,
    isConnected$: mockConnected,
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
      default_model_source: mockDefaultModelSource,
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
    mockDefaultModelSource = 'config.toml';
    mockApi = { baseUrl: 'http://127.0.0.1:5700', authHeader: 'Bearer test-token' };
    mockConnected.set(true);
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

  it('shows runtime defaults as the default model source', () => {
    mockDefaultModelSource = 'config.runtime.toml';

    render(<ServerDefaultModelSettings />);

    expect(screen.getByText(/current default:/i).closest('p')).toHaveTextContent(
      'anthropic/claude-sonnet-4-7 from config.runtime.toml'
    );
  });
});

describe('default-model draft ownership', () => {
  function selectDraft() {
    fireEvent.click(screen.getByRole('button', { name: /anthropic\/claude-sonnet-4-7/i }));
  }

  beforeEach(() => {
    mockFetch.mockReset();
    mockSuccess.mockReset();
    mockError.mockReset();
    mockRefetch.mockReset();
    mockDefaultModelSource = 'config.toml';
    mockApi = { baseUrl: 'http://127.0.0.1:5700', authHeader: 'Bearer test-token' };
    mockConnected.set(true);
    Object.defineProperty(window, 'fetch', { writable: true, value: mockFetch });
  });

  it.each(['url', 'token', 'disconnect'])('discards drafts on %s changes', (change) => {
    const { rerender } = render(<ServerDefaultModelSettings />);
    selectDraft();
    expect(screen.getByRole('button', { name: 'openai/gpt-4.1' })).toBeInTheDocument();
    act(() => {
      if (change === 'url') mockApi = { ...mockApi, baseUrl: 'http://server-b:5700' };
      if (change === 'token') mockApi = { ...mockApi, authHeader: 'Bearer other-user' };
      if (change === 'disconnect') mockConnected.set(false);
      rerender(<ServerDefaultModelSettings />);
    });
    expect(screen.queryByRole('button', { name: 'openai/gpt-4.1' })).not.toBeInTheDocument();
    if (change === 'disconnect') {
      expect(screen.getByRole('button', { name: /save default model/i })).toBeDisabled();
    }
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('keeps a draft when the same owner rerenders', () => {
    const { rerender } = render(<ServerDefaultModelSettings />);
    selectDraft();
    mockApi = { ...mockApi };
    rerender(<ServerDefaultModelSettings />);
    expect(screen.getByRole('button', { name: 'openai/gpt-4.1' })).toBeInTheDocument();
  });

  it.each(['resolve', 'reject'])('ignores old save %s after switching owner', async (outcome) => {
    let resolve!: (value: unknown) => void;
    let reject!: (reason: Error) => void;
    mockFetch.mockReturnValue(
      new Promise((res, rej) => {
        resolve = res;
        reject = rej;
      })
    );
    const { rerender } = render(<ServerDefaultModelSettings />);
    selectDraft();
    fireEvent.click(screen.getByRole('button', { name: /save default model/i }));
    mockApi = { ...mockApi, baseUrl: 'http://server-b:5700' };
    rerender(<ServerDefaultModelSettings />);
    selectDraft();
    await act(async () => {
      if (outcome === 'resolve')
        resolve({ ok: true, json: async () => ({ restart_required: false }) });
      else reject(new Error('old server failed'));
    });
    expect(mockSuccess).not.toHaveBeenCalled();
    expect(mockError).not.toHaveBeenCalled();
    expect(mockRefetch).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'openai/gpt-4.1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save default model/i })).toBeEnabled();
  });
});
