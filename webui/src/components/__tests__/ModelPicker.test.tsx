import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// cmdk uses ResizeObserver and scrollIntoView; jsdom doesn't implement them.
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.HTMLElement.prototype.scrollIntoView = jest.fn();

// ── Mocks ──────────────────────────────────────────────────────────────────

const mockToggleFavorite = jest.fn();

const KNOWN_MODELS = [
  {
    id: 'anthropic/claude-sonnet-4-6',
    provider: 'anthropic',
    model: 'claude-sonnet-4-6',
    context: 200000,
    supports_streaming: true,
    supports_vision: true,
    supports_reasoning: true,
    price_input: 3,
    price_output: 15,
  },
  {
    id: 'openrouter/deepseek/deepseek-chat',
    provider: 'openrouter',
    model: 'deepseek/deepseek-chat',
    context: 64000,
    supports_streaming: true,
    supports_vision: false,
    supports_reasoning: false,
    price_input: 0.14,
    price_output: 1.1,
  },
];

jest.mock('@/hooks/useModels', () => ({
  useModels: () => ({
    models: KNOWN_MODELS,
    availableModels: KNOWN_MODELS.map((m) => m.id),
    defaultModel: null,
    isLoading: false,
    error: null,
    recommendedModels: [KNOWN_MODELS[0].id],
    favorites: [],
    saveFavorites: jest.fn(),
    toggleFavorite: mockToggleFavorite,
    saveDefaultModel: jest.fn(),
  }),
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { baseUrl: 'http://localhost:5700', authHeader: null },
    isConnected$: { get: () => true },
  }),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { get: () => unknown } | null) => (obs ? obs.get() : null),
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => false,
}));

// ── Tests ──────────────────────────────────────────────────────────────────

import { ModelPicker } from '../ModelPicker';
import { TooltipProvider } from '@/components/ui/tooltip';

/** Wrap with TooltipProvider (required by ProviderIcon inside ModelItem). */
function renderPicker(onSelect: (id: string) => void, value?: string) {
  return render(
    <TooltipProvider>
      <ModelPicker onSelect={onSelect} value={value} />
    </TooltipProvider>
  );
}

describe('ModelPicker — custom model ID entry', () => {
  const onSelect = jest.fn();

  beforeEach(() => {
    onSelect.mockClear();
  });

  it('renders the search input with the updated placeholder', () => {
    renderPicker(onSelect);
    expect(screen.getByPlaceholderText('Search models or enter model ID...')).toBeInTheDocument();
  });

  it('shows no custom entry when input is empty', () => {
    renderPicker(onSelect);
    expect(screen.queryByText('Use this model ID directly')).not.toBeInTheDocument();
  });

  it('shows no custom entry when typed text exactly matches a known model ID', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    await user.type(input, 'anthropic/claude-sonnet-4-6');

    expect(screen.queryByText('Use this model ID directly')).not.toBeInTheDocument();
  });

  it('shows a custom entry when typed text is not a known model ID', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    await user.type(input, 'openrouter/deepseek/deepseek-v4-flash-0731@deepseek');

    expect(screen.getByText('Use this model ID directly')).toBeInTheDocument();
    expect(
      screen.getByText('openrouter/deepseek/deepseek-v4-flash-0731@deepseek')
    ).toBeInTheDocument();
    expect(screen.getByText('Custom model ID')).toBeInTheDocument();
  });

  it('calls onSelect with the exact typed ID (including @subprovider suffix)', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    const customId = 'openrouter/deepseek/deepseek-v4-flash-0731@deepseek';
    await user.type(input, customId);

    const customItem = screen.getByText('Use this model ID directly');
    await user.click(customItem);

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith(customId);
    });
  });

  it('shows a custom entry for partial/unknown model IDs (e.g. just a vendor path)', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    await user.type(input, 'openrouter/my-org/my-private-model');

    expect(screen.getByText('Use this model ID directly')).toBeInTheDocument();
  });

  it('does not show custom entry when input matches second known model exactly', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    await user.type(input, 'openrouter/deepseek/deepseek-chat');

    expect(screen.queryByText('Use this model ID directly')).not.toBeInTheDocument();
  });

  it('calls onSelect with verbatim input including leading/trailing whitespace', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    const customId = '  openrouter/deepseek/deepseek-v4  ';
    await user.type(input, customId);

    const customItem = screen.getByText('Use this model ID directly');
    await user.click(customItem);

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith(customId);
    });
  });

  it('shows custom entry and passes verbatim input for whitespace-padded known model ID', async () => {
    const user = userEvent.setup();
    renderPicker(onSelect);

    const input = screen.getByPlaceholderText('Search models or enter model ID...');
    const paddedId = '  anthropic/claude-sonnet-4-6  ';
    await user.type(input, paddedId);

    // Whitespace-padded ID does not match the canonical model.id, so custom entry must appear
    expect(screen.getByText('Use this model ID directly')).toBeInTheDocument();

    const customItem = screen.getByText('Use this model ID directly');
    await user.click(customItem);

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith(paddedId);
    });
  });
});
