import '@testing-library/jest-dom';
import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SettingsProvider } from '@/contexts/SettingsContext';
import { EmbeddedContextProvider } from '@/contexts/EmbeddedContext';
import { WelcomeView } from '../WelcomeView';
import { composerDraftKey } from '@/utils/composerDraft';

const mockNavigate = jest.fn();
const mockInvalidateQueries = jest.fn();
const mockConnect = jest.fn();
const mockCreateConversation = jest.fn(async () => 'conversation-1');
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
      createConversationWithPlaceholder: mockCreateConversation,
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
const postSeed = (prompt: string, autoSend = false) => {
  window.dispatchEvent(
    new MessageEvent('message', {
      data: { type: 'gptme-host:seed-prompt', payload: { prompt, autoSend } },
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
    mockCreateConversation.mockClear();
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
    localStorage.setItem(
      composerDraftKey({ baseUrl: 'http://localhost:5700', authToken: null, useAuthToken: false }),
      'my unsent draft'
    );

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

  it('sends a seed the host marked autoSend instead of waiting for Enter', async () => {
    render(
      <EmbeddedContextProvider>
        <SettingsProvider>
          <WelcomeView />
        </SettingsProvider>
      </EmbeddedContextProvider>
    );

    await act(async () => {
      postSeed('what is gptme?', true);
    });

    await waitFor(() => {
      expect(mockCreateConversation).toHaveBeenCalledTimes(1);
    });
    expect(mockCreateConversation).toHaveBeenCalledWith(
      'what is gptme?',
      expect.objectContaining({ workspace: '.' })
    );
    expect(mockNavigate).toHaveBeenCalledWith('/chat/conversation-1');
  });

  it('keeps a plain seed pre-filled (no auto-send) without host opt-in', async () => {
    render(
      <EmbeddedContextProvider>
        <SettingsProvider>
          <WelcomeView />
        </SettingsProvider>
      </EmbeddedContextProvider>
    );

    await act(async () => {
      postSeed('tell me about gptme');
    });

    await waitFor(() => {
      expect(screen.getByTestId('chat-input')).toHaveTextContent('tell me about gptme');
    });
    expect(mockCreateConversation).not.toHaveBeenCalled();
  });

  it('does not auto-send an autoSend seed that would clobber an existing draft', async () => {
    localStorage.setItem(
      composerDraftKey({ baseUrl: 'http://localhost:5700', authToken: null, useAuthToken: false }),
      'my unsent draft'
    );

    render(
      <EmbeddedContextProvider>
        <SettingsProvider>
          <WelcomeView />
        </SettingsProvider>
      </EmbeddedContextProvider>
    );

    await act(async () => {
      postSeed('what is gptme?', true);
    });

    await waitFor(() => {
      expect(screen.getByTestId('chat-input')).toHaveTextContent('my unsent draft');
    });
    expect(mockCreateConversation).not.toHaveBeenCalled();
  });

  it('holds an autoSend seed until the server connects', async () => {
    isConnected$.set(false);

    render(
      <EmbeddedContextProvider>
        <SettingsProvider>
          <WelcomeView />
        </SettingsProvider>
      </EmbeddedContextProvider>
    );

    await act(async () => {
      postSeed('what is gptme?', true);
    });

    // Pre-filled but not sent while disconnected.
    await waitFor(() => {
      expect(screen.getByTestId('chat-input')).toHaveTextContent('what is gptme?');
    });
    expect(mockCreateConversation).not.toHaveBeenCalled();

    await act(async () => {
      isConnected$.set(true);
    });

    await waitFor(() => {
      expect(mockCreateConversation).toHaveBeenCalledWith(
        'what is gptme?',
        expect.objectContaining({ workspace: '.' })
      );
    });
  });
});
