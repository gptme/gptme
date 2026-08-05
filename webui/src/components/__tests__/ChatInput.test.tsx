import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { ChatInput } from '../ChatInput';

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

jest.mock('@/contexts/ApiContext', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    useApi: () => ({
      api: {
        uploadFiles: mockUploadFiles,
      },
      isConnected$: observable(true),
      connectionConfig: { baseUrl: 'http://localhost:5700', authToken: null, useAuthToken: false },
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

jest.mock('@/hooks/useModels', () => ({
  useModels: () => ({
    models: [],
    defaultModel: '',
    availableModels: [],
    recommendedModels: [],
    isLoading: false,
    error: null,
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

describe('ChatInput', () => {
  beforeEach(() => {
    mockUploadFiles.mockReset();
    mockUploadFiles.mockResolvedValue({
      files: [
        {
          name: 'test.txt',
          path: '/tmp/conv-a/attachments/test.txt',
        },
      ],
    });
    window.localStorage.clear();
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: { chat: {} },
    });
  });

  it('clears attached files when the conversation changes', async () => {
    const autoFocus$ = observable(false);
    const onSend = jest.fn();

    const { container, rerender } = render(
      <ChatInput conversationId="conv-a" onSend={onSend} autoFocus$={autoFocus$} />
    );

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement | null;
    expect(fileInput).not.toBeNull();

    const file = new File(['hello world'], 'test.txt', { type: 'text/plain' });
    fireEvent.change(fileInput!, { target: { files: [file] } });

    // Files are buffered locally (not uploaded until send)
    await waitFor(() => expect(screen.getByText('test.txt')).toBeInTheDocument());

    rerender(<ChatInput conversationId="conv-b" onSend={onSend} autoFocus$={autoFocus$} />);

    await waitFor(() => expect(screen.queryByText('test.txt')).not.toBeInTheDocument());
  });

  it('labels the composer and disables empty sends', async () => {
    const autoFocus$ = observable(false);
    const onSend = jest.fn();

    render(<ChatInput conversationId="conv-a" onSend={onSend} autoFocus$={autoFocus$} />);

    const input = screen.getByRole('textbox', { name: 'Chat message' });
    expect(input).toHaveAccessibleDescription(/Press Enter to send/);

    const sendButton = screen.getByRole('button', { name: 'Send message' });
    expect(sendButton).toBeDisabled();

    fireEvent.change(input, { target: { value: 'ship the refresh' } });
    expect(sendButton).toBeEnabled();

    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledWith(
        'ship the refresh',
        expect.objectContaining({ stream: true, workspace: '.' })
      );
    });
  });

  it('keeps the stop action enabled while generating without text', () => {
    const autoFocus$ = observable(false);
    mockConversation$.set({
      isGenerating: true,
      executingTool: null,
      chatConfig: { chat: {} },
    });

    render(
      <ChatInput
        conversationId="conv-a"
        onSend={jest.fn()}
        onInterrupt={jest.fn().mockResolvedValue(undefined)}
        autoFocus$={autoFocus$}
      />
    );

    const stopButton = screen.getByRole('button', { name: 'Stop generation' });
    expect(stopButton).toBeEnabled();
    expect(stopButton).toHaveTextContent('Stop');
  });

  it('focuses the textarea on mount when autoFocus$ is true and resets the observable', async () => {
    // autoFocus$ must be reset to false after focusing so a stable observable
    // (useObservable in WelcomeView) doesn't re-trigger focus on subsequent renders.
    const autoFocus$ = observable(true);
    render(<ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    const textarea = screen.getByRole('textbox', { name: 'Chat message' });
    await waitFor(() => expect(document.activeElement).toBe(textarea));
    expect(autoFocus$.get()).toBe(false);
  });

  it('does not steal focus on re-render when autoFocus$ is false', () => {
    // Regression guard for the WelcomeView focus-steal bug:
    // before the fix, WelcomeView created a new observable(true) on every render,
    // so each state change stole focus from open popups and settings inputs.
    // With the fix (useObservable), the observable is stable and stays false after
    // the initial focus, so subsequent re-renders must not move focus.
    const autoFocus$ = observable(false);
    const onSend = jest.fn();
    const { rerender } = render(
      <ChatInput conversationId="conv-a" onSend={onSend} autoFocus$={autoFocus$} />
    );

    const textarea = screen.getByRole('textbox', { name: 'Chat message' });
    act(() => textarea.blur());
    expect(document.activeElement).not.toBe(textarea);

    rerender(<ChatInput conversationId="conv-a" onSend={onSend} autoFocus$={autoFocus$} />);
    expect(document.activeElement).not.toBe(textarea);
  });

  it('preserves existing attachments in edit mode', async () => {
    const autoFocus$ = observable(false);
    const onEditSave = jest.fn();

    const { rerender } = render(
      <ChatInput
        conversationId="conv-a"
        autoFocus$={autoFocus$}
        editMode
        editFiles={['/tmp/conv-a/attachments/existing.txt']}
        onEditSave={onEditSave}
      />
    );

    expect(screen.getByText('existing.txt')).toBeInTheDocument();

    rerender(
      <ChatInput
        conversationId="conv-b"
        autoFocus$={autoFocus$}
        editMode
        editFiles={['/tmp/conv-b/attachments/new.txt']}
        onEditSave={onEditSave}
      />
    );

    await waitFor(() => {
      expect(screen.queryByText('existing.txt')).not.toBeInTheDocument();
      expect(screen.getByText('new.txt')).toBeInTheDocument();
    });
  });
});

// Regression suite for gptme/gptme#3440 — model selector stability.
//
// Root cause: chatConfig (and thus the conversation model) loads asynchronously.
// Before the fix, the model badge showed the hardcoded client-side fallback
// ('anthropic/claude-sonnet-4-x') on every conversation open, then flickered to
// the real model once the API response arrived.  Users saw the wrong model in the
// badge for 1-3 seconds on every page load.
//
// Fix surface: ChatInput.tsx — effectiveModel derivation + ModelBadge data-testid.

describe('Model selector (gptme#3440)', () => {
  beforeEach(() => {
    // Reset to a clean state before each test
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: { chat: {} },
    });
  });

  it('displays the conversation model when chatConfig is populated', () => {
    // Simulate a conversation that has been opened and its config loaded.
    // The model badge must show the conversation model, not the hardcoded fallback.
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: { chat: { model: 'openai/gpt-4o' } },
    });

    const autoFocus$ = observable(false);
    render(<ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    // displayName = modelInfo?.model || model.split('/').pop()
    // With an empty models list in the mock, split('/').pop() gives 'gpt-4o'.
    const badge = screen.getByTestId('model-selector');
    expect(badge).toHaveTextContent('gpt-4o');
    // Must NOT show any variant of the legacy hardcoded fallback
    expect(badge).not.toHaveTextContent('claude-sonnet');
  });

  it('shows a loading skeleton before chatConfig loads (not the wrong fallback model)', () => {
    // Root cause of bug #3440-item-1: before chatConfig arrives the badge was
    // showing the client-side fallback model (claude-sonnet-4-6) as if it were
    // the conversation's model, confusing users who started a conversation with
    // a different model.  Fix: render a skeleton pill until chatConfig is known.
    // undefined = fetch not yet attempted (the true loading state).
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: undefined,
    });

    const autoFocus$ = observable(false);
    render(<ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    const badge = screen.getByTestId('model-selector');
    // Loading skeleton must be present and must NOT expose the wrong model name.
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute('aria-label', 'Loading model...');
    expect(badge.textContent ?? '').toBe('');
  });

  it('transitions from loading skeleton to real model when chatConfig arrives', async () => {
    // Phase 1: chatConfig not yet loaded (undefined = fetch not yet attempted) —
    // badge shows the skeleton, never the wrong fallback model name.
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: undefined,
    });

    const autoFocus$ = observable(false);
    render(<ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    // Loading skeleton is visible.
    expect(screen.getByTestId('model-selector')).toHaveAttribute('aria-label', 'Loading model...');
    // No model name text should be visible while loading.
    expect((screen.getByTestId('model-selector').textContent ?? '').trim()).toBe('');

    // Phase 2: simulate the API response landing — chatConfig updates with real model.
    act(() => {
      mockConversation$.chatConfig.set({ chat: { model: 'anthropic/claude-haiku-4-5' } });
    });

    // Badge must switch from skeleton to the actual conversation model.
    await waitFor(() => {
      expect(screen.getByTestId('model-selector')).toHaveTextContent('claude-haiku-4-5');
    });
    // Aria-label should now show the model, not the loading label.
    expect(screen.getByTestId('model-selector')).not.toHaveAttribute(
      'aria-label',
      'Loading model...'
    );
  });

  it('clears the loading skeleton when chatConfig fetch fails (no permanent skeleton)', () => {
    // Regression guard for the P1 scenario: if the server is unreachable both
    // getChatConfig requests fail and chatConfig stays null in the store.
    // null = "fetch attempted, no config" — must NOT show the skeleton.
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: null,
    });

    const autoFocus$ = observable(false);
    render(<ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    const badge = screen.getByTestId('model-selector');
    // Skeleton must NOT be shown when fetch failed — badge falls back to default model.
    expect(badge).not.toHaveAttribute('aria-label', 'Loading model...');
    // Badge must display some model text, not be empty.
    expect((badge.textContent ?? '').trim()).not.toBe('');
  });

  it('resets the model badge to the new conversation model when switching conversations', async () => {
    // First conversation uses gpt-4o
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: { chat: { model: 'openai/gpt-4o' } },
    });

    const autoFocus$ = observable(false);
    const { rerender } = render(
      <ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />
    );

    expect(screen.getByTestId('model-selector')).toHaveTextContent('gpt-4o');

    // Second conversation uses a different model
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: { chat: { model: 'anthropic/claude-haiku-4-5' } },
    });

    rerender(<ChatInput conversationId="conv-b" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    await waitFor(() => {
      expect(screen.getByTestId('model-selector')).toHaveTextContent('claude-haiku-4-5');
    });
    // Must NOT linger on the previous conversation's model
    expect(screen.getByTestId('model-selector')).not.toHaveTextContent('gpt-4o');
  });

  it('keeps the model badge enabled while not generating so the user can change it', () => {
    mockConversation$.set({
      isGenerating: false,
      executingTool: null,
      chatConfig: { chat: { model: 'openai/gpt-4o' } },
    });

    const autoFocus$ = observable(false);
    render(<ChatInput conversationId="conv-a" onSend={jest.fn()} autoFocus$={autoFocus$} />);

    expect(screen.getByTestId('model-selector')).not.toBeDisabled();
  });

  it('keeps the model badge enabled during generation so the user can pre-select the next model', () => {
    // Design intent: the model selector stays interactive even while the assistant
    // is generating. The selection takes effect on the next send, so disabling it
    // would prevent users from changing the model for a follow-up message. This
    // test documents that intentional behaviour and guards against regressions that
    // might incorrectly disable the badge during a streaming response.
    mockConversation$.set({
      isGenerating: true,
      executingTool: null,
      chatConfig: { chat: { model: 'openai/gpt-4o' } },
    });

    const autoFocus$ = observable(false);
    render(
      <ChatInput
        conversationId="conv-a"
        onSend={jest.fn()}
        onInterrupt={jest.fn().mockResolvedValue(undefined)}
        autoFocus$={autoFocus$}
      />
    );

    // Badge must remain enabled — user should be able to change model mid-stream
    // for the next turn (isDisabled is tied to isReadOnly/!isConnected, not isGenerating)
    expect(screen.getByTestId('model-selector')).not.toBeDisabled();
  });
});
