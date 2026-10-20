import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SetupWizard } from '../SetupWizard';
import { SettingsProvider } from '@/contexts/SettingsContext';
import { ChatInput } from '../ChatInput';
import { setupWizard$ } from '@/stores/setupWizard';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useModels } from '@/hooks/useModels';

const mockFetch = jest.fn();
const mockInvokeTauri = jest.fn();
const mockIsConnected$ = observable(true);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { baseUrl: 'http://127.0.0.1:5700', authHeader: null },
    isConnected$: mockIsConnected$,
    connect: jest.fn(),
    connectionConfig: {
      baseUrl: 'http://127.0.0.1:5700',
      authToken: null,
      useAuthToken: false,
    },
  }),
}));
jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => true,
  invokeTauri: (...args: unknown[]) => mockInvokeTauri(...args),
}));
jest.mock('@/hooks/useTauriServerStatus', () => ({
  useTauriServerStatus: () => ({
    isLoading: false,
    managesLocalServer: true,
    serverStatus: { running: true, port: 5700, port_available: false, manages_local_server: true },
  }),
}));
jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

jest.mock('@/stores/sidebar', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    selectedAgent$: observable(null),
    selectedWorkspace$: observable(null),
    rightSidebarVisible$: observable(false),
    rightSidebarActiveTab$: observable(null),
  };
});
jest.mock('@/stores/conversations', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  const conversation = observable({
    isGenerating: false,
    executingTool: null,
    chatConfig: { chat: { model: 'openai/gpt-4o' } },
  });
  return {
    conversations$: { get: () => conversation },
    setMaxTokens: jest.fn(),
    setTemperature: jest.fn(),
    setTopP: jest.fn(),
  };
});
jest.mock('@/hooks/useWorkspaces', () => ({
  useWorkspaces: () => ({ workspaces: [], addCustomWorkspace: jest.fn() }),
}));
jest.mock('@/hooks/useAgents', () => ({
  useAgents: () => ({ agents: [], isLoading: false, error: null }),
}));
jest.mock('@/hooks/useFileAutocomplete', () => ({
  useFileAutocomplete: () => ({
    state: { files: [], selectedIndex: -1, isOpen: false, query: '' },
    handleInputChange: jest.fn(),
    handleKeyDown: () => false,
    selectFile: jest.fn(),
    setSelectedIndex: jest.fn(),
  }),
}));
jest.mock('@/components/SpeechInputButton', () => ({ SpeechInputButton: () => null }));
jest.mock('@/components/WorkspaceSelector', () => ({ WorkspaceSelector: () => null }));
jest.mock('@/components/FileAutocomplete', () => ({ FileAutocomplete: () => null }));
jest.mock('@/components/ModelPicker', () => ({
  ModelPicker: ({ onSelect }: { onSelect: (model: string) => void }) => (
    <button onClick={() => onSelect('openai/gpt-4.1')}>Choose explicit model</button>
  ),
  ModelPickerField: () => null,
}));

it.each(['untouched', 'explicit choice', 'existing conversation'])(
  'refreshes setup models while preserving %s composer ownership',
  async (mode) => {
    mockFetch.mockClear();
    window.localStorage.clear();
    setupWizard$.set({ open: false, step: 'welcome', providerStatusVersion: 0 });
    let configured = false;
    const model = 'openrouter/deepseek/deepseek-v4.1-flash';
    mockInvokeTauri.mockResolvedValue(undefined);
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/api/v2/models')) {
        return {
          ok: true,
          json: async () => ({
            models: [{ id: model, provider: 'openrouter', model: 'deepseek/deepseek-v4.1-flash' }],
            default: configured ? model : null,
            recommended: [model],
          }),
        };
      }
      if (url.endsWith('/api/v2/user/api-key')) {
        expect(JSON.parse(init!.body as string)).toMatchObject({ provider: 'openrouter', model });
        configured = true;
        return { ok: true, json: async () => ({ status: 'ok' }) };
      }
      return { ok: true, json: async () => ({ provider_configured: configured }) };
    });
    Object.defineProperty(window, 'fetch', { writable: true, value: mockFetch });
    render(
      <SettingsProvider>
        <TooltipProvider>
          <ChatInput
            conversationId={mode === 'existing conversation' ? 'existing' : undefined}
            onSend={jest.fn()}
            autoFocus$={observable(false)}
          />
          <SetupWizard />
        </TooltipProvider>
      </SettingsProvider>
    );
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(
        'http://127.0.0.1:5700/api/v2/models',
        expect.anything()
      )
    );
    await act(async () => {});
    // Keep the composer mounted while setup runs; the connection stays true.
    if (mode === 'explicit choice') {
      fireEvent.click(screen.getByRole('button', { name: /skip for now/i }));
      fireEvent.click(screen.getByTestId('model-selector'));
      fireEvent.click(screen.getByRole('button', { name: /choose explicit model/i }));
      expect(screen.getByTestId('model-selector')).toHaveTextContent('gpt-4.1');
    }
    act(() => {
      setupWizard$.step.set('provider');
      setupWizard$.open.set(true);
    });
    await screen.findByRole('heading', { name: /configure a provider/i });
    fireEvent.change(screen.getByLabelText(/^provider$/i), { target: { value: 'openrouter' } });
    await waitFor(() => expect(screen.getByLabelText(/default model/i)).toHaveValue(model));
    fireEvent.change(screen.getByLabelText(/^api key$/i), { target: { value: 'sk-or-synthetic' } });
    fireEvent.click(screen.getByRole('button', { name: /save and restart server/i }));
    await screen.findByRole('heading', { name: /you're all set/i });
    expect(mockIsConnected$.get()).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /start using gptme/i }));
    const expected =
      mode === 'untouched'
        ? 'deepseek-v4.1-flash'
        : mode === 'explicit choice'
          ? 'gpt-4.1'
          : 'gpt-4o';
    await waitFor(() => expect(screen.getByTestId('model-selector')).toHaveTextContent(expected));
  }
);

it.each(['resolve', 'reject'] as const)(
  'ignores an old model request that settles via %s after setup refresh',
  async (settlement) => {
    setupWizard$.providerStatusVersion.set(0);
    mockFetch.mockReset();
    let resolveOld!: (value: unknown) => void;
    let rejectOld!: (reason: Error) => void;
    const oldRequest = new Promise((resolve, reject) => {
      resolveOld = resolve;
      rejectOld = reject;
    });
    const fresh = {
      models: [{ id: 'openrouter/deepseek/deepseek-v4.1-flash' }],
      default: 'openrouter/deepseek/deepseek-v4.1-flash',
      recommended: ['openrouter/deepseek/deepseek-v4.1-flash'],
    };
    mockFetch.mockReturnValueOnce(oldRequest).mockResolvedValueOnce({
      ok: true,
      json: async () => fresh,
    });
    Object.defineProperty(window, 'fetch', { writable: true, value: mockFetch });
    const { result } = renderHook(() => useModels());
    expect(result.current.isLoading).toBe(true);
    act(() => {
      setupWizard$.providerStatusVersion.set(1);
    });
    await waitFor(() => expect(result.current.defaultModel).toBe(fresh.default));
    expect(result.current.isLoading).toBe(false);
    await act(async () => {
      if (settlement === 'reject') rejectOld(new Error('stale request failed'));
      else resolveOld({ ok: true, json: async () => ({ models: [], default: 'old/model' }) });
    });
    expect(result.current.defaultModel).toBe(fresh.default);
    expect(result.current.models).toEqual(fresh.models);
    expect(result.current.recommendedModels).toEqual(fresh.recommended);
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  }
);
