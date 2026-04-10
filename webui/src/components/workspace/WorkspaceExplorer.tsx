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
import type { WorkspaceRoot } from '@/stores/workspaceExplorer';
import { workspaceNavigateTo$ } from '@/stores/workspaceExplorer';
import { use$ } from '@legendapp/state/react';

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
  const [activeRoot, setActiveRoot] = useState<WorkspaceRoot>('workspace');

  const [workspaceRoot, setWorkspaceRoot] = useState<string>('');
  const { api } = useApi();
  const { listWorkspace } = useWorkspaceApi();

  // Listen for external navigation requests (e.g. from "open in workspace" in ChatMessage)
  const navigateTo = use$(workspaceNavigateTo$);
  useEffect(() => {
    if (navigateTo) {
      setActiveRoot(navigateTo.root);
      setCurrentPath(navigateTo.path);
      setSelectedFile(null);
      workspaceNavigateTo$.set(null);
    }
  }, [navigateTo]);

  const loadFiles = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listWorkspace(conversationId, currentPath, showHidden, activeRoot);
      setFiles(data);
    } catch (err) {
      console.error('Error loading workspace:', err);
      setError(err instanceof Error ? err.message : 'Failed to load workspace');
    } finally {
      setLoading(false);
    }
  }, [conversationId, currentPath, showHidden, activeRoot, listWorkspace]);

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

  const handleRootChange = (root: WorkspaceRoot) => {
    setActiveRoot(root);
    setCurrentPath('');
    setSelectedFile(null);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Root selector tabs */}
      <div className="flex border-b">
        <button
          className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
            activeRoot === 'workspace'
              ? 'border-b-2 border-primary text-primary'
              : 'text-muted-foreground hover:text-foreground'
          }`}
          onClick={() => handleRootChange('workspace')}
        >
          Workspace
        </button>
        <button
          className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
            activeRoot === 'attachments'
              ? 'border-b-2 border-primary text-primary'
              : 'text-muted-foreground hover:text-foreground'
          }`}
          onClick={() => handleRootChange('attachments')}
        >
          Attachments
        </button>
      </div>

      <div className="flex items-center justify-between border-b p-4">
        <PathSegments
          path={currentPath}
          workspaceRoot={activeRoot === 'workspace' ? workspaceRoot : 'attachments'}
          onNavigate={handleDirectoryClick}
        />
        <div className="flex items-center space-x-2">
          <Switch id="show-hidden" checked={showHidden} onCheckedChange={setShowHidden} />
          <Label htmlFor="show-hidden">Show hidden files</Label>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="h-full w-1/2 overflow-hidden border-r">
          {error ? (
            <div className="flex h-full items-center justify-center text-destructive">{error}</div>
          ) : loading ? (
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
            <FilePreview file={selectedFile} conversationId={conversationId} root={activeRoot} />
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
