import React from 'react';
import {
  FileTextIcon,
  FolderIcon,
  ArrowLeft,
  ImageIcon,
  FileCodeIcon,
  FileIcon,
  FileVideoIcon,
  FileAudioIcon,
  FileArchiveIcon,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { ScrollArea } from '@/components/ui/scroll-area';
import type { FileType } from '@/types/workspace';

// prettier-ignore
const CODE_EXTENSIONS = [
  // Web
  'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'scss', 'less',
  // Backend
  'py', 'rb', 'php', 'java', 'go', 'rs', 'cs', 'cpp', 'c', 'h',
  // Config/Data
  'json', 'yaml', 'yml', 'toml', 'xml', 'ini',
  // Shell
  'sh', 'bash', 'zsh', 'fish'
];

// prettier-ignore
const ARCHIVE_EXTENSIONS = ['zip', 'tar', 'gz', 'tgz', '7z', 'rar', 'bz2', 'xz'];

// prettier-ignore
const DOCUMENT_EXTENSIONS = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'md', 'txt'];

const getFileIcon = (file: FileType): LucideIcon => {
  // Get lowercase extension
  const ext = file.name.split('.').pop()?.toLowerCase() || '';

  // Check extension first for more specific matches
  if (CODE_EXTENSIONS.includes(ext)) {
    return FileCodeIcon;
  }
  if (ARCHIVE_EXTENSIONS.includes(ext)) {
    return FileArchiveIcon;
  }
  if (DOCUMENT_EXTENSIONS.includes(ext)) {
    return FileTextIcon;
  }

  // Then check MIME type for broader categories
  if (!file.mime_type) {
    return FileIcon;
  }

  const [type, subtype] = file.mime_type.split('/');
  switch (type) {
    case 'image':
      return ImageIcon;
    case 'video':
      return FileVideoIcon;
    case 'audio':
      return FileAudioIcon;
    case 'text':
      return FileTextIcon;
    case 'application':
      switch (subtype) {
        case 'x-archive':
        case 'zip':
        case 'x-tar':
        case 'x-gzip':
          return FileArchiveIcon;
        case 'x-httpd-php':
        case 'javascript':
        case 'typescript':
          return FileCodeIcon;
        default:
          if (subtype.includes('code') || subtype.includes('script')) {
            return FileCodeIcon;
          }
      }
  }

  // Default icon for unknown types
  return FileIcon;
};

interface FileListProps {
  files: FileType[];
  currentPath: string;
  onFileClick: (file: FileType) => void;
  onDirectoryClick: (path: string) => void;
}

export function FileList({ files, currentPath, onFileClick, onDirectoryClick }: FileListProps) {
  const goToParent = () => {
    if (!currentPath) return;
    const parentPath = currentPath.split('/').slice(0, -1).join('/');
    onDirectoryClick(parentPath);
  };

  return (
    <ScrollArea className="h-full px-2">
      <div className="space-y-1 py-2">
        {currentPath && (
          <button
            onClick={goToParent}
            className="flex w-full items-center justify-between rounded-md p-2 hover:bg-muted"
          >
            <div className="flex items-center">
              <ArrowLeft className="mr-2 h-4 w-4" />
              <span>..</span>
            </div>
          </button>
        )}
        {files.map((file) => (
          <button
            key={file.path}
            onClick={() =>
              file.type === 'directory' ? onDirectoryClick(file.path) : onFileClick(file)
            }
            className="flex w-full items-center justify-between rounded-md p-2 hover:bg-muted"
          >
            <div className="flex items-center">
              {file.type === 'directory' ? (
                <FolderIcon className="mr-2 h-4 w-4 fill-sky-300" />
              ) : (
                React.createElement(getFileIcon(file), {
                  className: 'mr-2 h-4 w-4',
                })
              )}
              <span className="text-sm">{file.name}</span>
            </div>
            {file.type === 'file' && (
              <span className="text-xs text-muted-foreground">
                {(file.size / 1024).toFixed(1)} KB
              </span>
            )}
          </button>
        ))}
      </div>
    </ScrollArea>
  );
}
