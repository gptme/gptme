import { useEffect, useState, useCallback } from 'react';
import { Loader2 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useWorkspaceApi } from '@/utils/workspaceApi';
import type { FileType, FilePreview } from '@/types/workspace';
import { CodeDisplay } from '@/components/CodeDisplay';
import { MarkdownPreviewTabs } from './MarkdownPreviewTabs';

// Helper function to check if a file is a markdown file
function isMarkdownFileType(file: FileType): boolean {
  const fileName = file.name.toLowerCase();
  return (
    fileName.endsWith('.md') || fileName.endsWith('.markdown') || file.mime_type === 'text/markdown'
  );
}

interface FilePreviewProps {
  file: FileType;
  conversationId: string;
}

export function FilePreview({ file, conversationId }: FilePreviewProps) {
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const { previewFile } = useWorkspaceApi();

  const loadPreview = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await previewFile(conversationId, file.path);
      setPreview(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load preview');
    } finally {
      setLoading(false);
    }
  }, [file.path, conversationId, previewFile]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (error) {
    return <div className="flex h-full items-center justify-center text-destructive">{error}</div>;
  }

  if (!preview) {
    return null;
  }

  // Check if this is a markdown file
  const isMarkdownFile = isMarkdownFileType(file);

  switch (preview.type) {
    case 'text':
      return (
        <div className="flex h-full flex-col">
          <div className="border-b p-2">
            <h3 className="font-medium">{file.name}</h3>
            <div className="space-y-1 text-sm text-muted-foreground">
              <div>
                {(file.size / 1024).toFixed(1)} KB • {file.mime_type || 'Unknown type'}
              </div>
              <div>
                Modified {formatDistanceToNow(new Date(file.modified), { addSuffix: true })}
              </div>
            </div>
          </div>
          <div className="flex-1 overflow-auto">
            {isMarkdownFile ? (
              <MarkdownPreviewTabs
                content={preview.content}
                language={file.mime_type?.split('/')[1] || 'markdown'}
              />
            ) : (
              <CodeDisplay
                code={preview.content}
                language={file.mime_type?.split('/')[1] || 'plaintext'}
                maxHeight="none"
              />
            )}
          </div>
        </div>
      );
    case 'image':
      return (
        <div className="flex h-full flex-col">
          <div className="border-b p-2">
            <h3 className="font-medium">{file.name}</h3>
            <div className="space-y-1 text-sm text-muted-foreground">
              <div>
                {(file.size / 1024).toFixed(1)} KB • {file.mime_type || 'Unknown type'}
              </div>
              <div>
                Modified {formatDistanceToNow(new Date(file.modified), { addSuffix: true })}
              </div>
            </div>
          </div>
          <div className="flex flex-1 items-center justify-center overflow-auto p-4">
            <img
              src={preview.content}
              alt={file.name}
              className="max-h-full max-w-full object-contain"
            />
          </div>
        </div>
      );
    case 'binary':
      return (
        <div className="flex h-full flex-col">
          <div className="border-b p-2">
            <h3 className="font-medium">{file.name}</h3>
            <div className="space-y-1 text-sm text-muted-foreground">
              <div>
                {(file.size / 1024).toFixed(1)} KB • {file.mime_type || 'Unknown type'}
              </div>
              <div>
                Modified {formatDistanceToNow(new Date(file.modified), { addSuffix: true })}
              </div>
            </div>
          </div>
          <div className="flex flex-1 items-center justify-center">
            <p className="text-muted-foreground">Binary file</p>
          </div>
        </div>
      );
    default:
      return null;
  }
}
