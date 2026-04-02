import { useCallback, useRef, useState, type FC } from 'react';
import type { Message, StreamingMessage } from '@/types/conversation';
import { MessageAvatar } from './MessageAvatar';
import { useMessageChainType } from '@/utils/messageUtils';
import { useApi } from '@/contexts/ApiContext';
import { useSettings } from '@/contexts/SettingsContext';
import { ObservableHint, type Observable } from '@legendapp/state';
import { Memo, useObservable, useObserveEffect } from '@legendapp/state/react';
import * as smd from '@/utils/smd';
import { customRenderer, type CustomRenderer } from '@/utils/markdownRenderer';
import { processNestedCodeBlocks } from '@/utils/markdownUtils';
import {
  Clipboard,
  Check,
  AlertCircle,
  RotateCcw,
  Pencil,
  Play,
  RefreshCw,
  Trash2,
  Download,
  ExternalLink,
  FileText,
  FolderOpen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { ChatInput } from '@/components/ChatInput';
import { workspaceNavigateTo$ } from '@/stores/workspaceExplorer';
import { rightSidebarActiveTab$, rightSidebarVisible$ } from '@/stores/sidebar';

function formatTimestamp(timestamp: string): { short: string; full: string } {
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return { short: '', full: '' };
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const isThisYear = date.getFullYear() === now.getFullYear();

  const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const full = date.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  if (isToday) {
    return { short: timeStr, full };
  }
  const dateStr = date.toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    ...(isThisYear ? {} : { year: 'numeric' }),
  });
  return { short: `${dateStr}, ${timeStr}`, full };
}

interface Props {
  message$: Observable<Message | StreamingMessage>;
  previousMessage$?: Observable<Message | undefined>;
  nextMessage$?: Observable<Message | undefined>;
  conversationId: string;
  agentAvatarUrl?: string;
  agentName?: string;
  onRetry?: (message: Message) => void;
  onEdit?: (
    index: number,
    content: string,
    truncate: boolean,
    files?: string[],
    pendingFiles?: File[]
  ) => void;
  onDelete?: (index: number) => void;
  onRerun?: (index: number) => void;
  onRegenerate?: (index: number) => void;
  messageIndex?: number;
}

export const ChatMessage: FC<Props> = ({
  message$,
  previousMessage$,
  nextMessage$,
  conversationId,
  agentAvatarUrl,
  agentName,
  onRetry,
  onEdit,
  onDelete,
  onRerun,
  onRegenerate,
  messageIndex,
}) => {
  const { api, connectionConfig } = useApi();
  const { settings } = useSettings();
  // Use observables (not useState) because these are read inside <Memo>
  const isEditing$ = useObservable(false);
  const editContent$ = useObservable('');
  const editAutoFocus$ = useObservable(true);

  const contentRef = useRef<HTMLDivElement | null>(null);
  const renderer$ = useObservable<CustomRenderer | null>(null);
  const parser$ = useObservable<smd.Parser | null>(null);

  // Callback ref: initializes the parser whenever the content div mounts.
  // This fires inside <Memo> when the div appears (e.g. after exiting edit mode),
  // unlike useEffect which only fires on outer component re-render.
  const contentCallbackRef = useCallback(
    (node: HTMLDivElement | null) => {
      contentRef.current = node;
      if (!node) return;

      const renderer = customRenderer(node, false, true, settings.blocksDefaultOpen);
      renderer$.set(ObservableHint.opaque(renderer));
      const newParser = smd.parser(renderer);
      parser$.set(ObservableHint.opaque(newParser));

      // Write existing content to the new parser (handles re-mount after edit mode)
      const existingContent = message$.content.peek();
      if (existingContent) {
        previousContent$.set('');
        // Preprocess to widen outer fences for nested code blocks (gptme convention)
        // before feeding to smd, which doesn't understand ```lang = opener nesting.
        const { processedContent } = processNestedCodeBlocks(existingContent);
        smd.parser_write(newParser, processedContent);
        smd.parser_end(newParser);
        renderer$.set(null);
        parser$.set(null);
        previousContent$.set(existingContent);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settings.blocksDefaultOpen]
  );

  // Send any new content to the parser
  const previousContent$ = useObservable('');
  useObserveEffect(message$.content, ({ value }) => {
    const previousContent = previousContent$.get();
    const content = value || '';

    // If content was replaced (not appended), reinitialize the parser
    if (content && !content.startsWith(previousContent) && previousContent.length > 0) {
      previousContent$.set('');
      if (contentRef.current) {
        contentRef.current.innerHTML = '';
        const renderer = customRenderer(
          contentRef.current,
          false,
          true,
          settings.blocksDefaultOpen
        );
        renderer$.set(ObservableHint.opaque(renderer));
        const newParser = smd.parser(renderer);
        parser$.set(ObservableHint.opaque(newParser));
        // Preprocess nested code blocks before feeding to smd
        const { processedContent } = processNestedCodeBlocks(content);
        smd.parser_write(newParser, processedContent);
        smd.parser_end(newParser);
        renderer$.set(null);
        parser$.set(null);
        previousContent$.set(content);
      }
      return;
    }

    const newChars = content.slice(previousContent.length);
    if (!newChars) return;
    previousContent$.set(content);
    if (!contentRef.current) return;
    const parser = parser$.get();
    if (!parser) return;
    smd.parser_write(parser, newChars);
  });

  // End the parser when the message is complete
  // Handle content changes
  useObserveEffect(message$.content, () => {
    const message = message$.peek();
    if (!message) return;
    const parser = parser$.peek();
    if (!parser) return;

    // For non-streaming messages, end parser immediately
    if (!('isComplete' in message)) {
      smd.parser_end(parser);
      renderer$.set(null);
      parser$.set(null);
    }
  });

  // Handle streaming message completion separately
  useObserveEffect(
    (message$ as Observable<StreamingMessage>).isComplete,
    ({ value: isComplete }) => {
      if (!isComplete) return;
      const parser = parser$.peek();
      if (!parser) return;

      smd.parser_end(parser);
      renderer$.set(null);
      parser$.set(null);
    }
  );

  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [lightboxName, setLightboxName] = useState('');

  const getFileExtension = (filename: string) => {
    const ext = filename.split('.').pop()?.toLowerCase() || '';
    return ext;
  };

  const renderFiles = () => {
    if (!message$.files.get()?.length) return null;

    return (
      <div className="mt-2 space-y-2">
        <Memo>
          {() => {
            return message$.files.map((filename$) => {
              // Remove any parent directory references and normalize path
              const filename = filename$.get();
              const sanitizedPath = filename
                .split('/')
                .filter((part) => part !== '..')
                .join('/');
              const fileUrl = `${connectionConfig.baseUrl}/api/v2/conversations/${conversationId}/files/${sanitizedPath}`;
              const isImage = /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(filename);

              // Get just the filename without path for display
              const displayName = sanitizedPath.split('/').pop() || sanitizedPath;
              const ext = getFileExtension(filename);

              if (isImage) {
                return (
                  <div key={filename} className="max-w-md">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="block cursor-zoom-in text-left"
                          onClick={() => {
                            setLightboxUrl(fileUrl);
                            setLightboxName(displayName);
                          }}
                        >
                          <div className="relative">
                            <img
                              src={fileUrl}
                              alt={displayName}
                              className="rounded-md border border-border transition-opacity hover:opacity-90"
                              onError={(e) => {
                                const img = e.currentTarget;
                                const errorDiv = img.parentElement?.querySelector('.error-message');
                                if (errorDiv) {
                                  if (img.src.includes('..')) {
                                    errorDiv.textContent =
                                      '⚠️ Cannot access files outside the workspace';
                                  } else {
                                    errorDiv.textContent = '⚠️ Failed to load image';
                                  }
                                  errorDiv.classList.remove('hidden');
                                }
                                img.classList.add('hidden');
                              }}
                            />
                            <div className="error-message hidden rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400"></div>
                          </div>
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="top">
                        <p className="font-medium">{displayName}</p>
                        <p className="text-xs text-muted-foreground">
                          {ext.toUpperCase()} image — click to expand
                        </p>
                      </TooltipContent>
                    </Tooltip>
                    <div className="mt-1 text-xs text-muted-foreground">{displayName}</div>
                  </div>
                );
              }

              return (
                <div key={filename}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="inline-flex items-center gap-2 rounded-md border border-border bg-muted/50 px-3 py-1.5 text-sm transition-colors hover:bg-muted">
                        <FileText className="h-4 w-4 text-muted-foreground" />
                        <span className="max-w-[200px] truncate">{displayName}</span>
                        <a
                          href={fileUrl}
                          download={displayName}
                          className="text-muted-foreground transition-colors hover:text-foreground"
                          onClick={(e) => e.stopPropagation()}
                          title="Download"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </a>
                        <button
                          className="text-muted-foreground transition-colors hover:text-foreground"
                          onClick={(e) => {
                            e.stopPropagation();
                            const isAttachment = sanitizedPath.startsWith('attachments/');
                            const dir = sanitizedPath.includes('/')
                              ? sanitizedPath.substring(0, sanitizedPath.lastIndexOf('/'))
                              : '';
                            // Strip 'attachments/' prefix: the explorer root is already logdir/attachments/
                            const explorerPath = isAttachment
                              ? dir.replace(/^attachments\/?/, '')
                              : dir;
                            workspaceNavigateTo$.set({
                              path: explorerPath,
                              root: isAttachment ? 'attachments' : 'workspace',
                            });
                            rightSidebarVisible$.set(true);
                            rightSidebarActiveTab$.set('workspace');
                          }}
                          title="Open in workspace viewer"
                        >
                          <FolderOpen className="h-3.5 w-3.5" />
                        </button>
                        <a
                          href={fileUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-muted-foreground transition-colors hover:text-foreground"
                          onClick={(e) => e.stopPropagation()}
                          title="Open in new tab"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      <p className="font-medium">{displayName}</p>
                      <p className="text-xs text-muted-foreground">
                        {ext ? ext.toUpperCase() + ' file' : 'File'} — {sanitizedPath}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                </div>
              );
            });
          }}
        </Memo>

        {/* Image lightbox dialog */}
        <Dialog open={!!lightboxUrl} onOpenChange={(open) => !open && setLightboxUrl(null)}>
          <DialogContent className="max-h-[90vh] max-w-[90vw] overflow-hidden p-0">
            <div className="flex flex-col">
              <div className="flex items-center justify-between border-b px-4 py-2 pr-12">
                <DialogTitle className="truncate text-sm font-medium">{lightboxName}</DialogTitle>
                <a
                  href={lightboxUrl || ''}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ExternalLink className="h-3 w-3" />
                  Open original
                </a>
              </div>
              <div className="flex items-center justify-center bg-black/5 p-4 dark:bg-white/5">
                {lightboxUrl && (
                  <img
                    src={lightboxUrl}
                    alt={lightboxName}
                    className="max-h-[80vh] max-w-full rounded object-contain"
                  />
                )}
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    );
  };

  const isUser$ = useObservable(() => message$.role.get() === 'user');
  const isAssistant$ = useObservable(() => message$.role.get() === 'assistant');
  const isSystem$ = useObservable(() => message$.role.get() === 'system');
  const isError$ = useObservable(() => previousContent$.get().startsWith('Error'));
  const isSuccess$ = useObservable(() => {
    // The equivalent pattern for this in gptme-core exists in gptme/message.py
    // Keep these in sync for consistency
    const content = previousContent$.get().toLowerCase();
    const firstThreeWords = content.split(/\s+/).slice(0, 3);
    return (
      content.startsWith('saved') ||
      content.startsWith('appended') ||
      firstThreeWords.some((word) => word.includes('success') || word.includes('successfully'))
    );
  });

  const copied$ = useObservable(false);

  const handleCopy = async () => {
    const content = message$.content.peek();
    if (content) {
      try {
        await navigator.clipboard.writeText(content);
        copied$.set(true);
        setTimeout(() => copied$.set(false), 2000);
      } catch (err) {
        console.error('Failed to copy to clipboard:', err);
      }
    }
  };

  const chainType$ = useMessageChainType(message$, previousMessage$, nextMessage$);

  // Compute visual chain once: adjusts chain type to avoid border issues next to borderless
  // assistant messages. Used by both messageClasses$ and wrapperClasses$.
  const visualChain$ = useObservable(() => {
    const chain = chainType$.get();
    const prevIsAssistant = previousMessage$?.role?.get() === 'assistant';
    const nextIsAssistant = nextMessage$?.role?.get() === 'assistant';
    let visualChain = chain;
    if (prevIsAssistant && (visualChain === 'middle' || visualChain === 'end')) {
      visualChain = visualChain === 'middle' ? 'start' : 'standalone';
    }
    if (nextIsAssistant && (visualChain === 'middle' || visualChain === 'start')) {
      visualChain = visualChain === 'middle' ? 'end' : 'standalone';
    }
    return visualChain;
  });

  const messageClasses$ = useObservable(() => {
    const isAssistant = isAssistant$.get();

    // Assistant messages: borderless, page-native surface with subtle hover highlight
    if (isAssistant) {
      return 'text-foreground rounded-lg hover:bg-accent/30 transition-colors';
    }

    // Role-specific background + border colors
    const roleClasses = isUser$.get()
      ? 'bg-[#EAF4FF] text-black dark:bg-[#2A3441] dark:text-white ml-auto w-fit max-w-full'
      : isSystem$.get()
        ? 'font-mono border ' +
          (isError$.get()
            ? 'bg-[#FFF2F2] text-red-600 dark:bg-[#440000] dark:text-red-300 border-red-400 dark:border-red-800'
            : isSuccess$.get()
              ? 'bg-[#F0FDF4] text-green-700 dark:bg-[#003300] dark:text-green-200 border-green-400 dark:border-green-800'
              : 'bg-[#DDD] text-[#111] dark:bg-[#111] dark:text-gray-100 border-gray-200 dark:border-gray-800')
        : 'bg-card';

    // Chain rounding + borders for non-assistant messages
    const visualChain = visualChain$.get();
    const chainClasses = [
      (visualChain === 'standalone' && 'rounded-lg') || '',
      (visualChain === 'start' && 'rounded-t-lg') || '',
      (visualChain === 'end' && 'rounded-b-lg') || '',
      (visualChain !== 'start' && visualChain !== 'standalone' && 'border-t-0') || '',
      'border',
    ].join(' ');

    return `${roleClasses} ${chainClasses}`;
  });
  const wrapperClasses$ = useObservable(() => {
    const isAssistant = isAssistant$.get();

    // Assistant messages get consistent spacing (not tight chain spacing)
    if (isAssistant) {
      return 'mt-2 mb-2';
    }

    // Use visual chain to avoid tight overlap with borderless assistant messages
    const visualChain = visualChain$.get();
    return `
        ${visualChain !== 'start' && visualChain !== 'standalone' ? '-mt-[2px]' : 'mt-4'}
        ${visualChain === 'standalone' ? 'mb-4' : 'mb-0'}
      `;
  });

  return (
    <Memo>
      {() => {
        return (
          <div className={`role-${message$.role.get()} ${wrapperClasses$.get()}`}>
            <div className="mx-auto max-w-3xl px-4">
              <div className="relative">
                <MessageAvatar
                  role$={message$.role}
                  isError$={isError$}
                  isSuccess$={isSuccess$}
                  chainType$={chainType$}
                  agentAvatarUrl={agentAvatarUrl}
                  agentName={agentName}
                  userAvatarUrl={
                    api.userInfo$.avatar?.get()
                      ? `${connectionConfig.baseUrl.replace(/\/+$/, '')}/api/v2/user/avatar`
                      : undefined
                  }
                  userName={api.userInfo$.name?.get()}
                />
                <div className="md:px-12">
                  <div className={`group/message relative ${messageClasses$.get()}`}>
                    {/* Action buttons (top-right) */}
                    <div className="absolute right-1 top-1 z-10 flex gap-0.5 opacity-0 transition-opacity hover:!opacity-100 group-hover/message:opacity-50">
                      {onEdit &&
                        messageIndex !== undefined &&
                        message$.role.get() === 'user' &&
                        !isEditing$.get() && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              editContent$.set(message$.content.get());
                              editAutoFocus$.set(true);
                              isEditing$.set(true);
                            }}
                            className="h-7 w-7 p-0"
                            aria-label="Edit message"
                          >
                            <Pencil size={14} />
                          </Button>
                        )}
                      {messageIndex !== undefined && message$.role.get() === 'assistant' && (
                        <>
                          {onRerun && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onRerun(messageIndex)}
                              className="h-7 w-7 p-0"
                              aria-label="Re-run tools"
                              title="Re-run tools"
                            >
                              <Play size={14} />
                            </Button>
                          )}
                          {onRegenerate && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onRegenerate(messageIndex)}
                              className="h-7 w-7 p-0"
                              aria-label="Regenerate"
                              title="Regenerate response"
                            >
                              <RefreshCw size={14} />
                            </Button>
                          )}
                        </>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={handleCopy}
                        className="h-7 w-7 p-0"
                        aria-label="Copy message"
                      >
                        {copied$.get() ? <Check size={14} /> : <Clipboard size={14} />}
                      </Button>
                      {onDelete &&
                        messageIndex !== undefined &&
                        message$.role.get() !== 'system' && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              if (
                                window.confirm(
                                  'Delete this message? A backup branch will be created.'
                                )
                              ) {
                                onDelete(messageIndex);
                              }
                            }}
                            className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                            aria-label="Delete message"
                            title="Delete message"
                          >
                            <Trash2 size={14} />
                          </Button>
                        )}
                    </div>
                    <div className="px-3 py-1.5">
                      {isEditing$.get() ? (
                        <ChatInput
                          conversationId={conversationId}
                          autoFocus$={editAutoFocus$}
                          value={editContent$.get()}
                          onChange={(v) => editContent$.set(v)}
                          editMode
                          editFiles={message$.files?.get()?.map(String)}
                          onEditSave={(content, files, pendingFiles, truncate) => {
                            onEdit?.(messageIndex!, content, truncate, files, pendingFiles);
                            isEditing$.set(false);
                          }}
                          onEditCancel={() => isEditing$.set(false)}
                        />
                      ) : (
                        <>
                          <Memo>
                            {() => {
                              const isEmptyAssistantMessage =
                                message$.role.get() === 'assistant' && !message$.content.get();

                              return (
                                <div
                                  ref={contentCallbackRef}
                                  className="chat-message prose prose-sm dark:prose-invert prose-pre:overflow-x-auto prose-pre:max-w-[calc(100vw-16rem)]"
                                >
                                  {isEmptyAssistantMessage && (
                                    <span className="text-muted-foreground">Thinking...</span>
                                  )}
                                </div>
                              );
                            }}
                          </Memo>
                          {renderFiles()}
                        </>
                      )}
                    </div>
                    {/* Failed message indicator */}
                    <Memo>
                      {() => {
                        const msg = message$.get() as Message;
                        if (msg._status !== 'failed') return null;
                        return (
                          <div className="flex items-center gap-2 px-3 py-1 text-xs text-destructive">
                            <AlertCircle className="h-3 w-3" />
                            <span>{msg._error || 'Failed to send'}</span>
                            {onRetry && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-5 gap-1 px-1.5 text-xs text-destructive hover:text-destructive"
                                onClick={() => onRetry(msg)}
                              >
                                <RotateCcw className="h-3 w-3" />
                                Retry
                              </Button>
                            )}
                          </div>
                        );
                      }}
                    </Memo>
                    <Memo>
                      {() => {
                        const msg = message$.get() as Message;
                        const timestamp = msg?.timestamp;
                        const metadata = msg?.metadata;
                        if (!timestamp && !metadata) return null;

                        const time = timestamp ? formatTimestamp(timestamp) : null;
                        const model = metadata?.model;
                        const cost = metadata?.cost;
                        const usage = metadata?.usage;

                        // Build the short inline label: "model · time"
                        const parts: string[] = [];
                        if (model) parts.push(model);
                        if (time?.short) parts.push(time.short);
                        const shortLabel = parts.join(' · ');
                        if (!shortLabel) return null;

                        // Build rich tooltip content
                        const tooltipLines: string[] = [];
                        if (time?.full) tooltipLines.push(time.full);
                        if (model) tooltipLines.push(`Model: ${model}`);
                        if (cost != null) tooltipLines.push(`Cost: $${cost.toFixed(4)}`);
                        if (usage) {
                          if (usage.input_tokens)
                            tooltipLines.push(
                              `Input: ${usage.input_tokens.toLocaleString()} tokens`
                            );
                          if (usage.output_tokens)
                            tooltipLines.push(
                              `Output: ${usage.output_tokens.toLocaleString()} tokens`
                            );
                          if (usage.cache_read_tokens)
                            tooltipLines.push(
                              `Cache read: ${usage.cache_read_tokens.toLocaleString()} tokens`
                            );
                          if (usage.cache_creation_tokens)
                            tooltipLines.push(
                              `Cache write: ${usage.cache_creation_tokens.toLocaleString()} tokens`
                            );
                        }

                        return (
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <div className="h-0 select-none overflow-visible px-3 text-right text-[10px] leading-4 text-muted-foreground/50 opacity-0 transition-opacity group-hover/message:opacity-100">
                                  {shortLabel}
                                </div>
                              </TooltipTrigger>
                              <TooltipContent side="bottom" className="max-w-xs">
                                <div className="space-y-0.5 text-xs">
                                  {tooltipLines.map((line, i) => (
                                    <div key={i}>{line}</div>
                                  ))}
                                </div>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        );
                      }}
                    </Memo>
                  </div>
                </div>
              </div>
            </div>
          </div>
        );
      }}
    </Memo>
  );
};
