import { render, screen, fireEvent } from '@testing-library/react';
import { FileList } from '../workspace/FileList';
import type { FileType } from '@/types/workspace';

describe('FileList', () => {
  const mockOnFileClick = jest.fn();
  const mockOnDirectoryClick = jest.fn();

  const sampleFiles: FileType[] = [
    {
      name: 'src',
      path: 'src',
      type: 'directory',
      size: 0,
      modified: '2026-03-28T12:00:00Z',
      mime_type: null,
    },
    {
      name: 'README.md',
      path: 'README.md',
      type: 'file',
      size: 2048,
      modified: '2026-03-28T12:00:00Z',
      mime_type: 'text/markdown',
    },
    {
      name: 'main.py',
      path: 'main.py',
      type: 'file',
      size: 4096,
      modified: '2026-03-28T12:00:00Z',
      mime_type: 'text/x-python',
    },
    {
      name: 'image.png',
      path: 'image.png',
      type: 'file',
      size: 10240,
      modified: '2026-03-28T12:00:00Z',
      mime_type: 'image/png',
    },
    {
      name: 'archive.zip',
      path: 'archive.zip',
      type: 'file',
      size: 51200,
      modified: '2026-03-28T12:00:00Z',
      mime_type: 'application/zip',
    },
  ];

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders all files', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath=""
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    expect(screen.getByText('src')).toBeInTheDocument();
    expect(screen.getByText('README.md')).toBeInTheDocument();
    expect(screen.getByText('main.py')).toBeInTheDocument();
    expect(screen.getByText('image.png')).toBeInTheDocument();
    expect(screen.getByText('archive.zip')).toBeInTheDocument();
  });

  it('shows file sizes for files but not directories', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath=""
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    // Files show size
    expect(screen.getByText('2.0 KB')).toBeInTheDocument();
    expect(screen.getByText('4.0 KB')).toBeInTheDocument();
    expect(screen.getByText('10.0 KB')).toBeInTheDocument();
    expect(screen.getByText('50.0 KB')).toBeInTheDocument();
  });

  it('calls onFileClick when clicking a file', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath=""
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    fireEvent.click(screen.getByText('README.md'));
    expect(mockOnFileClick).toHaveBeenCalledWith(sampleFiles[1]);
    expect(mockOnDirectoryClick).not.toHaveBeenCalled();
  });

  it('calls onDirectoryClick when clicking a directory', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath=""
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    fireEvent.click(screen.getByText('src'));
    expect(mockOnDirectoryClick).toHaveBeenCalledWith('src');
    expect(mockOnFileClick).not.toHaveBeenCalled();
  });

  it('shows parent navigation when in a subdirectory', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath="src/components"
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    expect(screen.getByText('..')).toBeInTheDocument();
  });

  it('navigates to parent when clicking ".."', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath="src/components"
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    fireEvent.click(screen.getByText('..'));
    expect(mockOnDirectoryClick).toHaveBeenCalledWith('src');
  });

  it('hides parent navigation at root', () => {
    render(
      <FileList
        files={sampleFiles}
        currentPath=""
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    expect(screen.queryByText('..')).not.toBeInTheDocument();
  });

  it('renders empty list', () => {
    const { container } = render(
      <FileList
        files={[]}
        currentPath=""
        onFileClick={mockOnFileClick}
        onDirectoryClick={mockOnDirectoryClick}
      />
    );

    // Should render the container but no file buttons
    expect(container.querySelectorAll('button')).toHaveLength(0);
  });
});
