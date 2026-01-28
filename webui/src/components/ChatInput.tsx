import { Send, Loader2, Settings, X, Bot, Folder, Clock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useState, useEffect, useRef, type FC, type FormEvent, type KeyboardEvent } from 'react';
import { useApi } from '@/contexts/ApiContext';
import { Badge } from '@/components/ui/badge';
import { ModelSelector } from '@/components/ModelSelector';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { type Observable } from '@legendapp/state';
import { Computed, use$ } from '@legendapp/state/react';
import { conversations$ } from '@/stores/conversations';
import { selectedAgent$, selectedWorkspace$ } from '@/stores/sidebar';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import { WorkspaceSelector } from '@/components/WorkspaceSelector';
import type { WorkspaceProject, Agent } from '@/utils/workspaceUtils';
import { useModels } from '@/hooks/useModels';
import { useFileAutocomplete } from '@/hooks/useFileAutocomplete';
import { FileAutocomplete } from '@/components/FileAutocomplete';

export interface ChatOptions {
  model?: string;
  stream?: boolean;
  workspace?: string;
}

interface Props {
  conversationId?: string;
  onSend: (message: string, options?: ChatOptions) => void;
  onInterrupt?: () => Promise<void>;
  isReadOnly?: boolean;
  defaultModel?: string;
  autoFocus$: Observable<boolean>;
  value?: string;
  onChange?: (value: string) => void;
}

interface ChatOptionsProps {
  selectedModel: string;
  setSelectedModel: (model: string) => void;
  selectedWorkspace: string;
  setSelectedWorkspace: (workspace: string) => void;
  streamingEnabled: boolean;
  setStreamingEnabled: (enabled: boolean) => void;
  availableWorkspaces: WorkspaceProject[];
  isDisabled: boolean;
  showWorkspaceSelector: boolean;
  onAddWorkspace?: (path: string) => void;
}

const ChatOptionsPanel: FC<ChatOptionsProps> = ({
  selectedModel,
  setSelectedModel,
  selectedWorkspace,
  setSelectedWorkspace,
  streamingEnabled,
  setStreamingEnabled,
  availableWorkspaces,
  isDisabled,
  showWorkspaceSelector,
  onAddWorkspace,
}) => (
  <div className="space-y-8">
    <div className="space-y-1">
      <Label>Model</Label>
      <ModelSelector
        value={selectedModel}
        onValueChange={setSelectedModel}
        disabled={isDisabled}
        showFormField={false}
        placeholder="Select model"
      />
    </div>

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
      >
        <X className="h-2.5 w-2.5" />
      </Button>
    </Badge>
  );
};

const AgentBadge: FC<{ agent: Agent; onRemove: () => void }> = ({ agent, onRemove }) => (
  <Badge variant="secondary" className="flex items-center gap-1.5 pr-1">
    <div className="flex items-center gap-1.5">
      <Bot className="h-3 w-3" />
      <span className="text-xs">{agent.name}</span>
    </div>
    <Button
      variant="ghost"
      size="sm"
      onClick={onRemove}
      className="h-4 w-4 p-0 hover:bg-destructive/20"
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
      >
        <X className="h-2.5 w-2.5" />
      </Button>
    </Badge>
  );
};

export const ChatInput: FC<Props> = ({
  conversationId,
  onSend,
  onInterrupt,
  isReadOnly,
  defaultModel = '',
  autoFocus$,
  value,
  onChange,
}) => {
  const { isConnected$ } = useApi();
  const sidebarSelectedWorkspace = use$(selectedWorkspace$);
  const sidebarSelectedAgent = use$(selectedAgent$);

  // Use dynamic models instead of static list
  const { defaultModel: apiDefaultModel } = useModels();

  // Get conversation config to read the actual model
  const conversation$ = conversationId ? conversations$.get(conversationId) : null;
  const conversationModel = conversation$?.chatConfig?.get()?.chat?.model;

  // Initialize message from localStorage for persistence across page reloads
  const storageKey = conversationId ? `gptme-draft-${conversationId}` : 'gptme-draft-new';
  const [internalMessage, setInternalMessage] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem(storageKey) || '';
    }
    return '';
  });
  const [streamingEnabled, setStreamingEnabled] = useState(true);

  // Persist message to localStorage when it changes
  // Note: We only save non-empty messages, we don't clear on empty.
  // This ensures drafts persist until a new message is typed, preventing
  // data loss if send fails (the draft would already be cleared otherwise).
  useEffect(() => {
    if (typeof window !== 'undefined' && internalMessage) {
      localStorage.setItem(storageKey, internalMessage);
    }
  }, [internalMessage, storageKey]);

  // Track if user has explicitly selected a model (temporary override)
  const [hasExplicitModelSelection, setHasExplicitModelSelection] = useState(false);
  const [selectedModel, setSelectedModel] = useState('');

  // Compute the effective model to use (explicit selection or conversation default)
  const effectiveModel = hasExplicitModelSelection
    ? selectedModel
    : conversationModel || defaultModel || apiDefaultModel || '';

  // Update selectedModel when conversation config changes, but only if no explicit selection
  useEffect(() => {
    if (!hasExplicitModelSelection) {
      const newModel = conversationModel || defaultModel || apiDefaultModel || '';
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

  const isConnected = use$(isConnected$);

  // Get available workspaces using the reusable hook
  const { workspaces: availableWorkspaces, addCustomWorkspace } = useWorkspaces(false); // Don't fetch, just subscribe to cache changes

  // File autocomplete for @ mentions
  const fileAutocomplete = useFileAutocomplete({
    conversationId,
    enabled: isConnected && !isReadOnly,
  });

  const message = value !== undefined ? value : internalMessage;
  const setMessage = value !== undefined ? onChange || (() => {}) : setInternalMessage;

  // Queued message state - stores message and options to send when generation completes
  // Options are captured at queue time to prevent changes during generation from affecting the send
  interface QueuedMessage {
    text: string;
    options: ChatOptions;
  }
  const [queuedMessage, setQueuedMessage] = useState<QueuedMessage | null>(null);

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

  // Send queued message when generation completes
  useEffect(() => {
    // Detect transition from generating to not generating
    if (wasGenerating.current && !isGenerating && queuedMessage) {
      console.log('[ChatInput] Generation completed, sending queued message');
      // Use options captured at queue time, not current options
      onSend(queuedMessage.text, queuedMessage.options);
      setQueuedMessage(null);
    }
    wasGenerating.current = isGenerating;
  }, [isGenerating, queuedMessage, onSend]);

  // Update workspace when sidebar selection changes (only for new conversations)
  useEffect(() => {
    if (!conversationId && sidebarSelectedWorkspace) {
      setSelectedWorkspace(sidebarSelectedWorkspace);
      setWorkspaceExplicitlySelected(true);
    } else if (!conversationId && sidebarSelectedAgent && sidebarSelectedAgent.path) {
      setSelectedWorkspace(sidebarSelectedAgent.path);
      setWorkspaceExplicitlySelected(false); // Agent-derived workspace, not explicit
    } else if (!conversationId && !sidebarSelectedWorkspace && !sidebarSelectedAgent) {
      setSelectedWorkspace('.');
      setWorkspaceExplicitlySelected(false);
    }
  }, [conversationId, sidebarSelectedWorkspace, sidebarSelectedAgent]);

  // Wrapper function for explicit workspace selection
  const handleWorkspaceChange = (workspace: string) => {
    setSelectedWorkspace(workspace);
    setWorkspaceExplicitlySelected(workspace !== '.');
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (isGenerating) {
      // If there's a message, queue it instead of interrupting
      if (message.trim()) {
        console.log('[ChatInput] Queueing message for after generation completes');
        // Capture options at queue time to preserve user's intent
        setQueuedMessage({
          text: message,
          options: {
            model: effectiveModel === 'default' ? undefined : effectiveModel,
            stream: streamingEnabled,
            workspace: selectedWorkspace || undefined,
          },
        });
        setMessage('');
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
    } else if (message.trim()) {
      onSend(message, {
        model: effectiveModel === 'default' ? undefined : effectiveModel,
        stream: streamingEnabled,
        workspace: selectedWorkspace || undefined,
      });
      setMessage('');
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

      // If generating, interrupt
      if (isGenerating && onInterrupt) {
        console.log('[ChatInput] Escape pressed, interrupting generation...');
        onInterrupt();
      }

      // Always blur the input on Escape
      textareaRef.current?.blur();
    }
  };

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
      <div className="flex flex-col gap-2">
        {/* Show queued message indicator */}
        {queuedMessage && (
          <div className="flex items-center">
            <QueuedMessageBadge
              message={queuedMessage.text}
              onClear={() => setQueuedMessage(null)}
            />
          </div>
        )}
        <div className="flex">
          <Computed>
            {() => (
              <div className="relative flex flex-1">
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
                  data-testid="chat-input"
                  onChange={handleTextareaChange}
                  onKeyDown={handleKeyDown}
                  placeholder={placeholder}
                  className="max-h-[400px] min-h-[60px] resize-none overflow-y-auto pb-8 pr-16"
                  disabled={isDisabled}
                />

                <div className="absolute bottom-1.5 left-1.5 flex items-center gap-2">
                  <OptionsButton isDisabled={isDisabled}>
                    <ChatOptionsPanel
                      selectedModel={effectiveModel}
                      setSelectedModel={(model: string) => {
                        setSelectedModel(model);
                        setHasExplicitModelSelection(true);
                      }}
                      selectedWorkspace={selectedWorkspace}
                      setSelectedWorkspace={handleWorkspaceChange}
                      streamingEnabled={streamingEnabled}
                      setStreamingEnabled={setStreamingEnabled}
                      availableWorkspaces={availableWorkspaces}
                      isDisabled={isDisabled}
                      showWorkspaceSelector={!conversationId}
                      onAddWorkspace={(path: string) => {
                        console.log('[ChatInput] Adding new workspace:', path);
                        addCustomWorkspace(path);
                      }}
                    />
                  </OptionsButton>

                  {/* Agent badge for new conversations */}
                  {!conversationId && sidebarSelectedAgent && (
                    <AgentBadge
                      agent={sidebarSelectedAgent}
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
                        onRemove={() => {
                          setSelectedWorkspace('.');
                          setWorkspaceExplicitlySelected(false);
                        }}
                      />
                    )}
                </div>

                <SubmitButton
                  isGenerating={isGenerating}
                  isDisabled={isDisabled}
                  hasText={!!message.trim()}
                />
              </div>
            )}
          </Computed>
        </div>
      </div>
    </form>
  );
};
