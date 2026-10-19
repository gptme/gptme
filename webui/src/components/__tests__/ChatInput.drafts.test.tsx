import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { ChatInput } from '../ChatInput';
import { serverRegistry$ } from '@/stores/servers';
import type { ServerConfig } from '@/types/servers';

const mockUploadFiles = jest.fn();

// Three-value sentinel for chatConfig:
//   undefined = fetch not yet attempted (show loading skeleton)
//   null      = fetch completed but failed (show fallback model, no skeleton)
//   ChatConfig = successfully fetched
type MockChatConfig = { chat: { model?: string } } | null | undefined;
const mockConversation$ = observable<{
  isGenerating: boolean;
  executingTool: null;
  chatConfig: MockChatConfig;
  pendingModel?: string;
}>({
  isGenerating: false,
  executingTool: null,
  chatConfig: { chat: {} },
});

jest.mock('@/contexts/SettingsContext', () => ({
  useSettings: () => ({
    settings: {
      chimeEnabled: true,
      blocksDefaultOpen: true,
      showHiddenMessages: false,
      showInitialSystem: false,
      hasCompletedSetup: true,
      welcomeBackground: '',
      voiceServerUrl: '',
    },
    updateSettings: jest.fn(),
    resetSettings: jest.fn(),
  }),
}));

let mockConnectionConfig: { baseUrl: string; authToken: string | null; useAuthToken: boolean } = {
  baseUrl: 'http://server-a:5700',
  authToken: null,
  useAuthToken: false,
};

jest.mock('@/contexts/ApiContext', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    useApi: () => ({
      api: {
        uploadFiles: mockUploadFiles,
      },
      isConnected$: observable(true),
      connectionConfig: mockConnectionConfig,
    }),
  };
});

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
  return {
    conversations$: {
      get: jest.fn(() => mockConversation$),
    },
    setMaxTokens: jest.fn(),
    setTemperature: jest.fn(),
    setTopP: jest.fn(),
  };
});

const mockSaveDefaultModel = jest.fn();
let mockDefaultModel = '';
let mockModelsLoading = false;

beforeEach(() => {
  mockDefaultModel = '';
  mockModelsLoading = false;
});

jest.mock('@/hooks/useModels', () => ({
  useModels: () => ({
    models: [],
    defaultModel: mockDefaultModel,
    availableModels: [],
    recommendedModels: [],
    isLoading: mockModelsLoading,
    error: null,
    saveDefaultModel: mockSaveDefaultModel,
  }),
}));

jest.mock('@/hooks/useWorkspaces', () => ({
  useWorkspaces: () => ({ workspaces: [], addCustomWorkspace: jest.fn() }),
}));

jest.mock('@/hooks/useFileAutocomplete', () => ({
  useFileAutocomplete: () => ({
    state: {
      files: [],
      selectedIndex: -1,
      isOpen: false,
      query: '',
    },
    handleInputChange: jest.fn(),
    handleKeyDown: jest.fn(() => false),
    selectFile: jest.fn(() => ''),
    setSelectedIndex: jest.fn(),
  }),
}));

jest.mock('@/components/ModelPicker', () => ({
  ModelPicker: () => null,
  ModelPickerField: () => null,
}));

jest.mock('@/hooks/useAgents', () => ({
  useAgents: () => ({ agents: [], isLoading: false, error: null }),
}));

jest.mock('@/components/WorkspaceSelector', () => ({
  WorkspaceSelector: () => null,
}));

jest.mock('@/components/FileAutocomplete', () => ({
  FileAutocomplete: () => null,
}));

jest.mock('sonner', () => ({
  toast: {
    error: jest.fn(),
  },
}));

const SERVER_A = { baseUrl: 'http://server-a:5700', authToken: null, useAuthToken: false };
const SERVER_B = { baseUrl: 'http://server-b:5700', authToken: null, useAuthToken: false };

function jwt(sub: string, nonce: string): string {
  const enc = (o: object) =>
    btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${enc({ alg: 'none' })}.${enc({ sub, nonce })}.sig`;
}

function draftEntries(): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)!;
    if (k.startsWith('gptme-draft')) out[k] = localStorage.getItem(k)!;
  }
  return out;
}

const textbox = () => screen.getByRole('textbox', { name: 'Chat message' }) as HTMLTextAreaElement;
const type = (value: string) => fireEvent.change(textbox(), { target: { value } });

function renderInput(props: { conversationId?: string; serverId?: string } = {}) {
  const autoFocus$ = observable(false);
  const ui = (p: typeof props) => <ChatInput {...p} onSend={jest.fn()} autoFocus$={autoFocus$} />;
  const result = render(ui(props));
  return { ...result, rerenderWith: (p: typeof props) => result.rerender(ui(p)) };
}

describe('ChatInput draft ownership', () => {
  let setItemSpy: jest.SpyInstance;

  beforeEach(() => {
    window.localStorage.clear();
    mockConnectionConfig = SERVER_A;
    setItemSpy = jest.spyOn(Storage.prototype, 'setItem');
  });

  afterEach(() => {
    setItemSpy.mockRestore();
    serverRegistry$.servers.set([]);
  });

  /** Every (key, value) pair ever written under a key containing `marker`. */
  const writesTo = (marker: string) =>
    setItemSpy.mock.calls.filter(([k]) => String(k).includes(marker)).map(([, v]) => v);

  it.each([
    ['an existing conversation id', 'same-id'],
    ['the new-chat composer', undefined],
  ])('keeps drafts for %s separate per server', (_label, conversationId) => {
    const { rerenderWith, unmount } = renderInput({ conversationId });
    type('secret plan for A');

    mockConnectionConfig = SERVER_B;
    rerenderWith({ conversationId });
    expect(textbox().value).toBe('');
    // The outgoing text is never written under any server-b key, even transiently.
    expect(writesTo(encodeURIComponent('http://server-b:5700'))).not.toContain('secret plan for A');
    type('B draft');

    mockConnectionConfig = SERVER_A;
    rerenderWith({ conversationId });
    expect(textbox().value).toBe('secret plan for A');

    // Reload: each owner gets back only its own draft.
    unmount();
    mockConnectionConfig = SERVER_B;
    const reloaded = renderInput({ conversationId });
    expect(textbox().value).toBe('B draft');
    reloaded.unmount();
    mockConnectionConfig = SERVER_A;
    renderInput({ conversationId });
    expect(textbox().value).toBe('secret plan for A');
  });

  it('isolates different opaque credentials on the same server URL', () => {
    mockConnectionConfig = { ...SERVER_A, authToken: 'token-one', useAuthToken: true };
    const { rerenderWith } = renderInput({ conversationId: 'c1' });
    type('account one text');

    mockConnectionConfig = { ...SERVER_A, authToken: 'token-two', useAuthToken: true };
    rerenderWith({ conversationId: 'c1' });
    expect(textbox().value).toBe('');

    // Anonymous access to the same URL is a different owner too.
    mockConnectionConfig = SERVER_A;
    rerenderWith({ conversationId: 'c1' });
    expect(textbox().value).toBe('');

    const keys = Object.keys(draftEntries()).join('\n');
    expect(keys).not.toContain('token-one');
    expect(keys).not.toContain('token-two');
  });

  it('keeps the draft across a same-subject JWT refresh', () => {
    mockConnectionConfig = { ...SERVER_A, authToken: jwt('user-1', 'a'), useAuthToken: true };
    const { rerenderWith } = renderInput({ conversationId: 'c1' });
    type('survives refresh');

    mockConnectionConfig = { ...SERVER_A, authToken: jwt('user-1', 'b'), useAuthToken: true };
    rerenderWith({ conversationId: 'c1' });
    expect(textbox().value).toBe('survives refresh');

    mockConnectionConfig = { ...SERVER_A, authToken: jwt('user-2', 'c'), useAuthToken: true };
    rerenderWith({ conversationId: 'c1' });
    expect(textbox().value).toBe('');
  });

  it('still switches drafts between conversations and clears an emptied draft', () => {
    const { rerenderWith } = renderInput({ conversationId: 'c1' });
    type('first');
    rerenderWith({ conversationId: 'c2' });
    expect(textbox().value).toBe('');
    type('second');
    rerenderWith({ conversationId: 'c1' });
    expect(textbox().value).toBe('first');

    type('');
    expect(Object.values(draftEntries())).toEqual(['second']);
  });

  it('neither adopts nor deletes legacy unscoped drafts', () => {
    localStorage.setItem('gptme-draft-c1', 'legacy conversation text');
    localStorage.setItem('gptme-draft-new', 'legacy new-chat text');

    // The conversation composer and the new-chat composer are separate mounts
    // in the app (ConversationContent vs WelcomeView).
    const conversation = renderInput({ conversationId: 'c1' });
    expect(textbox().value).toBe('');
    conversation.unmount();
    expect(localStorage.getItem('gptme-draft-c1')).toBe('legacy conversation text');
    renderInput({});
    expect(textbox().value).toBe('');

    expect(localStorage.getItem('gptme-draft-c1')).toBe('legacy conversation text');
    expect(localStorage.getItem('gptme-draft-new')).toBe('legacy new-chat text');
  });

  it("scopes a secondary server's conversation to that server, not the primary", () => {
    const secondary: ServerConfig = {
      id: 'srv-b',
      name: 'B',
      ...SERVER_B,
      createdAt: 0,
      lastUsedAt: 0,
    };
    serverRegistry$.servers.set([secondary]);

    const { rerenderWith } = renderInput({ conversationId: 'same-id', serverId: 'srv-b' });
    type('belongs to B');
    expect(Object.keys(draftEntries())).toEqual([
      expect.stringContaining(encodeURIComponent('http://server-b:5700')),
    ]);

    // The primary server's conversation with the same id is a different owner.
    rerenderWith({ conversationId: 'same-id' });
    expect(textbox().value).toBe('');
  });

  it('does not persist a draft for a conversation whose server is unregistered', () => {
    renderInput({ conversationId: 'c1', serverId: 'gone' });
    type('nowhere to go');
    expect(draftEntries()).toEqual({});
  });
});
