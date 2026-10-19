import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { useState } from 'react';
import { ChatInput } from '../ChatInput';
import { useConversation } from '@/hooks/useConversation';
import { ApiClient, ApiClientError } from '@/utils/api';
import { conversations$, initConversation } from '@/stores/conversations';

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
jest.mock('@/utils/audio', () => ({ playChime: jest.fn() }));
jest.mock('@/utils/notifications', () => ({
  notifyGenerationComplete: jest.fn(),
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
