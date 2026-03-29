import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { ChatInput } from '../ChatInput';

const mockUploadFiles = jest.fn();

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
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    conversations$: {
      get: jest.fn(() =>
        observable({
          isGenerating: false,
          executingTool: null,
          chatConfig: { chat: {} },
        })
      ),
    },
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
});
