import '@testing-library/jest-dom';
import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SettingsProvider } from '@/contexts/SettingsContext';
import { EmbeddedContextProvider } from '@/contexts/EmbeddedContext';
import { WelcomeView } from '../WelcomeView';

const mockNavigate = jest.fn();
const mockInvalidateQueries = jest.fn();
const mockConnect = jest.fn();
const mockFetch = jest.fn(() => new Promise(() => {}));
const isConnected$ = observable(true);
const isAutoConnecting$ = observable(false);
const compatibilityWarning$ = observable<null>(null);
const lastConnectionResult$ = observable<null>(null);

jest.mock('@/utils/viteEnv', () => ({
  isViteDev: false,
  isEmbeddedMode: true,
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => false,
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => false,
  invokeTauri: jest.fn(),
}));

jest.mock('@/hooks/useTauriServerStatus', () => ({
  useTauriServerStatus: () => ({ isLoading: false, managesLocalServer: false, serverStatus: null }),
}));

jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      createConversationWithPlaceholder: jest.fn(),
      authHeader: null,
      lastConnectionResult$,
      compatibilityWarning$,
    },
    isConnected$,
    isAutoConnecting$,
    connect: mockConnect,
    connectionConfig: { baseUrl: 'http://localhost:5700' },
    switchServer: jest.fn(),
  }),
}));

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

jest.mock('@/stores/servers', () => {
  const { observable: obs } = jest.requireActual('@legendapp/state');
  return {
    serverRegistry$: obs({
      servers: [{ id: 'default', name: 'Default' }],
      activeServerId: 'default',
    }),
    getConnectedServers: () => [{ id: 'default', name: 'Default' }],
  };
});

jest.mock('../ChatInput', () => ({
  ChatInput: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <div>
      <div data-testid="chat-input">{value}</div>
      <button onClick={() => onChange('')}>clear-input</button>
    </div>
  ),
}));

jest.mock('../ExamplesSection', () => ({
  ExamplesSection: () => <div data-testid="examples-section" />,
}));

/** Toggles WelcomeView's presence so a seed can be delivered before it mounts. */
const ToggleableWelcome = () => {
  const [mounted, setMounted] = useState(false);
  return (
    <EmbeddedContextProvider>
      <SettingsProvider>
        {mounted ? (
          <WelcomeView />
        ) : (
          <button onClick={() => setMounted(true)}>mount-welcome</button>
        )}
      </SettingsProvider>
    </EmbeddedContextProvider>
  );
};

// jsdom's window.postMessage does not set event.source, which the real
// same-window handler in EmbeddedContext relies on to accept the message —
// dispatch the MessageEvent directly with source set, matching what browsers
// actually deliver for same-window postMessage.
const postSeed = (prompt: string) => {
  window.dispatchEvent(
    new MessageEvent('message', {
      data: { type: 'gptme-host:seed-prompt', payload: { prompt } },
      origin: window.location.origin,
      source: window,
    })
  );
};

describe('WelcomeView seed-prompt delivery', () => {
  beforeEach(() => {
    localStorage.clear();
    isConnected$.set(true);
    isAutoConnecting$.set(false);
    lastConnectionResult$.set(null);
    compatibilityWarning$.set(null);
    mockConnect.mockReset();
    mockNavigate.mockClear();
    mockInvalidateQueries.mockClear();
    Object.defineProperty(window, 'fetch', { writable: true, value: mockFetch });
  });

  it('applies a seed prompt that arrives after WelcomeView has mounted', async () => {
    render(
      <EmbeddedContextProvider>
        <SettingsProvider>
          <WelcomeView />
        </SettingsProvider>
      </EmbeddedContextProvider>
    );

    expect(screen.getByTestId('chat-input')).toHaveTextContent('');

    await act(async () => {
      postSeed('tell me about gptme');
    });

    await waitFor(() => {
      expect(screen.getByTestId('chat-input')).toHaveTextContent('tell me about gptme');
    });
  });

  it('applies a seed prompt that arrived before WelcomeView mounted', async () => {
    render(<ToggleableWelcome />);

    await act(async () => {
      postSeed('early seed');
    });

    act(() => {
      screen.getByRole('button', { name: 'mount-welcome' }).click();
    });

    await waitFor(() => {
      expect(screen.getByTestId('chat-input')).toHaveTextContent('early seed');
    });
  });

  it('drops (does not defer) a late seed that arrives while the input is non-empty', async () => {
    localStorage.setItem('gptme-draft-new', 'my unsent draft');

    render(
      <EmbeddedContextProvider>
        <SettingsProvider>
          <WelcomeView />
        </SettingsProvider>
      </EmbeddedContextProvider>
    );

    expect(screen.getByTestId('chat-input')).toHaveTextContent('my unsent draft');

    await act(async () => {
      postSeed('a seed that must not overwrite user text');
    });

    // The draft survives — the seed did not overwrite it.
    await waitFor(() => {
      expect(screen.getByTestId('chat-input')).toHaveTextContent('my unsent draft');
    });

    // The seed is a one-shot delivery: it is not held onto and re-applied
    // once the input becomes empty again. Clearing the input must not make
    // the dropped seed reappear.
    fireEvent.click(screen.getByRole('button', { name: 'clear-input' }));
    expect(screen.getByTestId('chat-input')).toHaveTextContent('');
  });
});
