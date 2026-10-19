import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { WorkspaceExplorer } from '../workspace/WorkspaceExplorer';
import type { FileType } from '@/types/workspace';

const mockListWorkspace = jest.fn();

jest.mock('@/utils/workspaceApi', () => ({
  useWorkspaceApi: () => ({ listWorkspace: mockListWorkspace }),
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { getChatConfig: jest.fn().mockResolvedValue({ chat: { workspace: '/ws' } }) },
  }),
}));

jest.mock('../workspace/FilePreview', () => ({
  FilePreview: () => <div data-testid="file-preview" />,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function file(name: string): FileType {
  return {
    name,
    path: name,
    type: 'file',
    size: 1,
    modified: '2026-10-19T12:00:00Z',
    mime_type: 'text/plain',
  };
}

describe('WorkspaceExplorer request ownership', () => {
  beforeEach(() => {
    mockListWorkspace.mockReset();
  });

  function startWorkspaceThenAttachments() {
    const workspace = deferred<FileType[]>();
    const attachments = deferred<FileType[]>();
    mockListWorkspace.mockImplementation(
      (_id: string, _path: string, _hidden: boolean, root: string) =>
        root === 'attachments' ? attachments.promise : workspace.promise
    );
    render(<WorkspaceExplorer conversationId="conv-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Attachments' }));
    return { workspace, attachments };
  }

  it('ignores an older root response that resolves after the selected root', async () => {
    const { workspace, attachments } = startWorkspaceThenAttachments();

    await act(async () => attachments.resolve([file('attachment.txt')]));
    expect(await screen.findByText('attachment.txt')).toBeInTheDocument();

    await act(async () => workspace.resolve([file('wrong-workspace-file.txt')]));
    expect(screen.getByText('attachment.txt')).toBeInTheDocument();
    expect(screen.queryByText('wrong-workspace-file.txt')).not.toBeInTheDocument();
  });

  it('keeps loading while the selected root is pending after an older response settles', async () => {
    const { workspace, attachments } = startWorkspaceThenAttachments();

    expect(screen.getByRole('status', { name: 'Loading files' })).toBeInTheDocument();

    await act(async () => workspace.resolve([file('wrong-workspace-file.txt')]));
    expect(screen.queryByText('wrong-workspace-file.txt')).not.toBeInTheDocument();
    // The stale response must not clear the loading state of the selected root.
    expect(screen.getByRole('status', { name: 'Loading files' })).toBeInTheDocument();

    await act(async () => attachments.resolve([file('attachment.txt')]));
    expect(await screen.findByText('attachment.txt')).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'Loading files' })).not.toBeInTheDocument();
  });

  it('does not show a stale request failure for the selected root', async () => {
    const { workspace, attachments } = startWorkspaceThenAttachments();
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});

    await act(async () => attachments.resolve([file('attachment.txt')]));
    await act(async () => workspace.reject(new Error('stale workspace failure')));

    await waitFor(() => expect(screen.getByText('attachment.txt')).toBeInTheDocument());
    expect(screen.queryByText('stale workspace failure')).not.toBeInTheDocument();
    consoleError.mockRestore();
  });
});
