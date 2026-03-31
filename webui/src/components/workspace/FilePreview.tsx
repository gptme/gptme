import { useEffect, useState, useCallback } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useWorkspaceApi } from '@/utils/workspaceApi';
import type { FileType, FilePreview } from '@/types/workspace';
import type { WorkspaceRoot } from '@/stores/workspaceExplorer';
import { CodeDisplay } from '@/components/CodeDisplay';
import { Button } from '@/components/ui/button';
import { MarkdownPreviewTabs } from './MarkdownPreviewTabs';

// Helper function to check if a file is a markdown file
function isMarkdownFileType(file: FileType): boolean {
  const fileName = file.name.toLowerCase();
  return (
    fileName.endsWith('.md') || fileName.endsWith('.markdown') || file.mime_type === 'text/markdown'
  );
}

function FileHeader({
  file,
  onDownload,
  downloadError,
}: {
  file: FileType;
  onDownload: () => void;
  downloadError?: string | null;
}) {
  return (
    <div className="flex flex-col border-b">
      <div className="flex items-start justify-between p-2">
        <div>
          <h3 className="font-medium">{file.name}</h3>
          <div className="space-y-1 text-sm text-muted-foreground">
            <div>
              {(file.size / 1024).toFixed(1)} KB • {file.mime_type || 'Unknown type'}
            </div>
            <div>Modified {formatDistanceToNow(new Date(file.modified), { addSuffix: true })}</div>
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          title="Download file"
          aria-label="Download file"
          onClick={onDownload}
        >
          <Download className="h-4 w-4" />
        </Button>
      </div>
      {downloadError && <div className="px-2 pb-2 text-sm text-destructive">{downloadError}</div>}
    </div>
  );
}

interface FilePreviewProps {
  file: FileType;
  conversationId: string;
  root?: WorkspaceRoot;
}

export function FilePreview({ file, conversationId, root = 'workspace' }: FilePreviewProps) {
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const { previewFile, downloadFile } = useWorkspaceApi();

  const loadPreview = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setDownloadError(null);
      const data = await previewFile(conversationId, file.path, root);
      setPreview(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load preview');
    } finally {
      setLoading(false);
    }
  }, [file.path, conversationId, previewFile, root]);

  const handleDownload = useCallback(async () => {
    try {
      setDownloadError(null);
      await downloadFile(conversationId, file.path, root);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed');
    }
  }, [downloadFile, conversationId, file.path, root]);

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

  const isMarkdownFile = isMarkdownFileType(file);

  switch (preview.type) {
    case 'text':
      return (
        <div className="flex h-full flex-col">
          <FileHeader file={file} onDownload={handleDownload} downloadError={downloadError} />
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
          <FileHeader file={file} onDownload={handleDownload} downloadError={downloadError} />
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
          <FileHeader file={file} onDownload={handleDownload} downloadError={downloadError} />
          <div className="flex flex-1 items-center justify-center">
            <p className="text-muted-foreground">Binary file — use the download button above</p>
          </div>
        </div>
      );
    default:
      return null;
  }
}
