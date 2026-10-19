import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
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

let mockConnectionConfig = {
  baseUrl: 'http://server-a:5700',
  authToken: null,
  useAuthToken: false,
};

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
    connectionConfig: mockConnectionConfig,
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
    <textarea aria-label="Chat message" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

jest.mock('../ExamplesSection', () => ({
  ExamplesSection: () => <div data-testid="examples-section" />,
}));

const SERVER_A = { baseUrl: 'http://server-a:5700', authToken: null, useAuthToken: false };
const SERVER_B = { baseUrl: 'http://server-b:5700', authToken: null, useAuthToken: false };

const textbox = () => screen.getByRole('textbox', { name: 'Chat message' }) as HTMLTextAreaElement;
const type = (value: string) => fireEvent.change(textbox(), { target: { value } });
const ui = () => (
  <EmbeddedContextProvider>
    <SettingsProvider>
      <WelcomeView />
    </SettingsProvider>
  </EmbeddedContextProvider>
);

describe('WelcomeView new-chat draft ownership', () => {
  let setItemSpy: jest.SpyInstance;

  beforeEach(() => {
    localStorage.clear();
    mockConnectionConfig = SERVER_A;
    Object.defineProperty(window, 'fetch', { writable: true, value: mockFetch });
    setItemSpy = jest.spyOn(Storage.prototype, 'setItem');
  });

  afterEach(() => setItemSpy.mockRestore());

  it('keeps the new-chat draft with the server it was typed for across a live switch', () => {
    const { rerender, unmount } = render(ui());
    type('secret plan for A');

    mockConnectionConfig = SERVER_B;
    rerender(ui());
    expect(textbox().value).toBe('');
    const bWrites = setItemSpy.mock.calls
      .filter(([k]) => String(k).includes(encodeURIComponent('http://server-b:5700')))
      .map(([, v]) => v);
    expect(bWrites).not.toContain('secret plan for A');
    type('B draft');

    mockConnectionConfig = SERVER_A;
    rerender(ui());
    expect(textbox().value).toBe('secret plan for A');

    unmount();
    mockConnectionConfig = SERVER_B;
    render(ui());
    expect(textbox().value).toBe('B draft');
  });

  it('does not adopt the legacy unscoped new-chat draft', () => {
    localStorage.setItem('gptme-draft-new', 'legacy text');
    render(ui());
    expect(textbox().value).toBe('');
    expect(localStorage.getItem('gptme-draft-new')).toBe('legacy text');
  });
});
