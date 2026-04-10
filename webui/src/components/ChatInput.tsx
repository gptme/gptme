import {
  Send,
  Loader2,
  Settings,
  X,
  Bot,
  Folder,
  Clock,
  Paperclip,
  File,
  ChevronDown,
  SlidersHorizontal,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type FC,
  type FormEvent,
  type KeyboardEvent,
  type DragEvent,
} from 'react';
import { useApi } from '@/contexts/ApiContext';
import { Badge } from '@/components/ui/badge';
import { ModelPicker } from '@/components/ModelPicker';
import { ProviderIcon } from '@/components/ProviderIcon';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { type Observable } from '@legendapp/state';
import { Computed, use$ } from '@legendapp/state/react';
import { conversations$ } from '@/stores/conversations';
import {
  selectedAgent$,
  selectedWorkspace$,
  rightSidebarVisible$,
  rightSidebarActiveTab$,
} from '@/stores/sidebar';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import { WorkspaceSelector } from '@/components/WorkspaceSelector';
import type { WorkspaceProject, Agent } from '@/utils/workspaceUtils';
import { useModels } from '@/hooks/useModels';
import { useAgents } from '@/hooks/useAgents';
import { useFileAutocomplete } from '@/hooks/useFileAutocomplete';
import { FileAutocomplete } from '@/components/FileAutocomplete';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export interface ChatOptions {
  model?: string;
  stream?: boolean;
  workspace?: string;
  files?: string[];
  /** Raw File objects to upload after conversation creation (for new chat view) */
  pendingFiles?: File[];
}

interface Props {
  conversationId?: string;
  onSend?: (message: string, options?: ChatOptions) => void;
  onInterrupt?: () => Promise<void>;
  isReadOnly?: boolean;
  defaultModel?: string;
  autoFocus$: Observable<boolean>;
  value?: string;
  onChange?: (value: string) => void;
  // Edit mode: reuse ChatInput for inline message editing with attachment support
  editMode?: boolean;
  editFiles?: string[]; // Pre-existing file paths from the message being edited
  onEditSave?: (content: string, files: string[], pendingFiles: File[], truncate: boolean) => void;
  onEditCancel?: () => void;
}

interface AttachedFile {
  name: string;
  path: string; // absolute path returned by the upload endpoint
  file?: File; // raw File object (for local buffering / preview)
  previewUrl?: string; // object URL for image preview
}

function createAttachedFiles(paths?: string[]): AttachedFile[] {
  if (!paths?.length) return [];
  return paths.map((path) => ({
    name: path.split('/').pop() || path,
    path,
  }));
}

interface ChatOptionsProps {
  selectedWorkspace: string;
  setSelectedWorkspace: (workspace: string) => void;
  selectedAgent: Agent | null;
  setSelectedAgent: (agent: Agent | null) => void;
  availableAgents: Agent[];
  baseUrl: string;
  streamingEnabled: boolean;
  setStreamingEnabled: (enabled: boolean) => void;
  availableWorkspaces: WorkspaceProject[];
  isDisabled: boolean;
  showWorkspaceSelector: boolean;
  onAddWorkspace?: (path: string) => void;
  onOpenChatSettings?: () => void;
}

const ChatOptionsPanel: FC<ChatOptionsProps> = ({
  selectedWorkspace,
  setSelectedWorkspace,
  selectedAgent,
  setSelectedAgent,
  availableAgents,
  baseUrl,
  streamingEnabled,
  setStreamingEnabled,
  availableWorkspaces,
  isDisabled,
  showWorkspaceSelector,
  onAddWorkspace,
  onOpenChatSettings,
}) => (
  <div className="space-y-8">
    {showWorkspaceSelector && availableAgents.length > 0 && (
      <div className="space-y-1">
        <Label>Agent</Label>
        <Select
          value={selectedAgent?.path || '_none'}
          onValueChange={(val) => {
            if (val === '_none') {
              setSelectedAgent(null);
            } else {
              const agent = availableAgents.find((a) => a.path === val);
              if (agent) setSelectedAgent(agent);
            }
          }}
          disabled={isDisabled}
        >
          <SelectTrigger>
            <SelectValue placeholder="No agent">
              {selectedAgent ? (
                <div className="flex items-center gap-2">
                  {selectedAgent.hasAvatar ? (
                    <img
                      src={`${baseUrl}/api/v2/agents/avatar?path=${encodeURIComponent(selectedAgent.path)}`}
                      alt={selectedAgent.name}
                      className="h-4 w-4 rounded-full object-cover"
                    />
                  ) : (
                    <Bot className="h-3.5 w-3.5" />
                  )}
                  <span>{selectedAgent.name}</span>
                </div>
              ) : (
                'No agent'
              )}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="_none">No agent</SelectItem>
            {availableAgents.map((agent) => (
              <SelectItem key={agent.path} value={agent.path}>
                <div className="flex items-center gap-2">
                  {agent.hasAvatar ? (
                    <img
                      src={`${baseUrl}/api/v2/agents/avatar?path=${encodeURIComponent(agent.path)}`}
                      alt={agent.name}
                      className="h-4 w-4 rounded-full object-cover"
                    />
                  ) : (
                    <Bot className="h-3.5 w-3.5" />
                  )}
                  <span>{agent.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    )}

    {showWorkspaceSelector && (
      <WorkspaceSelector
        selectedWorkspace={selectedWorkspace}
        onWorkspaceChange={setSelectedWorkspace}
        workspaces={availableWorkspaces}
        disabled={isDisabled}
        showConversationCount={true}
        allowCustomPath={true}
        onAddWorkspace={onAddWorkspace}
      />
    )}

    <StreamingToggle
      streamingEnabled={streamingEnabled}
      setStreamingEnabled={setStreamingEnabled}
      isDisabled={isDisabled}
    />

    {onOpenChatSettings && (
      <button
        type="button"
        onClick={onOpenChatSettings}
        className="flex w-full items-center gap-2 border-t pt-4 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <SlidersHorizontal className="h-3.5 w-3.5" />
        Chat settings
      </button>
    )}
  </div>
);

const StreamingToggle: FC<{
  streamingEnabled: boolean;
  setStreamingEnabled: (enabled: boolean) => void;
  isDisabled: boolean;
}> = ({ streamingEnabled, setStreamingEnabled, isDisabled }) => (
  <div className="flex items-center justify-between">
    <Label htmlFor="streaming-toggle">Enable streaming</Label>
    <Switch
      id="streaming-toggle"
      checked={streamingEnabled}
      onCheckedChange={setStreamingEnabled}
      disabled={isDisabled}
    />
  </div>
);

const ModelBadge: FC<{
  model: string;
  models: { id: string; provider: string; model: string }[];
  onModelChange: (model: string) => void;
  isDisabled: boolean;
}> = ({ model, models, onModelChange, isDisabled }) => {
  const [open, setOpen] = useState(false);
  const modelInfo = models.find((m) => m.id === model);
  const displayName = modelInfo?.model || model.split('/').pop() || model;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-5 max-w-[200px] rounded-sm px-1.5 text-[10px] text-muted-foreground transition-all hover:bg-accent hover:text-muted-foreground hover:opacity-100"
          disabled={isDisabled}
        >
          {modelInfo?.provider && <ProviderIcon provider={modelInfo.provider} size={10} />}
          <span className="ml-1 truncate">{displayName}</span>
          <ChevronDown className="ml-0.5 h-2 w-2 flex-shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="start">
        <ModelPicker
          value={model}
          onSelect={(id) => {
            onModelChange(id);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
};

const OptionsButton: FC<{ isDisabled: boolean; children: React.ReactNode }> = ({
  isDisabled,
  children,
}) => (
  <Popover>
    <PopoverTrigger asChild>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 rounded-sm px-1.5 text-[10px] text-muted-foreground transition-all hover:bg-accent hover:text-muted-foreground hover:opacity-100"
        disabled={isDisabled}
      >
        <Settings className="mr-0.5 h-2.5 w-2.5" />
        Options
      </Button>
    </PopoverTrigger>
    <PopoverContent className="w-80" align="start">
      {children}
    </PopoverContent>
  </Popover>
);

const SubmitButton: FC<{ isGenerating: boolean; isDisabled: boolean; hasText: boolean }> = ({
  isGenerating,
  isDisabled,
  hasText,
}) => {
  // When generating: show "Queue" if there's text, "Stop" if not
  // When not generating: show "Send"
  const showQueue = isGenerating && hasText;
  const showStop = isGenerating && !hasText;

  return (
    <Button
      type="submit"
      className={`absolute bottom-2 right-2 rounded-full p-1 transition-colors ${
        showStop
          ? 'animate-[pulse_1s_ease-in-out_infinite] bg-red-600 p-3 hover:bg-red-700'
          : showQueue
            ? 'bg-blue-600 p-3 text-white hover:bg-blue-700'
            : 'h-8 w-8 bg-green-600 text-green-100'
      }`}
      disabled={isDisabled}
      aria-label={showStop ? 'Stop generation' : showQueue ? 'Queue message' : 'Send message'}
    >
      {showStop ? (
        <div className="flex items-center gap-2">
          <span>Stop</span>
          <Loader2 className="h-3 w-3 animate-spin" />
        </div>
      ) : showQueue ? (
        <div className="flex items-center gap-2">
          <Clock className="h-3 w-3" />
          <span>Queue</span>
        </div>
      ) : (
        <Send className="h-3 w-3" />
      )}
    </Button>
  );
};

const WorkspaceBadge: FC<{ workspace: string; onRemove: () => void }> = ({
  workspace,
  onRemove,
}) => {
  // Show a shortened version of the workspace path for better UX
  const displayName = workspace === '.' ? 'Current' : workspace.split('/').pop() || workspace;

  return (
    <Badge variant="secondary" className="flex items-center gap-1.5 pr-1">
      <div className="flex items-center gap-1.5">
        <Folder className="h-3 w-3" />
        <span className="text-xs" title={workspace}>
          {displayName}
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={onRemove}
        className="h-4 w-4 p-0 hover:bg-destructive/20"
        aria-label={`Remove workspace ${displayName}`}
      >
        <X className="h-2.5 w-2.5" />
      </Button>
    </Badge>
  );
};

const AgentBadge: FC<{ agent: Agent; baseUrl: string; onRemove: () => void }> = ({
  agent,
  baseUrl,
  onRemove,
}) => (
  <Badge variant="secondary" className="flex items-center gap-1.5 pr-1">
    <div className="flex items-center gap-1.5">
      {agent.hasAvatar ? (
        <img
          src={`${baseUrl}/api/v2/agents/avatar?path=${encodeURIComponent(agent.path)}`}
          alt={agent.name}
          className="h-4 w-4 rounded-full object-cover"
        />
      ) : (
        <Bot className="h-3 w-3" />
      )}
      <span className="text-xs">{agent.name}</span>
    </div>
    <Button
      variant="ghost"
      size="sm"
      onClick={onRemove}
      className="h-4 w-4 p-0 hover:bg-destructive/20"
      aria-label={`Remove agent ${agent.name}`}
    >
      <X className="h-2.5 w-2.5" />
    </Button>
  </Badge>
);

const QueuedMessageBadge: FC<{ message: string; onClear: () => void }> = ({ message, onClear }) => {
  // Truncate long messages for display
  const displayMessage = message.length > 30 ? message.slice(0, 30) + '...' : message;

  return (
    <Badge
      variant="outline"
      className="flex items-center gap-1.5 border-blue-500 bg-blue-50 pr-1 dark:bg-blue-950"
    >
      <div className="flex items-center gap-1.5">
        <Clock className="h-3 w-3 text-blue-600" />
        <span className="text-xs text-blue-700 dark:text-blue-300" title={message}>
          Queued: {displayMessage}
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={onClear}
        className="h-4 w-4 p-0 hover:bg-destructive/20"
        title="Clear queued message"
        aria-label="Clear queued message"
      >
        <X className="h-2.5 w-2.5" />
      </Button>
    </Badge>
  );
};

const AttachedFileBadge: FC<{ name: string; previewUrl?: string; onRemove: () => void }> = ({
  name,
  previewUrl,
  onRemove,
}) => (
  <Badge variant="secondary" className="flex items-center gap-1.5 pr-1">
    <div className="flex items-center gap-1.5">
      {previewUrl ? (
        <img src={previewUrl} alt={name} className="h-6 w-6 rounded object-cover" />
      ) : (
        <File className="h-3 w-3" />
      )}
      <span className="max-w-[120px] truncate text-xs" title={name}>
        {name}
      </span>
    </div>
    <Button
      variant="ghost"
      size="sm"
      onClick={onRemove}
      className="h-4 w-4 p-0 hover:bg-destructive/20"
      aria-label={`Remove file ${name}`}
    >
      <X className="h-2.5 w-2.5" />
    </Button>
  </Badge>
);

export const ChatInput: FC<Props> = ({
  conversationId,
  onSend,
  onInterrupt,
  isReadOnly,
  defaultModel = '',
  autoFocus$,
  value,
  onChange,
  editMode,
  editFiles,
  onEditSave,
  onEditCancel,
}) => {
  const { isConnected$, connectionConfig } = useApi();
  const sidebarSelectedWorkspace = use$(selectedWorkspace$);
  const sidebarSelectedAgent = use$(selectedAgent$);

  // Use dynamic models instead of static list
  const { models: modelInfos, defaultModel: apiDefaultModel } = useModels();

  // Get conversation config to read the actual model
  const conversation$ = conversationId ? conversations$.get(conversationId) : null;
  const conversationModel = conversation$?.chatConfig?.get()?.chat?.model;

  // Initialize message from localStorage for persistence across page reloads
  // Skip localStorage in edit mode — content comes from the message being edited
  const storageKey = conversationId ? `gptme-draft-${conversationId}` : 'gptme-draft-new';
  const [internalMessage, setInternalMessage] = useState(() => {
    if (editMode) return '';
    if (typeof window !== 'undefined') {
      return localStorage.getItem(storageKey) || '';
    }
    return '';
  });
  const [streamingEnabled, setStreamingEnabled] = useState(true);

  // When switching conversations, load the new conversation's draft.
  // Use a ref to track the previous key so we can save the outgoing draft first.
  const prevStorageKey = useRef(storageKey);
  useEffect(() => {
    if (editMode || typeof window === 'undefined') return;
    if (storageKey !== prevStorageKey.current) {
      // Save outgoing draft to old key (if non-empty)
      // skip if current message was already cleared (e.g. after send)
      // Note: we read internalMessage indirectly via the DOM/ref to avoid
      // needing it as a dependency (which would cause infinite loops)
      prevStorageKey.current = storageKey;
      // Load incoming draft from new key
      setInternalMessage(localStorage.getItem(storageKey) || '');
    }
  }, [storageKey, editMode]);

  // Persist message draft to localStorage (skip in edit mode).
  // Clears the draft when the input is emptied (e.g. after send).
  useEffect(() => {
    if (editMode || typeof window === 'undefined') return;
    if (internalMessage) {
      localStorage.setItem(storageKey, internalMessage);
    } else {
      localStorage.removeItem(storageKey);
    }
  }, [internalMessage, storageKey, editMode]);

  // Track if user has explicitly selected a model (temporary override)
  const [hasExplicitModelSelection, setHasExplicitModelSelection] = useState(false);
  const [selectedModel, setSelectedModel] = useState('');

  // Fallback model when no other default is available
  const fallbackModel = 'anthropic/claude-sonnet-4-20250514';

  // Compute the effective model to use (explicit selection or conversation default)
  const effectiveModel = hasExplicitModelSelection
    ? selectedModel
    : conversationModel || defaultModel || apiDefaultModel || fallbackModel;

  // Update selectedModel when conversation config changes, but only if no explicit selection
  useEffect(() => {
    if (!hasExplicitModelSelection) {
      const newModel = conversationModel || defaultModel || apiDefaultModel || fallbackModel;
      setSelectedModel(newModel);
    }
  }, [conversationModel, defaultModel, apiDefaultModel, hasExplicitModelSelection]);

  // Reset explicit selection when conversation changes
  useEffect(() => {
    setHasExplicitModelSelection(false);
    setSelectedModel(conversationModel || defaultModel || apiDefaultModel || '');
  }, [conversationId, conversationModel, defaultModel, apiDefaultModel]);

  const [selectedWorkspace, setSelectedWorkspace] = useState<string>(
    // For new conversations, use the selected workspace from sidebar, otherwise default to '.'
    !conversationId && sidebarSelectedWorkspace
      ? sidebarSelectedWorkspace
      : !conversationId && sidebarSelectedAgent && sidebarSelectedAgent.path
        ? sidebarSelectedAgent.path
        : '.'
  );

  // Track whether workspace was explicitly selected (not derived from agent)
  const [workspaceExplicitlySelected, setWorkspaceExplicitlySelected] = useState(
    !conversationId && !!sidebarSelectedWorkspace
  );
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Stable string key derived from editFiles — prevents the useEffect below from
  // firing on every render due to new array references from the parent.
  const editFileKey = editFiles?.join('\0') ?? '';
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>(() =>
    editMode ? createAttachedFiles(editFiles) : []
  );
  const [isDragOver, setIsDragOver] = useState(false);

  const replaceAttachedFiles = useCallback((nextFiles: AttachedFile[]) => {
    setAttachedFiles((prev) => {
      prev.forEach((f) => {
        if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
      });
      return nextFiles;
    });
  }, []);

  // Revoke preview URLs and clear files
  const cleanupAndClearFiles = useCallback(() => {
    replaceAttachedFiles([]);
  }, [replaceAttachedFiles]);

  useEffect(() => {
    setIsDragOver(false);
    if (!editMode) {
      cleanupAndClearFiles();
    }
  }, [conversationId, cleanupAndClearFiles, editMode]);

  useEffect(() => {
    if (!editMode) return;
    replaceAttachedFiles(createAttachedFiles(editFiles));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editMode, editFileKey, replaceAttachedFiles]);

  // Always buffer files locally — upload happens on send, not on attach
  const attachFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;
    setAttachedFiles((prev) => [
      ...prev,
      ...fileArray.map((f) => ({
        name: f.name,
        path: '', // set after upload on send
        file: f,
        previewUrl: f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined,
      })),
    ]);
  }, []);

  // Clean up object URLs on unmount
  useEffect(() => {
    return () => {
      attachedFiles.forEach((f) => {
        if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only on unmount

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const relatedTarget = e.relatedTarget;
    if (relatedTarget instanceof Node && e.currentTarget.contains(relatedTarget)) {
      return;
    }
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        attachFiles(e.dataTransfer.files);
      }
    },
    [attachFiles]
  );

  const isConnected = use$(isConnected$);

  // Get available workspaces and agents using reusable hooks
  const { workspaces: availableWorkspaces, addCustomWorkspace } = useWorkspaces(false);
  const { agents: availableAgents } = useAgents(false);

  // File autocomplete for @ mentions
  const fileAutocomplete = useFileAutocomplete({
    conversationId,
    enabled: isConnected && !isReadOnly,
  });

  const message = value !== undefined ? value : internalMessage;
  const setMessage = value !== undefined ? onChange || (() => {}) : setInternalMessage;

  // Message queue - stores messages to send when generation completes
  // Options are captured at queue time to prevent changes during generation from affecting the send
  interface QueuedMessage {
    text: string;
    options: ChatOptions;
  }
  const [messageQueue, setMessageQueue] = useState<QueuedMessage[]>([]);

  const autoFocus = use$(autoFocus$);
  const conversation = conversationId ? use$(conversations$.get(conversationId)) : undefined;
  const isGenerating = conversation?.isGenerating || !!conversation?.executingTool;
  const placeholder = isReadOnly
    ? 'This is a demo conversation (read-only)'
    : !isConnected
      ? 'Connect to gptme to send messages'
      : "What's on your mind...";

  // Don't disable input while waiting for session - let users type
  // Session will be established by the time they finish typing
  const isDisabled = isReadOnly || !isConnected;

  // Focus the textarea when autoFocus is true and component is interactive
  useEffect(() => {
    if (autoFocus && textareaRef.current && !isReadOnly && isConnected) {
      textareaRef.current.focus();
      // Reset autoFocus$ to false after focusing
      autoFocus$.set(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFocus, isReadOnly, isConnected]);

  // Track previous isGenerating state to detect when generation completes
  const wasGenerating = useRef(false);

  // Send next queued message when generation completes
  useEffect(() => {
    // Detect transition from generating to not generating
    if (wasGenerating.current && !isGenerating && messageQueue.length > 0) {
      if (!onSend) return;
      const nextMessage = messageQueue[0];
      console.log('[ChatInput] Generation completed, sending queued message', {
        remaining: messageQueue.length - 1,
      });
      // Use options captured at queue time, not current options
      onSend(nextMessage.text, nextMessage.options);
      // Remove the sent message from queue
      setMessageQueue((prev) => prev.slice(1));
    }
    wasGenerating.current = isGenerating;
  }, [isGenerating, messageQueue, onSend]);

  // Sync workspace from sidebar/agent selection (only for new conversations).
  // Sidebar workspace selections sync both ways. Agent default applies only when
  // the user hasn't made an explicit pick. Clearing sidebar filters always resets
  // the ChatInput badge regardless of workspaceExplicitlySelected.
  useEffect(() => {
    if (conversationId) return; // only for new conversations

    if (sidebarSelectedWorkspace) {
      setSelectedWorkspace(sidebarSelectedWorkspace);
      setWorkspaceExplicitlySelected(true);
    } else if (sidebarSelectedAgent?.path && !workspaceExplicitlySelected) {
      setSelectedWorkspace(sidebarSelectedAgent.path);
    } else if (!sidebarSelectedWorkspace && !sidebarSelectedAgent) {
      // Sidebar cleared — reset ChatInput workspace badge
      setSelectedWorkspace('.');
      setWorkspaceExplicitlySelected(false);
    }
  }, [conversationId, sidebarSelectedWorkspace, sidebarSelectedAgent, workspaceExplicitlySelected]);

  // Wrapper function for explicit workspace selection
  const handleWorkspaceChange = (workspace: string) => {
    setSelectedWorkspace(workspace);
    setWorkspaceExplicitlySelected(workspace !== '.');
    // Sync to sidebar observable so the conversation list filters accordingly
    selectedWorkspace$.set(workspace === '.' ? '' : workspace);
  };

  // Edit mode: save with truncate option
  const handleEditSave = useCallback(
    (truncate: boolean) => {
      if (!onEditSave) return;
      const existingPaths = attachedFiles.filter((f) => f.path && !f.file).map((f) => f.path);
      const newFiles = attachedFiles.filter((f) => f.file).map((f) => f.file!);
      onEditSave(message, existingPaths, newFiles, truncate);
    },
    [onEditSave, attachedFiles, message]
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();

    // In edit mode, save instead of send
    if (editMode) {
      if (message.trim() || attachedFiles.length > 0) {
        handleEditSave(false);
      }
      return;
    }

    if (!onSend) return;

    // Files are always buffered locally — pass raw File objects for upload on send
    const pendingFiles =
      attachedFiles.length > 0
        ? attachedFiles.filter((f) => f.file).map((f) => f.file!)
        : undefined;
    // Also include any already-uploaded file paths (shouldn't happen with new flow, but defensive)
    const uploadedPaths =
      attachedFiles.length > 0 ? attachedFiles.filter((f) => f.path).map((f) => f.path) : undefined;

    if (isGenerating) {
      // If there's a message, queue it instead of interrupting
      if (message.trim() || attachedFiles.length > 0) {
        console.log('[ChatInput] Queueing message for after generation completes', {
          queueLength: messageQueue.length + 1,
        });
        // Capture options at queue time to preserve user's intent
        setMessageQueue((prev) => [
          ...prev,
          {
            text: message,
            options: {
              model: hasExplicitModelSelection ? effectiveModel : undefined,
              stream: streamingEnabled,
              workspace: selectedWorkspace || undefined,
              files: uploadedPaths,
              pendingFiles,
            },
          },
        ]);
        setMessage('');
        cleanupAndClearFiles();
        // Clear localStorage draft since we're queueing it
        if (typeof window !== 'undefined') {
          localStorage.removeItem(storageKey);
        }
        // Reset textarea height
        if (textareaRef.current) {
          textareaRef.current.style.height = '';
        }
      } else if (onInterrupt) {
        // No message text, so interrupt
        console.log('[ChatInput] Interrupting generation...', { isGenerating });
        try {
          await onInterrupt();
          console.log('[ChatInput] Generation interrupted successfully', { isGenerating });
        } catch (error) {
          console.error('[ChatInput] Error interrupting generation:', error);
        }
      }
    } else if (message.trim() || attachedFiles.length > 0) {
      onSend(message, {
        model: hasExplicitModelSelection ? effectiveModel : undefined,
        stream: streamingEnabled,
        workspace: selectedWorkspace || undefined,
        files: uploadedPaths,
        pendingFiles,
      });
      setMessage('');
      cleanupAndClearFiles();
      // Reset textarea height to default by removing inline style
      if (textareaRef.current) {
        textareaRef.current.style.height = '';
      }
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    // Handle file autocomplete keyboard navigation first
    if (fileAutocomplete.state.isOpen) {
      const handled = fileAutocomplete.handleKeyDown(e);
      if (handled) {
        // If Tab or Enter was pressed with a selection, apply it
        if (
          (e.key === 'Tab' || e.key === 'Enter') &&
          fileAutocomplete.state.files[fileAutocomplete.state.selectedIndex]
        ) {
          const newValue = fileAutocomplete.selectFile(
            fileAutocomplete.state.files[fileAutocomplete.state.selectedIndex]
          );
          setMessage(newValue);
        }
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();

      // If autocomplete is open, close it first
      if (fileAutocomplete.state.isOpen) {
        fileAutocomplete.close();
        return;
      }

      // In edit mode, cancel editing
      if (editMode && onEditCancel) {
        onEditCancel();
        return;
      }

      // If generating, interrupt
      if (isGenerating && onInterrupt) {
        console.log('[ChatInput] Escape pressed, interrupting generation...');
        onInterrupt();
      }

      // Always blur the input on Escape
      textareaRef.current?.blur();
    }
  };

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;

      const imageFiles: File[] = [];
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) imageFiles.push(file);
        }
      }

      if (imageFiles.length > 0) {
        e.preventDefault(); // Don't paste the image as text
        attachFiles(imageFiles);
      }
    },
    [attachFiles]
  );

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    const cursorPos = e.target.selectionStart || 0;
    setMessage(newValue);
    // Auto-adjust height
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 400)}px`;
    // Update file autocomplete
    fileAutocomplete.handleInputChange(newValue, cursorPos);
  };

  return (
    <form onSubmit={handleSubmit} className="p-4">
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        aria-label="Attach files"
        onChange={(e) => {
          if (e.target.files && e.target.files.length > 0) {
            attachFiles(e.target.files);
          }
          // Reset so the same file can be selected again
          e.target.value = '';
        }}
      />
      <div className="flex flex-col gap-2">
        {/* Show attached files */}
        {attachedFiles.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {attachedFiles.map((file, index) => (
              <AttachedFileBadge
                key={`${file.name}-${index}`}
                name={file.name}
                previewUrl={file.previewUrl}
                onRemove={() =>
                  setAttachedFiles((prev) => {
                    const removed = prev[index];
                    if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
                    return prev.filter((_, i) => i !== index);
                  })
                }
              />
            ))}
          </div>
        )}
        {/* Show queued messages indicator */}
        {messageQueue.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {messageQueue.map((msg, index) => (
              <QueuedMessageBadge
                key={index}
                message={messageQueue.length > 1 ? `(${index + 1}) ${msg.text}` : msg.text}
                onClear={() => setMessageQueue((prev) => prev.filter((_, i) => i !== index))}
              />
            ))}
          </div>
        )}
        <div className="flex">
          <Computed>
            {() => (
              <div
                className={`relative flex flex-1 ${isDragOver ? 'rounded-md ring-2 ring-primary ring-offset-2' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
              >
                {/* Drag overlay */}
                {isDragOver && (
                  <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-md bg-primary/10">
                    <span className="text-sm font-medium text-primary">Drop files to attach</span>
                  </div>
                )}
                {/* File autocomplete dropdown */}
                <FileAutocomplete
                  files={fileAutocomplete.state.files}
                  selectedIndex={fileAutocomplete.state.selectedIndex}
                  onSelect={(file) => {
                    const newValue = fileAutocomplete.selectFile(file);
                    setMessage(newValue);
                  }}
                  onHover={fileAutocomplete.setSelectedIndex}
                  isOpen={fileAutocomplete.state.isOpen}
                  query={fileAutocomplete.state.query}
                />
                <Textarea
                  ref={textareaRef}
                  value={message}
                  data-testid={editMode ? 'edit-input' : 'chat-input'}
                  onChange={handleTextareaChange}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={editMode ? 'Edit message...' : placeholder}
                  className={
                    editMode
                      ? 'max-h-[300px] min-h-[60px] resize-none overflow-y-auto pb-8'
                      : 'max-h-[400px] min-h-[60px] resize-none overflow-y-auto pb-8 pr-16'
                  }
                  disabled={isDisabled}
                  autoFocus={editMode}
                />

                {editMode ? (
                  /* Edit mode: minimal toolbar with attach + save/cancel */
                  <div className="absolute bottom-1.5 left-1.5 right-1.5 flex items-center justify-between">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-5 rounded-sm px-1.5 text-[10px] text-muted-foreground transition-all hover:bg-accent hover:text-muted-foreground hover:opacity-100"
                      onClick={() => fileInputRef.current?.click()}
                      title="Attach files"
                    >
                      <Paperclip className="mr-0.5 h-2.5 w-2.5" />
                      Attach
                    </Button>
                    <div className="flex items-center gap-1">
                      <Button
                        type="button"
                        size="sm"
                        className="h-6 px-2 text-xs"
                        onClick={() => handleEditSave(false)}
                        disabled={!message.trim() && attachedFiles.length === 0}
                      >
                        Save
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        className="h-6 px-2 text-xs"
                        onClick={() => handleEditSave(true)}
                        disabled={!message.trim() && attachedFiles.length === 0}
                      >
                        Save & Re-run
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-6 px-2 text-xs"
                        onClick={onEditCancel}
                      >
                        <X className="mr-0.5 h-3 w-3" />
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  /* Normal mode: full toolbar */
                  <>
                    <div className="absolute bottom-1.5 left-1.5 flex items-center gap-2">
                      <ModelBadge
                        model={effectiveModel}
                        models={modelInfos}
                        onModelChange={(model: string) => {
                          setSelectedModel(model);
                          setHasExplicitModelSelection(true);
                        }}
                        isDisabled={isDisabled}
                      />
                      {/* File attach button */}
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-5 rounded-sm px-1.5 text-[10px] text-muted-foreground transition-all hover:bg-accent hover:text-muted-foreground hover:opacity-100"
                        disabled={isDisabled}
                        onClick={() => fileInputRef.current?.click()}
                        title="Attach files"
                      >
                        <Paperclip className="mr-0.5 h-2.5 w-2.5" />
                        Attach
                      </Button>

                      <OptionsButton isDisabled={isDisabled}>
                        <ChatOptionsPanel
                          selectedWorkspace={selectedWorkspace}
                          setSelectedWorkspace={handleWorkspaceChange}
                          selectedAgent={sidebarSelectedAgent}
                          setSelectedAgent={(agent) => selectedAgent$.set(agent)}
                          availableAgents={availableAgents}
                          baseUrl={connectionConfig.baseUrl.replace(/\/+$/, '')}
                          streamingEnabled={streamingEnabled}
                          setStreamingEnabled={setStreamingEnabled}
                          availableWorkspaces={availableWorkspaces}
                          isDisabled={isDisabled}
                          showWorkspaceSelector={!conversationId}
                          onAddWorkspace={(path: string) => {
                            console.log('[ChatInput] Adding new workspace:', path);
                            addCustomWorkspace(path);
                          }}
                          onOpenChatSettings={
                            conversationId
                              ? () => {
                                  rightSidebarActiveTab$.set('settings');
                                  rightSidebarVisible$.set(true);
                                }
                              : undefined
                          }
                        />
                      </OptionsButton>

                      {/* Agent badge for new conversations */}
                      {!conversationId && sidebarSelectedAgent && (
                        <AgentBadge
                          agent={sidebarSelectedAgent}
                          baseUrl={connectionConfig.baseUrl.replace(/\/+$/, '')}
                          onRemove={() => selectedAgent$.set(null)}
                        />
                      )}

                      {/* Workspace badge for new conversations */}
                      {!conversationId &&
                        selectedWorkspace &&
                        selectedWorkspace !== '.' &&
                        workspaceExplicitlySelected && (
                          <WorkspaceBadge
                            workspace={selectedWorkspace}
                            onRemove={() => handleWorkspaceChange('.')}
                          />
                        )}
                    </div>

                    <SubmitButton
                      isGenerating={isGenerating}
                      isDisabled={isDisabled}
                      hasText={!!message.trim() || attachedFiles.length > 0}
                    />
                  </>
                )}
              </div>
            )}
          </Computed>
        </div>
      </div>
    </form>
  );
};
