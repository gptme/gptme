import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { useState } from 'react';
import { ChatInput } from '../ChatInput';
import { useConversation } from '@/hooks/useConversation';
import { ApiClient, ApiClientError, AttachmentUploadError } from '@/utils/api';
import {
  conversations$,
  initConversation,
  replaceLog,
  setGenerating,
  updateConversationData,
} from '@/stores/conversations';

let mockClient: ApiClient;
const mockToast = jest.fn();
jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: mockClient,
    getClient: () => mockClient,
    isConnected$: observable(true),
    connectionConfig: { baseUrl: 'http://localhost:5700', authToken: null, useAuthToken: false },
  }),
}));
jest.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: mockToast }) }));
jest.mock('@/contexts/SettingsContext', () => ({
  useSettings: () => ({ settings: {}, updateSettings: jest.fn() }),
}));
jest.mock('@/hooks/useModels', () => ({
  useModels: () => ({
    models: [],
    availableModels: [],
    recommendedModels: [],
    defaultModel: '',
    isLoading: false,
  }),
}));
jest.mock('@/hooks/useWorkspaces', () => ({
  useWorkspaces: () => ({ workspaces: [], addCustomWorkspace: jest.fn() }),
}));
jest.mock('@/hooks/useAgents', () => ({ useAgents: () => ({ agents: [], isLoading: false }) }));
jest.mock('@/hooks/useFileAutocomplete', () => ({
  useFileAutocomplete: () => ({
    state: { files: [], selectedIndex: -1, isOpen: false, query: '' },
    handleInputChange: jest.fn(),
    handleKeyDown: () => false,
    selectFile: () => '',
    setSelectedIndex: jest.fn(),
  }),
}));
jest.mock('@/components/ModelPicker', () => ({
  ModelPicker: () => null,
  ModelPickerField: () => null,
}));
jest.mock('@/components/WorkspaceSelector', () => ({ WorkspaceSelector: () => null }));
jest.mock('@/components/FileAutocomplete', () => ({ FileAutocomplete: () => null }));
jest.mock('@/components/VoiceButton', () => ({ VoiceButton: () => null }));
jest.mock('@/components/SpeechInputButton', () => ({ SpeechInputButton: () => null }));
jest.mock('@/utils/audio', () => ({ playChime: jest.fn().mockResolvedValue(undefined) }));
jest.mock('@/utils/notifications', () => ({
  notifyGenerationComplete: jest.fn().mockResolvedValue(undefined),
  notifyToolConfirmation: jest.fn(),
}));

function ExistingComposer() {
  const { sendMessage } = useConversation('upload-existing');
  return (
    <ChatInput
      conversationId="upload-existing"
      autoFocus$={observable(false)}
      onSend={(message, options) => sendMessage({ message, options })}
    />
  );
}
function CreatedConversation({ id }: { id: string }) {
  useConversation(id);
  return null;
}
function NewComposer() {
  const [id, setId] = useState('');
  return (
    <>
      <ChatInput
        autoFocus$={observable(false)}
        onSend={async (message, options) => {
          setId(await mockClient.createConversationWithPlaceholder(message, options));
        }}
      />
      {id && <CreatedConversation id={id} />}
    </>
  );
}

const originalFetch = global.fetch;
beforeEach(() => {
  conversations$.set(new Map());
  localStorage.clear();
  mockToast.mockClear();
  mockClient = new ApiClient('http://localhost:5700');
  mockClient.setConnected(true);
  jest.spyOn(mockClient, 'subscribeToEvents').mockImplementation(async (_id, handlers) => {
    handlers.onConnected?.();
  });
  jest.spyOn(mockClient, 'getChatConfig').mockResolvedValue(null as any);
  jest.spyOn(mockClient, 'step').mockResolvedValue(undefined);
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ session_id: 'upload-session' }),
  });
  initConversation(
    'upload-existing',
    {
      id: 'upload-existing',
      name: 'Uploads',
      log: [],
      logfile: 'upload-existing',
      branches: {},
      workspace: '.',
    },
    {}
  );
});
afterEach(() => {
  jest.restoreAllMocks();
  global.fetch = originalFetch;
});

it.each(['existing', 'new'])(
  'preserves text/files after rejected upload and deliberately retries once (%s)',
  async (kind) => {
    const upload = jest
      .spyOn(mockClient, 'uploadFiles')
      .mockRejectedValueOnce(new ApiClientError('Too large', 413))
      .mockResolvedValue({
        files: [
          { name: 'one.txt', path: '/workspace/one.txt' },
          { name: 'two.txt', path: '/workspace/two.txt' },
        ],
      } as any);
    const post = jest.spyOn(mockClient, 'sendMessage');
    const { container } = render(kind === 'existing' ? <ExistingComposer /> : <NewComposer />);
    const files = [new File(['one'], 'one.txt'), new File(['two'], 'two.txt')];
    const text = '  exact draft\nwith whitespace  ';
    const input = screen.getByRole('textbox', { name: 'Chat message' });
    fireEvent.change(input, { target: { value: text } });
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files } });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    expect(post).not.toHaveBeenCalled();
    expect(mockClient.step).not.toHaveBeenCalled();
    await waitFor(() => expect(input).toHaveValue(text));
    expect(screen.getByText('one.txt')).toBeInTheDocument();
    expect(screen.getByText('two.txt')).toBeInTheDocument();
    expect(mockToast).toHaveBeenCalled();
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(upload.mock.calls[1][1][0]).toBe(files[0]);
    expect(upload.mock.calls[1][1][1]).toBe(files[1]);
    expect(post).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        content: text,
        files: ['/workspace/one.txt', '/workspace/two.txt'],
      })
    );
    await waitFor(() => expect(input).toHaveValue(''));
  }
);

it.each(['existing', 'new'])(
  'never retries a message POST as fileless after upload success (%s)',
  async (kind) => {
    jest
      .spyOn(mockClient, 'uploadFiles')
      .mockResolvedValue({ files: [{ name: 'one.txt', path: 'attachments/one.txt' }] } as any);
    const post = jest
      .spyOn(mockClient, 'sendMessage')
      .mockRejectedValue(new ApiClientError('ambiguous transport failure', 500));
    const { container } = render(kind === 'existing' ? <ExistingComposer /> : <NewComposer />);
    const input = screen.getByRole('textbox', { name: 'Chat message' });
    fireEvent.change(input, { target: { value: 'Persist exactly once' } });
    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(['one'], 'one.txt')] },
    });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ files: ['attachments/one.txt'] })
    );
    expect(mockClient.step).not.toHaveBeenCalled();
    expect(input).toHaveValue('');
    const failed = conversations$.get(post.mock.calls[0][0])!.data.log.get().at(-1)!;
    expect(failed).toMatchObject({
      _status: 'failed',
      _retry: { persistence: 'unknown' },
      files: ['attachments/one.txt'],
    });
  }
);

it('retains a queued failed upload until explicit Retry upload, preserving files and options', async () => {
  const upload = jest
    .spyOn(mockClient, 'uploadFiles')
    .mockRejectedValueOnce(new ApiClientError('name conflict', 409))
    .mockResolvedValue({ files: [{ name: 'one.txt', path: 'attachments/one.txt' }] } as any);
  const post = jest.spyOn(mockClient, 'sendMessage');
  const state = conversations$.get('upload-existing')!;
  state.isGenerating.set(true);
  state.chatConfig.set({ chat: { model: 'test/model' } } as any);
  const { container } = render(<ExistingComposer />);
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  const file = new File(['one'], 'one.txt');
  fireEvent.change(input, { target: { value: 'queued text' } });
  fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  expect(upload).not.toHaveBeenCalled();
  await act(async () => {
    state.isGenerating.set(false);
  });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Retry upload' })).toBeInTheDocument()
  );
  expect(post).not.toHaveBeenCalled();
  expect(upload).toHaveBeenCalledTimes(1);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Retry upload' }));
  });
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(upload.mock.calls[1][1][0]).toBe(file);
  expect(mockClient.step).toHaveBeenCalledTimes(1);
});

it.each(['existing', 'new'])(
  'sends attachment-only messages and prevents double-submit during upload (%s)',
  async (kind) => {
    let finish!: (value: any) => void;
    const upload = jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    const post = jest.spyOn(mockClient, 'sendMessage');
    const { container } = render(kind === 'existing' ? <ExistingComposer /> : <NewComposer />);
    const input = screen.getByRole('textbox', { name: 'Chat message' });
    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(['one'], 'one.txt')] },
    });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(upload).toHaveBeenCalledTimes(1);
    expect(screen.getByText('one.txt')).toBeInTheDocument();
    await act(async () => {
      finish({ files: [{ name: 'one.txt', path: 'attachments/one.txt' }] });
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ content: '', files: ['attachments/one.txt'] })
    );
    expect(screen.queryByText('one.txt')).not.toBeInTheDocument();
  }
);

it('keeps a new-chat attachment draft locked until message persistence finishes', async () => {
  jest
    .spyOn(mockClient, 'uploadFiles')
    .mockResolvedValue({ files: [{ name: 'one.txt', path: 'attachments/one.txt' }] } as any);
  let finishPost!: () => void;
  const post = jest.spyOn(mockClient, 'sendMessage').mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finishPost = resolve;
      })
  );
  const create = jest.spyOn(mockClient, 'createConversationWithPlaceholder');
  const { container } = render(<NewComposer />);
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'first draft' } });
  const fileInput = container.querySelector('input[type="file"]')!;
  fireEvent.change(fileInput, { target: { files: [new File(['one'], 'one.txt')] } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(input).toBeDisabled();
  expect(input).toHaveValue('first draft');
  expect(screen.getByText('one.txt')).toBeInTheDocument();
  fireEvent.change(fileInput, { target: { files: [new File(['two'], 'two.txt')] } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(screen.queryByText('two.txt')).not.toBeInTheDocument();
  expect(create).toHaveBeenCalledTimes(1);
  await act(async () => {
    finishPost();
  });
  await waitFor(() => expect(input).not.toBeDisabled());
  expect(input).toHaveValue('');
  expect(screen.queryByText('one.txt')).not.toBeInTheDocument();
});

it('sends the next queued message when a failed head upload is discarded', async () => {
  const upload = jest
    .spyOn(mockClient, 'uploadFiles')
    .mockRejectedValue(new ApiClientError('Too large', 413));
  const post = jest.spyOn(mockClient, 'sendMessage');
  const state = conversations$.get('upload-existing')!;
  state.isGenerating.set(true);
  const { container } = render(<ExistingComposer />);
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'failed head' } });
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['one'], 'one.txt')] },
  });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  fireEvent.change(input, { target: { value: 'next queued message' } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  await act(async () => {
    state.isGenerating.set(false);
  });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Retry upload' })).toBeInTheDocument()
  );
  expect(post).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear queued message' })[0]);
  });
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post).toHaveBeenCalledWith(
    'upload-existing',
    expect.objectContaining({ content: 'next queued message' })
  );
  expect(upload).toHaveBeenCalledTimes(1);
});

// Opt-in integration against tests/fixtures/retry_server.py. Only SSE and the
// LLM boundary are stubbed; upload, append, HTTP history and JSONL are real.
(process.env.GPTME_RETRY_TEST_SERVER ? describe : describe.skip)(
  'real server upload recovery',
  () => {
    it.each(['existing', 'new'])(
      'persists no stripped message and one deliberate recovery (%s)',
      async (kind) => {
        const { request } = await import('node:http');
        const { readFileSync, writeFileSync, unlinkSync } = await import('node:fs');
        const root = process.env.GPTME_RETRY_TEST_ROOT!;
        const base = process.env.GPTME_RETRY_TEST_SERVER!;
        const sessions = new Map<string, string>();
        let rejectedId = '';
        let rejectNextUpload = true;
        const calls: { path: string; method: string }[] = [];
        const http = async (url: string, options: RequestInit = {}): Promise<any> => {
          const path = new URL(url).pathname;
          const method = options.method ?? 'GET';
          calls.push({ path, method });
          let body = options.body as string | undefined;
          const headers: Record<string, string> = {
            ...(options.headers as Record<string, string>),
          };
          if (options.body instanceof FormData) {
            const id = path.split('/')[4];
            if (rejectNextUpload) {
              rejectNextUpload = false;
              rejectedId = id;
              // A filesystem obstruction exercises a real upload failure after
              // allocation. Remove only this scratch file for deliberate retry.
              writeFileSync(`${root}/logs/${id}/attachments`, 'block upload');
            }
            const boundary = 'gptme-upload-regression';
            headers['Content-Type'] = `multipart/form-data; boundary=${boundary}`;
            const chunks: Buffer[] = [];
            for (const [, value] of options.body.entries()) {
              const file = value as File;
              const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result as ArrayBuffer);
                reader.onerror = reject;
                reader.readAsArrayBuffer(file);
              });
              chunks.push(
                Buffer.from(
                  `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${file.name}"\r\nContent-Type: text/plain\r\n\r\n`
                ),
                Buffer.from(bytes),
                Buffer.from('\r\n')
              );
            }
            chunks.push(Buffer.from(`--${boundary}--\r\n`));
            body = Buffer.concat(chunks) as any;
          }
          return new Promise((resolve, reject) => {
            const req = request(new URL(path, base), { method, headers }, (res) => {
              let data = '';
              res.on('data', (chunk) => {
                data += chunk;
              });
              res.on('end', () => {
                const value = JSON.parse(data);
                if (value.session_id) sessions.set(path.split('/')[4], value.session_id);
                resolve({
                  ok: res.statusCode! < 400,
                  status: res.statusCode,
                  json: async () => value,
                });
              });
            });
            req.on('error', reject);
            req.end(body);
          });
        };
        global.fetch = jest.fn((input, init) => http(String(input), init));
        jest.mocked(mockClient.step).mockImplementation(async (id) => {
          const response = await http(`${base}/api/v2/conversations/${id}/step`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessions.get(id), stream: false }),
          });
          if (!response.ok) throw new Error('fixture generation failed');
        });
        writeFileSync(`${root}/step_mode.txt`, 'ok');
        if (kind === 'existing')
          await mockClient.createConversation('upload-existing', [], {
            chat: { model: 'openai/mock-model' },
          } as any);
        const post = jest.spyOn(mockClient, 'sendMessage');
        const { container } = render(kind === 'existing' ? <ExistingComposer /> : <NewComposer />);
        const input = screen.getByRole('textbox', { name: 'Chat message' });
        fireEvent.change(input, { target: { value: 'real upload integrity' } });
        fireEvent.change(container.querySelector('input[type="file"]')!, {
          target: { files: [new File(['real attachment'], 'receipt.txt')] },
        });
        await act(async () => {
          fireEvent.keyDown(input, { key: 'Enter' });
        });
        await waitFor(() => expect(mockToast).toHaveBeenCalled());
        expect(post).not.toHaveBeenCalled();
        expect(mockClient.step).not.toHaveBeenCalled();
        expect(
          calls.filter((call) => call.method === 'POST' && !call.path.endsWith('/upload'))
        ).toHaveLength(0);
        const failedHistory = await (
          await http(`${base}/api/v2/conversations/${rejectedId}`)
        ).json();
        expect(failedHistory.log.filter((m: any) => m.role === 'user')).toHaveLength(0);
        expect(input).toHaveValue('real upload integrity');
        expect(screen.getByText('receipt.txt')).toBeInTheDocument();
        unlinkSync(`${root}/logs/${rejectedId}/attachments`);
        await act(async () => {
          fireEvent.keyDown(input, { key: 'Enter' });
        });
        await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
        await act(async () => {
          await post.mock.results[0].value;
        });
        await waitFor(() => expect(input).not.toBeDisabled());
        await waitFor(() => expect(mockClient.step).toHaveBeenCalledTimes(1));
        await act(async () => {
          await jest.mocked(mockClient.step).mock.results[0].value;
        });
        const id = post.mock.calls[0][0];
        const history = await (await http(`${base}/api/v2/conversations/${id}`)).json();
        const users = history.log.filter((m: any) => m.role === 'user');
        expect(users).toHaveLength(1);
        expect(users[0].content).toBe('real upload integrity');
        expect(users[0].files).toHaveLength(1);
        expect(readFileSync(`${root}/logs/${id}/${users[0].files[0]}`, 'utf8')).toBe(
          'real attachment'
        );
        const persisted = readFileSync(`${root}/logs/${id}/conversation.jsonl`, 'utf8')
          .split('\n')
          .filter(Boolean)
          .map((line: string) => JSON.parse(line));
        expect(persisted.filter((m: any) => m.role === 'user')).toHaveLength(1);
      },
      30000
    );
  }
);

it.each(['existing', 'new'])('keeps text-only sends unchanged (%s)', async (kind) => {
  const upload = jest.spyOn(mockClient, 'uploadFiles');
  const create = jest.spyOn(mockClient, 'createConversation');
  const post = jest.spyOn(mockClient, 'sendMessage');
  render(kind === 'existing' ? <ExistingComposer /> : <NewComposer />);
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'text without attachments' } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  expect(upload).not.toHaveBeenCalled();
  if (kind === 'existing') expect(post).toHaveBeenCalledTimes(1);
  else
    expect(create).toHaveBeenCalledWith(
      expect.any(String),
      [expect.objectContaining({ content: 'text without attachments' })],
      expect.anything()
    );
  expect(mockClient.step).toHaveBeenCalledTimes(1);
  expect(input).toHaveValue('');
});

it('preserves captured generation options through rejected upload and retry', async () => {
  const { renderHook } = await import('@testing-library/react');
  const upload = jest
    .spyOn(mockClient, 'uploadFiles')
    .mockRejectedValueOnce(new ApiClientError('upload unavailable', 503))
    .mockResolvedValue({ files: [{ path: 'attachments/options.txt' }] } as any);
  const { result } = renderHook(() => useConversation('upload-existing'));
  const options = {
    pendingFiles: [new File(['options'], 'options.txt')],
    files: ['attachments/previous.txt'],
    model: 'test/model',
    stream: false,
    maxTokens: 123,
    temperature: 0.4,
    topP: 0.8,
  };
  await act(async () => {
    await expect(result.current.sendMessage({ message: 'settings', options })).rejects.toThrow(
      /attached files/i
    );
  });
  await act(async () => {
    await result.current.sendMessage({ message: 'settings', options });
  });
  expect(upload).toHaveBeenCalledTimes(2);
  expect(mockClient.step).toHaveBeenCalledTimes(1);
  expect(mockClient.step).toHaveBeenCalledWith(
    'upload-existing',
    'test/model',
    false,
    'main',
    123,
    0.4,
    0.8
  );
  expect(conversations$.get('upload-existing')!.data.log.get().at(-1)?.files).toEqual([
    'attachments/previous.txt',
    'attachments/options.txt',
  ]);
});

it('does not turn an upload HTTP 409 into an automatic message retry', async () => {
  const upload = jest
    .spyOn(mockClient, 'uploadFiles')
    .mockRejectedValue(new ApiClientError('name conflict', 409));
  render(<ExistingComposer />);
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  const fileInput = document.querySelector('input[type="file"]')!;
  fireEvent.change(input, { target: { value: 'retain conflict' } });
  fireEvent.change(fileInput, { target: { files: [new File(['one'], 'one.txt')] } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  expect(input).toHaveValue('retain conflict');
  expect(screen.queryByText(/Queued:/)).not.toBeInTheDocument();
  expect(upload).toHaveBeenCalledTimes(1);
});

it('does not accept dropped files or removal while the submitted upload owns the composer', async () => {
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  const { container } = render(<ExistingComposer />);
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['A'], 'A.txt')] },
  });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  expect(screen.getByRole('button', { name: 'Remove file A.txt' })).toBeDisabled();
  fireEvent.drop(input.parentElement!, { dataTransfer: { files: [new File(['B'], 'B.txt')] } });
  expect(screen.queryByText('B.txt')).not.toBeInTheDocument();
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/A.txt' }] });
  });
});

it('hands a new-chat rejected POST to existing message Retry without uploading again', async () => {
  const { renderHook } = await import('@testing-library/react');
  const upload = jest
    .spyOn(mockClient, 'uploadFiles')
    .mockResolvedValue({ files: [{ path: 'attachments/retry.txt' }] } as any);
  const post = jest
    .spyOn(mockClient, 'sendMessage')
    .mockRejectedValueOnce(new ApiClientError('rejected', 422))
    .mockResolvedValue(undefined);
  const id = await mockClient.createConversationWithPlaceholder('retained failed message', {
    pendingFiles: [new File(['retry'], 'retry.txt')],
    model: 'test/model',
    stream: false,
  });
  const failed = conversations$.get(id)!.data.log.get().at(-1)!;
  expect(failed).toMatchObject({ _status: 'failed', _retry: { persistence: 'rejected' } });
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.retryMessage(failed);
  });
  expect(upload).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[1][1].files).toEqual(['attachments/retry.txt']);
  expect(mockClient.step).toHaveBeenCalledWith(
    id,
    'test/model',
    false,
    'main',
    undefined,
    undefined,
    undefined
  );
});

it('clears the unsent placeholder log after new-chat upload rejection', async () => {
  const create = jest.spyOn(mockClient, 'createConversation');
  jest.spyOn(mockClient, 'uploadFiles').mockRejectedValue(new ApiClientError('Too large', 413));
  await expect(
    mockClient.createConversationWithPlaceholder('never saved', {
      pendingFiles: [new File(['one'], 'one.txt')],
    })
  ).rejects.toThrow();
  const id = create.mock.calls[0][0];
  expect(conversations$.get(id)!.data.log.get()).toEqual([]);
  expect(conversations$.get(id)!.isGenerating.get()).toBe(false);
});

it('keeps newer saved messages when an earlier new-chat upload fails', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let rejectUpload!: (error: unknown) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectUpload = reject;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  const post = jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('never saved', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  // Observe the rejection now, before settling the deferred upload below.
  const rejection = expect(creation).rejects.toThrow(AttachmentUploadError);
  await waitFor(() => expect(rejectUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  expect(post).toHaveBeenCalledTimes(1);
  const saved = post.mock.calls[0][1];
  expect(saved.content).toBe('newer saved message');
  await act(async () => {
    rejectUpload(new ApiClientError('Too large', 413));
    await rejection;
  });
  expect(conversations$.get(id)!.data.log.get()).toEqual([
    expect.objectContaining({
      content: saved.content,
      timestamp: saved.timestamp,
      _status: 'sent',
    }),
  ]);
});

it('uses server order for positional edits/deletes after a late new-chat upload saves', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  const post = jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const original = conversations$.get(id)!.data.log.get()[0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  expect(post).toHaveBeenCalledTimes(1);
  const saved = post.mock.calls[0][1];
  expect(saved.content).toBe('newer saved message');
  // The server appends on POST, not by client timestamp. Include an assistant
  // turn to ensure moving the placeholder or sorting timestamps is insufficient.
  const serverLog = [
    saved,
    { role: 'assistant' as const, content: 'newer response', timestamp: '2099-01-01T00:00:00Z' },
    { ...original, files: ['attachments/one.txt'], _status: 'sent' as const },
  ];
  const get = jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: serverLog,
  });
  setGenerating(id, true);
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
    await creation;
  });
  const log = conversations$.get(id)!.data.log.get();
  expect(log.map((entry) => entry.content)).toEqual(serverLog.map((entry) => entry.content));
  expect(get).toHaveBeenCalledWith(id);
  expect(conversations$.get(id)!.isGenerating.get()).toBe(true);
  const edit = jest.spyOn(mockClient, 'editMessage').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: serverLog,
  });
  const remove = jest.spyOn(mockClient, 'deleteMessage').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: serverLog.slice(0, 2),
  });
  const index = log.findIndex((entry) => entry.timestamp === original.timestamp);
  await act(async () => {
    await result.current.editMessage(index, 'edited attached');
    await result.current.deleteMessage(index);
  });
  expect(edit).toHaveBeenCalledWith(id, 2, 'edited attached', false, undefined);
  expect(remove).toHaveBeenCalledWith(id, 2);
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[1][1]).toMatchObject({
    content: original.content,
    timestamp: original.timestamp,
    files: ['attachments/one.txt'],
  });
});

it('reconciles server order after a successful Retry of a rejected late attachment POST', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  const post = jest
    .spyOn(mockClient, 'sendMessage')
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new ApiClientError('invalid', 422))
    .mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const original = conversations$.get(id)!.data.log.get()[0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  const saved = post.mock.calls[0][1];
  expect(saved.content).toBe('newer saved message');
  const attached = {
    ...original,
    timestamp: original.timestamp!.replace('Z', '000+00:00'),
    files: ['attachments/one.txt'],
  };
  const serverLog = [
    saved,
    { role: 'assistant' as const, content: 'newer response', timestamp: '2099-01-01T00:00:00Z' },
    attached,
  ];
  jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: serverLog,
  });
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
    await creation;
  });
  const failed = conversations$.get(id)!.data.log.get()[0];
  expect(failed._retry?.persistence).toBe('rejected');
  const edit = jest.spyOn(mockClient, 'editMessage').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: serverLog,
  });
  await act(async () => {
    await result.current.retryMessage(failed);
  });
  const log = conversations$.get(id)!.data.log.get();
  expect(log.map((entry) => entry.content)).toEqual(serverLog.map((entry) => entry.content));
  expect(log.every((entry) => !entry._retry || entry._retry.persistence === 'saved')).toBe(true);
  const index = log.findIndex((entry) => entry.content === original.content);
  await act(async () => {
    await result.current.editMessage(index, 'edited attached');
  });
  expect(edit).toHaveBeenCalledWith(id, 2, 'edited attached', false, undefined);
  expect(post).toHaveBeenCalledTimes(3);
});

it('does not restore a message deleted during upload-order reconciliation GET', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  let resolveFirstGet!: (value: any) => void;
  let resolveSecondGet!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const get = jest.spyOn(mockClient, 'getConversation').mockImplementation(
    () =>
      new Promise((resolve) => {
        if (get.mock.calls.length <= 1) {
          resolveFirstGet = resolve;
        } else {
          resolveSecondGet = resolve;
        }
      })
  );
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const original = conversations$.get(id)!.data.log.get()[0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  const saved = conversations$
    .get(id)!
    .data.log.get()
    .find((entry) => entry.content === 'newer saved message')!;
  const attached = { ...original, files: ['attachments/one.txt'], _status: 'sent' as const };
  const assistant = {
    role: 'assistant' as const,
    content: 'newer response',
    timestamp: '2099-01-01T00:00:00Z',
  };
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
  });
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  // deleteMessage applies the server log through replaceLog, bumping logRevision.
  replaceLog(id, [attached]);
  const staleSnapshot = {
    ...conversations$.get(id)!.data.get(),
    log: [saved, assistant, attached],
  };
  resolveFirstGet(staleSnapshot);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  resolveSecondGet({
    ...conversations$.get(id)!.data.get(),
    log: [assistant, attached],
  });
  await creation;
  const log = conversations$.get(id)!.data.log.get();
  expect(log.map((entry) => entry.content)).toEqual(['newer response', 'original attached']);
  expect(log.some((entry) => entry.content === 'newer saved message')).toBe(false);
  const remove = jest.spyOn(mockClient, 'deleteMessage').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: [attached],
  });
  await act(async () => {
    await result.current.deleteMessage(0);
  });
  expect(remove).toHaveBeenCalledWith(id, 0);
});

it('does not overwrite an edit made during upload-order reconciliation GET', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  let resolveFirstGet!: (value: any) => void;
  let resolveSecondGet!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const get = jest.spyOn(mockClient, 'getConversation').mockImplementation(
    () =>
      new Promise((resolve) => {
        if (get.mock.calls.length <= 1) {
          resolveFirstGet = resolve;
        } else {
          resolveSecondGet = resolve;
        }
      })
  );
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const original = conversations$.get(id)!.data.log.get()[0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  const saved = conversations$
    .get(id)!
    .data.log.get()
    .find((entry) => entry.content === 'newer saved message')!;
  const attached = { ...original, files: ['attachments/one.txt'] };
  const edited = { ...saved, content: 'edited newer message' };
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
  });
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  replaceLog(id, [edited, attached]);
  resolveFirstGet({
    ...conversations$.get(id)!.data.get(),
    log: [saved, attached],
  });
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  resolveSecondGet({
    ...conversations$.get(id)!.data.get(),
    log: [edited, attached],
  });
  await creation;
  expect(
    conversations$
      .get(id)!
      .data.log.get()
      .map((entry) => entry.content)
  ).toEqual(['edited newer message', 'original attached']);
});

it('preserves Retry metadata and sends started during upload-order reconciliation', async () => {
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  let resolveGet!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const get = jest.spyOn(mockClient, 'getConversation').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveGet = resolve;
      })
  );
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const state = conversations$.get(id)!;
  const original = state.data.log.get()[0];
  const saved = {
    role: 'user' as const,
    content: 'saved but step failed',
    timestamp: '2026-10-24T20:01:00.000Z',
    _status: 'failed' as const,
    _retry: { persistence: 'saved' as const, options: { model: 'retry-model' } },
  };
  state.data.log.set([original, saved]);
  resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
  await waitFor(() => expect(get).toHaveBeenCalledWith(id));
  const pending = {
    role: 'user' as const,
    content: 'send started during GET',
    timestamp: '2026-10-24T20:02:00Z',
    _status: 'pending' as const,
  };
  const persistedDuringGet = {
    role: 'user' as const,
    content: 'saved after GET snapshot',
    timestamp: '2026-10-24T20:01:30Z',
    _status: 'sent' as const,
  };
  const responseDuringGet = {
    role: 'assistant' as const,
    content: 'response after GET snapshot',
    timestamp: '2026-10-24T20:01:40Z',
  };
  state.data.log.set([...state.data.log.get(), persistedDuringGet, responseDuringGet, pending]);
  const attached = { ...original, files: ['attachments/one.txt'], _status: 'sent' as const };
  resolveGet({
    ...state.data.get(),
    log: [
      // Server timestamps need not have identical string formatting.
      { role: saved.role, content: saved.content, timestamp: '2026-10-24T20:01:00+00:00' },
      attached,
    ],
  });
  await creation;
  expect(state.data.log.get()).toEqual([
    saved,
    attached,
    persistedDuringGet,
    responseDuringGet,
    pending,
  ]);
  expect(state.isGenerating.get()).toBe(true);
});

it('does not mark a saved upload failed or idle newer generation when order refresh fails', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  const post = jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  jest.spyOn(mockClient, 'getConversation').mockRejectedValue(new Error('offline'));
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const state = conversations$.get(id)!;
  const original = state.data.log.get()[0];
  const newer = { role: 'user' as const, content: 'newer', timestamp: '2099-01-01T00:00:00Z' };
  state.data.log.set([original, newer]);
  resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
  await creation;
  expect(state.data.log.get()).toEqual([
    newer,
    { ...original, files: ['attachments/one.txt'], _status: 'sent' },
  ]);
  expect(post).toHaveBeenCalledTimes(1);
  expect(state.isGenerating.get()).toBe(true);
  const { result } = renderHook(() => useConversation(id));
  const edit = jest.spyOn(mockClient, 'editMessage');
  const remove = jest.spyOn(mockClient, 'deleteMessage');
  await act(async () => {
    await result.current.editMessage(1, 'would target an unseen assistant');
    await result.current.deleteMessage(1);
  });
  expect(edit).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
  expect(state.historyOrderUncertain.get()).toBe(true);
  // A deliberate authoritative refresh restores indices without reposting.
  const assistant = { role: 'assistant' as const, content: 'response' };
  const attached = { ...original, files: ['attachments/one.txt'], _status: 'sent' as const };
  updateConversationData(id, { ...state.data.get(), log: [newer, assistant, attached] });
  remove.mockResolvedValue({ ...state.data.get(), log: [newer, assistant] });
  await act(async () => {
    await result.current.deleteMessage(2);
  });
  expect(remove).toHaveBeenCalledWith(id, 2);
  expect(post).toHaveBeenCalledTimes(1);
});

it('keeps positional mutations blocked when both order snapshots become stale', async () => {
  const { renderHook } = await import('@testing-library/react');
  const state = conversations$.get('upload-existing')!;
  const message = { role: 'user' as const, content: 'saved', timestamp: '2026-10-24T20:00:00Z' };
  replaceLog('upload-existing', [message]);
  const get = jest.spyOn(mockClient, 'getConversation').mockImplementation(async () => {
    const snapshot = { ...state.data.get(), log: [message] };
    replaceLog('upload-existing', [message, { role: 'assistant', content: 'new arrival' }]);
    return snapshot;
  });
  await mockClient.reconcileLateUploadOrder('upload-existing');
  expect(get).toHaveBeenCalledTimes(2);
  expect(state.historyOrderUncertain.get()).toBe(true);
  const { result } = renderHook(() => useConversation('upload-existing'));
  const remove = jest.spyOn(mockClient, 'deleteMessage');
  await act(async () => {
    await result.current.deleteMessage(0);
  });
  expect(remove).not.toHaveBeenCalled();
});

it('keeps newer saved messages when an earlier new-chat POST fails after upload', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  const post = jest
    .spyOn(mockClient, 'sendMessage')
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new ApiClientError('rejected', 422));
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const original = conversations$.get(id)!.data.log.get()[0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
    await creation;
  });
  const log = conversations$.get(id)!.data.log.get();
  expect(log).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        content: original.content,
        timestamp: original.timestamp,
        files: ['attachments/one.txt'],
        _status: 'failed',
        _retry: expect.objectContaining({ persistence: 'rejected' }),
      }),
      expect.objectContaining({
        content: 'newer saved message',
        _status: 'sent',
      }),
    ])
  );
  expect(log).toHaveLength(2);
  expect(post).toHaveBeenCalledTimes(2);
  expect(conversations$.get(id)!.isGenerating.get()).toBe(false);
});

it('does not clear a newer generation when an earlier new-chat upload fails', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let rejectUpload!: (error: unknown) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectUpload = reject;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('never saved', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  const rejection = expect(creation).rejects.toThrow(AttachmentUploadError);
  await waitFor(() => expect(rejectUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved message' });
  });
  setGenerating(id, true);
  await act(async () => {
    rejectUpload(new ApiClientError('Too large', 413));
    await rejection;
  });
  expect(conversations$.get(id)!.isGenerating.get()).toBe(true);
  expect(conversations$.get(id)!.data.log.get()).toEqual([
    expect.objectContaining({ content: 'newer saved message', _status: 'sent' }),
  ]);
});

it('releases an old upload lock on conversation change without unlocking a newer upload', async () => {
  let firstOptions: any;
  let secondOptions: any;
  let finishFirst!: () => void;
  let finishSecond!: () => void;
  const first = new Promise<void>((resolve) => {
    finishFirst = resolve;
  });
  const second = new Promise<void>((resolve) => {
    finishSecond = resolve;
  });
  const onSend = jest
    .fn()
    .mockImplementationOnce((_message, options) => {
      firstOptions = options;
      return first;
    })
    .mockImplementationOnce((_message, options) => {
      secondOptions = options;
      return second;
    });
  const { container, rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={onSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'first draft' } });
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['one'], 'one.txt')] },
  });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(input).toBeDisabled();
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={onSend} />
  );
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: 'second draft' } });
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['two'], 'two.txt')] },
  });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(input).toBeDisabled();
  await act(async () => {
    firstOptions.onFilesUploaded();
    finishFirst();
  });
  expect(input).toBeDisabled();
  expect(input).toHaveValue('second draft');
  expect(screen.getByText('two.txt')).toBeInTheDocument();
  await act(async () => {
    secondOptions.onFilesUploaded();
    finishSecond();
  });
  expect(input).toBeEnabled();
  expect(input).toHaveValue('');
});

it('retains an earlier direct 409 when a newer send has already started', async () => {
  let rejectFirst!: (error: unknown) => void;
  const first = new Promise<void>((_resolve, reject) => {
    rejectFirst = reject;
  });
  const onSend = jest.fn().mockReturnValueOnce(first).mockResolvedValue(undefined);
  render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={onSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'first pending' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  fireEvent.change(input, { target: { value: 'second send' } });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  await act(async () => {
    rejectFirst(new ApiClientError('Busy', 409));
  });
  expect(screen.getByTitle('first pending')).toBeInTheDocument();
});

it('does not requeue a rejected upload into a different conversation', async () => {
  let rejectUpload!: (error: unknown) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockReturnValue(
    new Promise((_resolve, reject) => {
      rejectUpload = reject;
    })
  );
  const onSend = jest.fn(async (_message, options) => {
    await mockClient.uploadFiles('upload-existing', options.pendingFiles);
  });
  const state = conversations$.get('upload-existing')!;
  state.isGenerating.set(true);
  const { container, rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={onSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'private queued draft' } });
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['one'], 'one.txt')] },
  });
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  await act(async () => {
    state.isGenerating.set(false);
  });
  await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={onSend} />
  );
  await act(async () => {
    rejectUpload(new AttachmentUploadError(new Error('Rejected')));
  });
  expect(screen.queryByRole('button', { name: 'Retry upload' })).not.toBeInTheDocument();
  expect(screen.queryByText('private queued draft')).not.toBeInTheDocument();
});

it('sends a restored queue after returning to an idle conversation', async () => {
  conversations$.get('upload-existing')!.isGenerating.set(true);
  initConversation(
    'other-upload',
    {
      id: 'other-upload',
      name: 'Other',
      log: [],
      logfile: 'other-upload',
      branches: {},
      workspace: '.',
    },
    {}
  );
  const oldSend = jest.fn();
  const newSend = jest.fn();
  const { rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'queued while busy' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(oldSend).not.toHaveBeenCalled();
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={newSend} />
  );
  expect(newSend).not.toHaveBeenCalled();
  conversations$.get('upload-existing')!.isGenerating.set(false);
  rerender(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  await waitFor(() => expect(oldSend).toHaveBeenCalledTimes(1));
  expect(oldSend).toHaveBeenCalledWith('queued while busy', expect.any(Object));
  expect(newSend).not.toHaveBeenCalled();
});

it('does not flush the previous conversation queue on a busy-to-idle conversation switch', async () => {
  conversations$.get('upload-existing')!.isGenerating.set(true);
  const oldSend = jest.fn();
  const newSend = jest.fn();
  const { rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'private queued text' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={newSend} />
  );
  expect(newSend).not.toHaveBeenCalled();
});

it('does not clear a controlled replacement draft after the original composer unmounts', async () => {
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  function Parent({ server }: { server: string }) {
    const [value, setValue] = useState('original draft');
    return (
      <ChatInput
        key={server}
        value={value}
        onChange={setValue}
        autoFocus$={observable(false)}
        onSend={() => pending}
      />
    );
  }
  const { container, rerender } = render(<Parent server="a" />);
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['one'], 'one.txt')] },
  });
  fireEvent.keyDown(screen.getByRole('textbox', { name: 'Chat message' }), { key: 'Enter' });
  rerender(<Parent server="b" />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Chat message' }), {
    target: { value: 'new server draft' },
  });
  await act(async () => {
    finish();
  });
  expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue('new server draft');
});

it('keeps a new-chat failed message available when deliberate Retry also returns 409', async () => {
  const { renderHook } = await import('@testing-library/react');
  jest
    .spyOn(mockClient, 'uploadFiles')
    .mockResolvedValue({ files: [{ path: 'attachments/retry.txt' }] } as any);
  const post = jest
    .spyOn(mockClient, 'sendMessage')
    .mockRejectedValue(new ApiClientError('busy', 409));
  const id = await mockClient.createConversationWithPlaceholder('retry while busy', {
    pendingFiles: [new File(['retry'], 'retry.txt')],
  });
  const failed = conversations$.get(id)!.data.log.get().at(-1)!;
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.retryMessage(failed);
  });
  expect(conversations$.get(id)!.data.log.get()).toEqual([
    expect.objectContaining({
      content: 'retry while busy',
      files: ['attachments/retry.txt'],
      _status: 'failed',
      _retry: { persistence: 'rejected', options: expect.any(Object) },
    }),
  ]);
  expect(post).toHaveBeenCalledTimes(2);
});

it('retains a direct-send 409 in its owning queue after switching conversations', async () => {
  let rejectFirst!: (error: unknown) => void;
  const first = new Promise<void>((_resolve, reject) => {
    rejectFirst = reject;
  });
  const oldSend = jest.fn().mockReturnValueOnce(first).mockResolvedValue(undefined);
  const newSend = jest.fn();
  const { rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'direct original' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={newSend} />
  );
  await act(async () => {
    rejectFirst(new ApiClientError('Busy', 409));
  });
  expect(newSend).not.toHaveBeenCalled();
  expect(screen.queryByTitle('direct original')).not.toBeInTheDocument();
  rerender(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  await waitFor(() => expect(oldSend).toHaveBeenCalledTimes(2));
  expect(oldSend.mock.calls[1][0]).toBe('direct original');
  expect(newSend).not.toHaveBeenCalled();
});

it('retries a late direct 409 after returning to the owning conversation before rejection', async () => {
  let rejectFirst!: (error: unknown) => void;
  const first = new Promise<void>((_resolve, reject) => {
    rejectFirst = reject;
  });
  const oldSend = jest.fn().mockReturnValueOnce(first).mockResolvedValue(undefined);
  const newSend = jest.fn();
  const { rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'late original' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={newSend} />
  );
  rerender(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Chat message' }), {
    target: { value: 'new draft must survive' },
  });
  await act(async () => {
    rejectFirst(new ApiClientError('Busy', 409));
  });
  await waitFor(() => expect(oldSend).toHaveBeenCalledTimes(2), { timeout: 2500 });
  expect(oldSend.mock.calls[1][0]).toBe('late original');
  expect(newSend).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue(
    'new draft must survive'
  );
});

it('retries a queued 409 after returning to idle A before rejection', async () => {
  let rejectQueued!: (error: unknown) => void;
  const queued = new Promise<void>((_resolve, reject) => {
    rejectQueued = reject;
  });
  const oldSend = jest.fn().mockReturnValueOnce(queued).mockResolvedValue(undefined);
  const newSend = jest.fn();
  conversations$.get('upload-existing')!.isGenerating.set(true);
  initConversation(
    'other-upload',
    {
      id: 'other-upload',
      name: 'Other',
      log: [],
      logfile: 'other-upload',
      branches: {},
      workspace: '.',
    },
    {}
  );
  const { rerender } = render(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  const input = screen.getByRole('textbox', { name: 'Chat message' });
  fireEvent.change(input, { target: { value: 'queued original' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(oldSend).not.toHaveBeenCalled();
  await act(async () => {
    conversations$.get('upload-existing')!.isGenerating.set(false);
  });
  await waitFor(() => expect(oldSend).toHaveBeenCalledTimes(1));
  expect(oldSend.mock.calls[0][0]).toBe('queued original');
  rerender(
    <ChatInput conversationId="other-upload" autoFocus$={observable(false)} onSend={newSend} />
  );
  rerender(
    <ChatInput conversationId="upload-existing" autoFocus$={observable(false)} onSend={oldSend} />
  );
  await act(async () => {
    rejectQueued(new ApiClientError('Busy', 409));
  });
  await waitFor(() => expect(oldSend).toHaveBeenCalledTimes(2), { timeout: 2500 });
  expect(oldSend.mock.calls[1][0]).toBe('queued original');
  expect(newSend).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Retry upload' })).not.toBeInTheDocument();
});

it.each(['rejected', 'unknown'] as const)(
  'withholds positional mutations while a late attachment POST is %s',
  async (persistence) => {
    const { renderHook } = await import('@testing-library/react');
    const create = jest.spyOn(mockClient, 'createConversation');
    let resolveUpload!: (value: any) => void;
    jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        })
    );
    jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
    jest
      .spyOn(mockClient, 'sendMessage')
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new ApiClientError('failed', persistence === 'rejected' ? 422 : 500));
    const creation = mockClient.createConversationWithPlaceholder('original attached', {
      pendingFiles: [new File(['one'], 'one.txt')],
    });
    await waitFor(() => expect(resolveUpload).toBeDefined());
    const id = create.mock.calls[0][0];
    const { result } = renderHook(() => useConversation(id));
    await act(async () => {
      await result.current.interruptGeneration();
      await result.current.sendMessage({ message: 'newer saved message' });
    });
    await act(async () => {
      resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
      await creation;
    });
    const log = conversations$.get(id)!.data.log.get();
    expect(log[0]._retry?.persistence).toBe(persistence);
    const response = { ...conversations$.get(id)!.data.get(), log };
    const edit = jest.spyOn(mockClient, 'editMessage').mockResolvedValue(response);
    const remove = jest.spyOn(mockClient, 'deleteMessage').mockResolvedValue(response);
    const step = jest.spyOn(mockClient, 'step');
    step.mockClear();
    const rerun = jest.spyOn(mockClient, 'rerunTools');
    await act(async () => {
      expect(await result.current.editMessage(0, 'must not edit the saved message')).toBe(false);
      expect(await result.current.editMessage(1, 'must not use shifted saved index')).toBe(false);
      await result.current.deleteMessage(0);
      await result.current.deleteMessage(1);
      await result.current.regenerateMessage(1);
      await result.current.rerunFromMessage(0);
    });
    expect(edit).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
    expect(step).not.toHaveBeenCalled();
    expect(rerun).not.toHaveBeenCalled();
    expect(conversations$.get(id)!.data.log.get()).toEqual(log);
  }
);

it('keeps sub-millisecond distinct entries and their own Retry metadata during reconciliation', async () => {
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('original attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const state = conversations$.get(id)!;
  const original = state.data.log.get()[0];
  const earlier = {
    role: 'assistant' as const,
    content: 'earlier',
    timestamp: '2026-10-24T20:01:00.000100Z',
  };
  const later = {
    role: 'user' as const,
    content: 'later',
    timestamp: '2026-10-24T20:01:00.000900Z',
    _status: 'failed' as const,
    _retry: { persistence: 'saved' as const, options: { model: 'later-model' } },
  };
  state.data.log.set([original, later, earlier]);
  const attached = { ...original, files: ['attachments/one.txt'], _status: 'sent' as const };
  jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...state.data.get(),
    log: [earlier, attached],
  });
  resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
  await creation;
  expect(state.data.log.get()).toEqual([earlier, attached, later]);
  expect(state.data.log.get()[0]._retry).toBeUndefined();
});

it('blocks positional mutations while the initial attachment upload is pending', async () => {
  const { renderHook } = await import('@testing-library/react');
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('pending attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const { result } = renderHook(() => useConversation(id));
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved' });
  });
  const response = conversations$.get(id)!.data.get();
  const edit = jest.spyOn(mockClient, 'editMessage').mockResolvedValue(response);
  const remove = jest.spyOn(mockClient, 'deleteMessage').mockResolvedValue(response);
  await act(async () => {
    expect(await result.current.editMessage(1, 'wrong index')).toBe(false);
    await result.current.deleteMessage(1);
  });
  expect(edit).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
    await creation;
  });
});

it('keeps live content when an order snapshot has the same assistant identity', async () => {
  const state = conversations$.get('upload-existing')!;
  const assistant = {
    role: 'assistant' as const,
    content: 'first second',
    timestamp: '2026-10-27T12:00:00Z',
    isComplete: false,
  };
  replaceLog('upload-existing', [assistant]);
  jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...state.data.get(),
    log: [{ ...assistant, content: 'first' }],
  });
  await mockClient.reconcileLateUploadOrder('upload-existing');
  expect(state.data.log.get()[0].content).toBe('first second');
  expect(state.historyOrderUncertain.get()).toBe(false);
});

it('keeps timestamp-less rows unchanged and positional mutations blocked', async () => {
  const state = conversations$.get('upload-existing')!;
  const instruction = { role: 'system' as const, content: 'Instructions' };
  replaceLog('upload-existing', [instruction]);
  jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...state.data.get(),
    log: [instruction],
  });
  await mockClient.reconcileLateUploadOrder('upload-existing');
  expect(state.data.log.get()).toEqual([instruction]);
  expect(state.historyOrderUncertain.get()).toBe(true);
});

it('defers order reconciliation when the live assistant has a different server identity', async () => {
  const { renderHook } = await import('@testing-library/react');
  let handlers: any;
  jest.spyOn(mockClient, 'subscribeToEvents').mockImplementation(async (_id, callbacks) => {
    handlers = callbacks;
    callbacks.onConnected?.();
  });
  renderHook(() => useConversation('upload-existing'));
  await waitFor(() => expect(handlers).toBeDefined());
  await act(async () => {
    handlers.onMessageStart();
    handlers.onToken('first');
  });
  const state = conversations$.get('upload-existing')!;
  const assistant = state.data.log.get()[0];
  const serverAssistant = {
    ...assistant,
    timestamp: new Date(Date.parse(assistant.timestamp!) - 3).toISOString(),
    content: 'stale snapshot',
  };
  const get = jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...state.data.get(),
    log: [serverAssistant],
  });
  await act(async () => {
    await mockClient.reconcileLateUploadOrder('upload-existing');
    handlers.onToken(' second');
  });
  expect(state.data.log.get()).toHaveLength(1);
  expect(state.data.log.get()[0].content).toBe('first second');
  expect(state.historyOrderUncertain.get()).toBe(true);
  await act(async () => {
    handlers.onMessageComplete({ ...serverAssistant, content: 'final response' });
    handlers.onStepComplete();
  });
  get.mockResolvedValue({
    ...state.data.get(),
    log: [{ ...serverAssistant, content: 'final response' }],
  });
  await act(async () => {
    await mockClient.reconcileLateUploadOrder('upload-existing');
  });
  expect(state.data.log.get()).toHaveLength(1);
  expect(state.data.log.get()[0].content).toBe('final response');
  expect(state.historyOrderUncertain.get()).toBe(false);
});

it('keeps streaming tokens and completion on their assistant after late upload reorder', async () => {
  const { renderHook } = await import('@testing-library/react');
  // A second mounted client can already have a live stream while this client uploads.
  jest.spyOn(mockClient, 'waitForConversationCreation').mockResolvedValue(undefined);
  let handlers: any;
  jest.spyOn(mockClient, 'subscribeToEvents').mockImplementation(async (_id, callbacks) => {
    handlers = callbacks;
    callbacks.onConnected?.();
  });
  const create = jest.spyOn(mockClient, 'createConversation');
  let resolveUpload!: (value: any) => void;
  jest.spyOn(mockClient, 'uploadFiles').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveUpload = resolve;
      })
  );
  jest.spyOn(mockClient, 'interruptGeneration').mockResolvedValue(undefined);
  const post = jest.spyOn(mockClient, 'sendMessage').mockResolvedValue(undefined);
  const creation = mockClient.createConversationWithPlaceholder('pending attached', {
    pendingFiles: [new File(['one'], 'one.txt')],
  });
  await waitFor(() => expect(resolveUpload).toBeDefined());
  const id = create.mock.calls[0][0];
  const original = conversations$.get(id)!.data.log.get()[0];
  const { result } = renderHook(() => useConversation(id));
  await waitFor(() => expect(handlers).toBeDefined());
  await act(async () => {
    await result.current.interruptGeneration();
    await result.current.sendMessage({ message: 'newer saved' });
    handlers.onMessageStart();
    handlers.onToken('first');
  });
  const assistant = conversations$
    .get(id)!
    .data.log.get()
    .find((m) => m.role === 'assistant')!;
  expect(assistant.content).toBe('first');
  const attached = { ...original, files: ['attachments/one.txt'] };
  jest.spyOn(mockClient, 'getConversation').mockResolvedValue({
    ...conversations$.get(id)!.data.get(),
    log: [post.mock.calls[0][1], assistant, attached],
  });
  await act(async () => {
    resolveUpload({ files: [{ path: 'attachments/one.txt' }] });
    await creation;
    handlers.onToken(' second');
  });
  expect(
    conversations$
      .get(id)!
      .data.log.get()
      .find((m) => m.role === 'assistant')!.content
  ).toBe('first second');
  await act(async () => {
    handlers.onMessageComplete({ ...assistant, content: 'final response' });
  });
  const log = conversations$.get(id)!.data.log.get();
  expect(log.find((m) => m.role === 'assistant')!.content).toBe('final response');
  expect(log[log.length - 1]).toMatchObject({ role: 'user', content: 'pending attached' });
});
