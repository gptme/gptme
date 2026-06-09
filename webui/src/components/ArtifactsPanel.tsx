import { useCallback, useEffect, useRef, useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import { FolderOpen, Loader2, Package, RefreshCw } from 'lucide-react';
import { FilePreview } from '@/components/workspace/FilePreview';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { rightSidebarActiveTab$, rightSidebarVisible$ } from '@/stores/sidebar';
import { workspaceNavigateTo$ } from '@/stores/workspaceExplorer';
import type { Artifact } from '@/types/artifact';
import type { FileType } from '@/types/workspace';
import { useArtifactsApi } from '@/utils/artifactsApi';

interface ArtifactsPanelProps {
  conversationId: string;
}

function getAttachmentRelativePath(sourcePath: string): string {
  return sourcePath.replace(/^attachments\/?/, '');
}

function toPreviewFile(artifact: Artifact): FileType | null {
  if (artifact.source.type !== 'attachment' || !artifact.source.path) {
    return null;
  }

  return {
    name: artifact.title,
    path: getAttachmentRelativePath(artifact.source.path),
    type: 'file',
    size: artifact.size ?? 0,
    modified: artifact.created_at,
    mime_type: artifact.mime_type,
  };
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatCreatedAt(createdAt: string): string {
  const createdDate = new Date(createdAt);
  if (Number.isNaN(createdDate.getTime())) {
    return 'Unknown time';
  }

  return formatDistanceToNow(createdDate, { addSuffix: true });
}

export function ArtifactsPanel({ conversationId }: ArtifactsPanelProps) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { listArtifacts } = useArtifactsApi();
  const fetchControllerRef = useRef<AbortController | null>(null);

  const loadArtifacts = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const data = await listArtifacts(conversationId, signal);
        if (!signal?.aborted) {
          setArtifacts(data);
          setLoading(false);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        console.error('Error loading artifacts:', err);
        setError(err instanceof Error ? err.message : 'Failed to load artifacts');
        setLoading(false);
      }
    },
    [conversationId, listArtifacts]
  );

  const startLoad = useCallback(() => {
    fetchControllerRef.current?.abort();
    const controller = new AbortController();
    fetchControllerRef.current = controller;
    void loadArtifacts(controller.signal);
  }, [loadArtifacts]);

  useEffect(() => {
    startLoad();
    return () => fetchControllerRef.current?.abort();
  }, [startLoad]);

  useEffect(() => {
    if (!artifacts.length) {
      setSelectedArtifactId(null);
      return;
    }

    if (!selectedArtifactId || !artifacts.some((artifact) => artifact.id === selectedArtifactId)) {
      setSelectedArtifactId(artifacts[0].id);
    }
  }, [artifacts, selectedArtifactId]);

  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null;
  const selectedFile = selectedArtifact ? toPreviewFile(selectedArtifact) : null;

  const handleOpenInWorkspace = () => {
    if (selectedArtifact?.source.type !== 'attachment' || !selectedArtifact.source.path) {
      return;
    }

    const relativePath = getAttachmentRelativePath(selectedArtifact.source.path);
    const directory = relativePath.includes('/')
      ? relativePath.slice(0, relativePath.lastIndexOf('/'))
      : '';

    workspaceNavigateTo$.set({
      path: directory,
      root: 'attachments',
    });
    rightSidebarVisible$.set(true);
    rightSidebarActiveTab$.set('workspace');
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b p-4">
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-muted-foreground" />
          <div>
            <h2 className="text-sm font-medium">Artifacts</h2>
            <p className="text-xs text-muted-foreground">
              {artifacts.length} artifact{artifacts.length === 1 ? '' : 's'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={startLoad} title="Refresh">
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleOpenInWorkspace}
            disabled={
              selectedArtifact?.source.type !== 'attachment' || !selectedArtifact?.source.path
            }
            title="Open in workspace viewer"
          >
            <FolderOpen className="mr-2 h-4 w-4" />
            Open in workspace
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="max-h-[45%] w-full shrink-0 overflow-auto border-b">
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : error ? (
            <div className="flex h-full items-center justify-center p-4 text-center text-destructive">
              {error}
            </div>
          ) : artifacts.length === 0 ? (
            <div className="flex h-full items-center justify-center p-4 text-center text-muted-foreground">
              No artifacts yet. Uploaded files and generated attachments will show up here.
            </div>
          ) : (
            <div className="divide-y">
              {artifacts.map((artifact) => {
                const isSelected = artifact.id === selectedArtifactId;

                return (
                  <button
                    key={artifact.id}
                    type="button"
                    onClick={() => setSelectedArtifactId(artifact.id)}
                    className={`w-full px-4 py-3 text-left transition-colors hover:bg-muted/50 ${
                      isSelected ? 'bg-muted' : ''
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{artifact.title}</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {formatCreatedAt(artifact.created_at)}
                          {artifact.size !== null ? ` • ${formatFileSize(artifact.size)}` : ''}
                        </div>
                      </div>
                      <Badge variant="secondary" className="shrink-0 lowercase">
                        {artifact.kind}
                      </Badge>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="min-h-0 w-full flex-1 overflow-hidden">
          {selectedArtifact && selectedFile ? (
            <FilePreview file={selectedFile} conversationId={conversationId} root="attachments" />
          ) : selectedArtifact ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
              <Package className="h-8 w-8 text-muted-foreground" />
              <div className="text-sm font-medium">{selectedArtifact.title}</div>
              <div className="text-sm text-muted-foreground">
                Preview is not available for this artifact type yet.
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              Select an artifact to preview
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
