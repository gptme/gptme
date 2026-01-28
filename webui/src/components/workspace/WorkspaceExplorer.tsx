import { useState, useEffect, useCallback } from 'react';
import { Loader2 } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { useWorkspaceApi } from '@/utils/workspaceApi';
import { FileList } from './FileList';
import { FilePreview } from './FilePreview';
import { PathSegments } from './PathSegments';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import type { FileType } from '@/types/workspace';

interface WorkspaceExplorerProps {
  conversationId: string;
}

export function WorkspaceExplorer({ conversationId }: WorkspaceExplorerProps) {
  const [files, setFiles] = useState<FileType[]>([]);
  const [currentPath, setCurrentPath] = useState('');
  const [selectedFile, setSelectedFile] = useState<FileType | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [workspaceRoot, setWorkspaceRoot] = useState<string>('');
  const api = useApi();
  const { listWorkspace } = useWorkspaceApi();

  const loadFiles = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listWorkspace(conversationId, currentPath, showHidden);
      setFiles(data);
    } catch (err) {
      console.error('Error loading workspace:', err);
      setError(err instanceof Error ? err.message : 'Failed to load workspace');
    } finally {
      setLoading(false);
    }
  }, [conversationId, currentPath, showHidden, listWorkspace]);

  // Load chat config to get workspace root path
  useEffect(() => {
    const loadChatConfig = async () => {
      try {
        const config = await api.getChatConfig(conversationId);
        setWorkspaceRoot(config.chat.workspace || '');
      } catch (err) {
        console.error('Error loading chat config:', err);
        // Set a default workspace root if config loading fails
        setWorkspaceRoot('.');
      }
    };

    loadChatConfig();
  }, [api, conversationId]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const handleFileClick = (file: FileType) => {
    setSelectedFile(file);
  };

  const handleDirectoryClick = (path: string) => {
    setCurrentPath(path);
    setSelectedFile(null);
  };

  if (error) {
    return <div className="flex h-full items-center justify-center text-destructive">{error}</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b p-4">
        <PathSegments
          path={currentPath}
          workspaceRoot={workspaceRoot}
          onNavigate={handleDirectoryClick}
        />
        <div className="flex items-center space-x-2">
          <Switch id="show-hidden" checked={showHidden} onCheckedChange={setShowHidden} />
          <Label htmlFor="show-hidden">Show hidden files</Label>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="h-full w-1/2 overflow-hidden border-r">
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : (
            <FileList
              files={files}
              currentPath={currentPath}
              onFileClick={handleFileClick}
              onDirectoryClick={handleDirectoryClick}
            />
          )}
        </div>
        <div className="h-full w-1/2 overflow-hidden">
          {selectedFile ? (
            <FilePreview file={selectedFile} conversationId={conversationId} />
          ) : (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              Select a file to preview
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
