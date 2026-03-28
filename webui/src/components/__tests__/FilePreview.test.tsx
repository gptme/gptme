import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FilePreview } from '../workspace/FilePreview';
import type { FileType } from '@/types/workspace';

// Mock the workspace API
const mockPreviewFile = jest.fn();
const mockDownloadFile = jest.fn();

jest.mock('@/utils/workspaceApi', () => ({
  useWorkspaceApi: () => ({
    previewFile: mockPreviewFile,
    downloadFile: mockDownloadFile,
  }),
}));

// Mock CodeDisplay (avoid complex rendering)
jest.mock('@/components/CodeDisplay', () => ({
  CodeDisplay: ({ code, language }: { code: string; language: string }) => (
    <pre data-testid="code-display" data-language={language}>
      {code}
    </pre>
  ),
}));

// Mock MarkdownPreviewTabs
jest.mock('../workspace/MarkdownPreviewTabs', () => ({
  MarkdownPreviewTabs: ({ content }: { content: string }) => (
    <div data-testid="markdown-preview">{content}</div>
  ),
}));

// Mock lucide-react icons
jest.mock('lucide-react', () => ({
  Download: () => <span data-testid="download-icon">Download</span>,
  Loader2: ({ className }: { className: string }) => (
    <span data-testid="loader" className={className}>
      Loading
    </span>
  ),
}));

// Mock the Button component
jest.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    title,
    ...props
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    title?: string;
    variant?: string;
    size?: string;
  }) => (
    <button onClick={onClick} title={title} {...props}>
      {children}
    </button>
  ),
}));

describe('FilePreview', () => {
  const textFile: FileType = {
    name: 'example.py',
    path: 'src/example.py',
    type: 'file',
    size: 1024,
    modified: '2026-03-28T12:00:00Z',
    mime_type: 'text/x-python',
  };

  const markdownFile: FileType = {
    name: 'README.md',
    path: 'README.md',
    type: 'file',
    size: 2048,
    modified: '2026-03-28T12:00:00Z',
    mime_type: 'text/markdown',
  };

  const imageFile: FileType = {
    name: 'photo.png',
    path: 'assets/photo.png',
    type: 'file',
    size: 51200,
    modified: '2026-03-28T12:00:00Z',
    mime_type: 'image/png',
  };

  const binaryFile: FileType = {
    name: 'data.bin',
    path: 'data.bin',
    type: 'file',
    size: 8192,
    modified: '2026-03-28T12:00:00Z',
    mime_type: 'application/octet-stream',
  };

  const conversationId = 'test-conversation';

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows loading spinner initially', () => {
    mockPreviewFile.mockReturnValue(new Promise(() => {})); // Never resolves
    render(<FilePreview file={textFile} conversationId={conversationId} />);
    expect(screen.getByTestId('loader')).toBeInTheDocument();
  });

  it('renders text file preview with code display', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: 'print("hello world")',
    });

    render(<FilePreview file={textFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTestId('code-display')).toBeInTheDocument();
    });

    expect(screen.getByText('print("hello world")')).toBeInTheDocument();
    expect(screen.getByText('example.py')).toBeInTheDocument();
    expect(screen.getByText(/1\.0 KB/)).toBeInTheDocument();
  });

  it('renders markdown file with markdown preview', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: '# Hello\n\nWorld',
    });

    render(<FilePreview file={markdownFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTestId('markdown-preview')).toBeInTheDocument();
    });

    expect(screen.getByTestId('markdown-preview')).toHaveTextContent('# Hello');
  });

  it('renders image preview', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'image',
      content: 'blob:http://localhost/test-blob',
    });

    render(<FilePreview file={imageFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByAltText('photo.png')).toBeInTheDocument();
    });

    const img = screen.getByAltText('photo.png') as HTMLImageElement;
    expect(img.src).toBe('blob:http://localhost/test-blob');
  });

  it('renders binary file message', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'binary',
      metadata: binaryFile,
    });

    render(<FilePreview file={binaryFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByText(/Binary file/)).toBeInTheDocument();
    });

    expect(screen.getByText(/download button/)).toBeInTheDocument();
  });

  it('shows error message on preview failure', async () => {
    mockPreviewFile.mockRejectedValue(new Error('File not found'));

    render(<FilePreview file={textFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByText('File not found')).toBeInTheDocument();
    });
  });

  it('shows download button in file header', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: 'content',
    });

    render(<FilePreview file={textFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTitle('Download file')).toBeInTheDocument();
    });
  });

  it('triggers download on button click', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: 'content',
    });
    mockDownloadFile.mockResolvedValue(undefined);

    render(<FilePreview file={textFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTitle('Download file')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Download file'));

    await waitFor(() => {
      expect(mockDownloadFile).toHaveBeenCalledWith(conversationId, textFile.path);
    });
  });

  it('shows download error', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: 'content',
    });
    mockDownloadFile.mockRejectedValue(new Error('Download failed'));

    render(<FilePreview file={textFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTitle('Download file')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Download file'));

    await waitFor(() => {
      expect(screen.getByText('Download failed')).toBeInTheDocument();
    });
  });

  it('clears download error on file switch', async () => {
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: 'content',
    });
    mockDownloadFile.mockRejectedValue(new Error('Download failed'));

    const { rerender } = render(<FilePreview file={textFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTitle('Download file')).toBeInTheDocument();
    });

    // Trigger a download error
    fireEvent.click(screen.getByTitle('Download file'));
    await waitFor(() => {
      expect(screen.getByText('Download failed')).toBeInTheDocument();
    });

    // Switch to a different file — download error should be cleared
    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: 'other content',
    });

    rerender(<FilePreview file={markdownFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByText('README.md')).toBeInTheDocument();
    });
    expect(screen.queryByText('Download failed')).not.toBeInTheDocument();
  });

  it('detects markdown by .markdown extension', async () => {
    const mdFile: FileType = {
      name: 'notes.markdown',
      path: 'notes.markdown',
      type: 'file',
      size: 512,
      modified: '2026-03-28T12:00:00Z',
      mime_type: null,
    };

    mockPreviewFile.mockResolvedValue({
      type: 'text',
      content: '## Notes',
    });

    render(<FilePreview file={mdFile} conversationId={conversationId} />);

    await waitFor(() => {
      expect(screen.getByTestId('markdown-preview')).toBeInTheDocument();
    });
  });
});
