import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigFileEditor } from '../ConfigFileEditor';

const mockSuccess = jest.fn();
const mockError = jest.fn();
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

jest.mock('@/components/ui/label', () => ({
  Label: ({
    children,
    ...props
  }: React.LabelHTMLAttributes<HTMLLabelElement> & { children: React.ReactNode }) => (
    <label {...props}>{children}</label>
  ),
}));

jest.mock('@/components/ui/textarea', () => ({
  Textarea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} />,
}));

jest.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

jest.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockSuccess(...args),
    error: (...args: unknown[]) => mockError(...args),
  },
}));

const configResponse = {
  content: '[env]\nMODEL = "old/model"\n',
  path: '~/.config/gptme/config.toml',
  write_target: '~/.config/gptme/config.toml',
  local_config_path: '~/.config/gptme/config.local.toml',
  local_config_exists: false,
  local_overrides_main: true,
};

describe('ConfigFileEditor', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockSuccess.mockReset();
    mockError.mockReset();
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  it('loads and saves the raw config file through the server API', async () => {
    const updatedContent = '[env]\nMODEL = "anthropic/claude-sonnet-4-7"\n';
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => configResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...configResponse,
          content: updatedContent,
          status: 'ok',
        }),
      });

    render(<ConfigFileEditor />);

    await waitFor(() => {
      expect(screen.getByLabelText('gptme config TOML')).toHaveValue(configResponse.content);
    });
    expect(mockFetch).toHaveBeenCalledWith('http://127.0.0.1:5700/api/v2/user/config-file', {
      headers: {
        Authorization: 'Bearer test-token',
      },
    });

    fireEvent.change(screen.getByLabelText('gptme config TOML'), {
      target: { value: updatedContent },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenLastCalledWith('http://127.0.0.1:5700/api/v2/user/config-file', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer test-token',
        },
        body: JSON.stringify({ content: updatedContent }),
      });
    });
    expect(mockSuccess).toHaveBeenCalledWith('Config file saved.');
    expect(screen.getByLabelText('gptme config TOML')).toHaveValue(updatedContent);
  });

  it('shows env-section warning when config contains [env]', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => configResponse, // configResponse.content contains [env]
    });

    render(<ConfigFileEditor />);

    await waitFor(() => {
      expect(screen.getByLabelText('gptme config TOML')).toHaveValue(configResponse.content);
    });
    expect(screen.getByText(/This config contains an/)).toBeInTheDocument();
  });

  it('does not show env-section warning when config has no [env] section', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...configResponse,
        content: '[model]\ndefault = "anthropic/claude-3-5-sonnet"\n',
      }),
    });

    render(<ConfigFileEditor />);

    await waitFor(() => {
      expect(screen.getByLabelText('gptme config TOML')).toBeInTheDocument();
    });
    expect(screen.queryByText(/This config contains an/)).not.toBeInTheDocument();
  });

  it('shows read-only runtime defaults separately from local overrides', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...configResponse,
        local_config_exists: true,
        runtime_config_path: '~/.config/gptme/config.runtime.toml',
        runtime_config_exists: true,
        runtime_is_defaults: true,
      }),
    });

    render(<ConfigFileEditor />);

    const notice = await screen.findByText(/Read-only runtime defaults are provided by/);
    expect(notice).toHaveTextContent('~/.config/gptme/config.runtime.toml');
    expect(notice).toHaveTextContent(
      'Values in config.toml and config.local.toml override these defaults.'
    );
    expect(notice).toHaveTextContent(
      'This editor saves only to ~/.config/gptme/config.toml, not the runtime file.'
    );
    expect(screen.getByText(/A local override config also exists/)).toBeInTheDocument();
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    expect(screen.getByLabelText('gptme config TOML')).toHaveValue(configResponse.content);
  });

  it.each([false, undefined])(
    'does not show runtime notice when runtime presence is %s',
    async (runtimeExists) => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...configResponse,
          runtime_config_exists: runtimeExists,
          runtime_is_defaults: runtimeExists === undefined ? undefined : true,
        }),
      });

      render(<ConfigFileEditor />);

      await waitFor(() => {
        expect(screen.getByLabelText('gptme config TOML')).toHaveValue(configResponse.content);
      });
      expect(screen.queryByText(/Read-only runtime defaults/)).not.toBeInTheDocument();
    }
  );
});
