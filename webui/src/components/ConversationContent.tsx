import type { FC } from 'react';
import { useRef, useEffect, useCallback, useState } from 'react';
import { ChatMessage } from './ChatMessage';
import { ChatInput, type ChatOptions } from './ChatInput';
import { CollapsedStepGroup } from './CollapsedStepGroup';
import { useConversation } from '@/hooks/useConversation';
import { BranchIndicator } from './BranchIndicator';
import { computeForkPoints } from '@/utils/branchUtils';
import { buildStepRoles, type StepRole } from '@/utils/stepGrouping';

import { InlineToolConfirmation } from './InlineToolConfirmation';
import { MessageSearchBar } from './MessageSearchBar';
import { InlineToolExecution, ToolCompletionBadge } from './InlineToolExecution';
import { OpenConversationPathButton } from './OpenConversationPathButton';
import { For, Memo, use$, useObservable, useObserveEffect } from '@legendapp/state/react';
import { getObservableIndex } from '@legendapp/state';
import { useApi } from '@/contexts/ApiContext';
import { useSettings } from '@/contexts/SettingsContext';
import { useModels } from '@/hooks/useModels';
import { ArrowDown, RefreshCw, WifiOff } from 'lucide-react';

interface Props {
  conversationId: string;
  serverId?: string;
  isReadOnly?: boolean;
}

export const ConversationContent: FC<Props> = ({ conversationId, serverId, isReadOnly }) => {
  const {
    conversation$,
    sendMessage,
    retryMessage,
    editMessage,
    deleteMessage,
    rerunFromMessage,
    regenerateMessage,
    switchBranch,
    confirmTool,
    interruptGeneration,
  } = useConversation(conversationId, serverId);
  const connectionStatus = use$(() => conversation$?.connectionStatus.get() ?? 'disconnected');
  const reconnectAttempt = use$(() => conversation$?.reconnectAttempt.get() ?? null);
  const reconnectMaxAttempts = use$(() => conversation$?.reconnectMaxAttempts.get() ?? null);
  const reconnectRetryInMs = use$(() => conversation$?.reconnectRetryInMs.get() ?? null);
  const connectionError = use$(() => conversation$?.connectionError.get() ?? null);
  // State to track when to auto-focus the input
  const shouldFocus$ = useObservable(false);
  // Store the previous conversation ID to detect changes
  const prevConversationIdRef = useRef<string | null>(null);

  const { api, connectionConfig } = useApi();
  const hasSession$ = useObservable<boolean>(false);
  const { defaultModel } = useModels();

  // Message search state — declared early so keyboard handlers can reference them
  const searchVisible$ = useObservable(false);
  const searchQuery$ = useObservable('');
  const searchMatchIndices$ = useObservable<number[]>([]);
  const searchCurrentMatch$ = useObservable(0);

  // Fetch user info once (cached in ApiClient)
  useEffect(() => {
    if (api.isConnected$.get()) {
      api.getUserInfo().catch(() => {});
    }
  }, [api]);

  useObserveEffect(api.sessions$.get(conversationId), () => {
    if (!isReadOnly) {
      hasSession$.set(api.sessions$.get(conversationId).get() !== undefined);
    }
  });

  // Detect when the conversation changes and set focus
  useEffect(() => {
    if (conversationId !== prevConversationIdRef.current) {
      // New conversation detected - set focus flag
      shouldFocus$.set(true);
      // Store the current conversation ID for future comparisons
      prevConversationIdRef.current = conversationId;
    }
  }, [conversationId, shouldFocus$]);

  // Add keyboard shortcut for focusing the input
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Only handle 'i' key when:
      // - Not in an input/textarea
      // - Not in read-only mode
      // - Has an active session
      if (
        e.key === 'i' &&
        !isReadOnly &&
        hasSession$.get() &&
        !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault();
        shouldFocus$.set(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isReadOnly, hasSession$, shouldFocus$]);

  // Ctrl+F / Cmd+F to open message search (or re-focus if already open)
  useEffect(() => {
    const handleSearchKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        if (searchVisible$.get()) {
          document.querySelector<HTMLInputElement>('[data-search-input]')?.focus();
        } else {
          searchVisible$.set(true);
        }
      }
    };
    window.addEventListener('keydown', handleSearchKeyDown);
    return () => window.removeEventListener('keydown', handleSearchKeyDown);
  }, [searchVisible$]);

  const firstNonSystemIndex$ = useObservable(() => {
    return conversation$.get()?.data.log.findIndex((msg) => msg.role !== 'system') || 0;
  });

  // Update the firstNonSystemIndex$ when the conversationId changes
  useEffect(() => {
    firstNonSystemIndex$.set(
      conversation$.get()?.data.log.findIndex((msg) => msg.role !== 'system') || 0
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // Import settings from global context
  const { settings } = useSettings();

  // Create observables for settings that need to be reactive in the For loop
  // (Legend State's <For> only re-renders on observable changes, not React state)
  const showInitialSystem$ = useObservable(settings.showInitialSystem);
  const showHiddenMessages$ = useObservable(settings.showHiddenMessages);

  // Sync observables when settings change
  useEffect(() => {
    showInitialSystem$.set(settings.showInitialSystem);
  }, [settings.showInitialSystem, showInitialSystem$]);

  useEffect(() => {
    showHiddenMessages$.set(settings.showHiddenMessages);
  }, [settings.showHiddenMessages, showHiddenMessages$]);

  // Step grouping: compute roles and track expanded groups
  const stepRoles$ = useObservable<Map<number, StepRole>>(() => new Map());
  // Must be an observable (not React state) so changes trigger re-renders inside <For>
  const expandedGroups$ = useObservable<Set<number>>(new Set<number>());

  // Reset expanded state when switching conversations
  useEffect(() => {
    expandedGroups$.set(new Set<number>());
  }, [conversationId, expandedGroups$]);

  const toggleGroup = (groupId: number) => {
    const prev = expandedGroups$.get();
    const next = new Set(prev);
    if (next.has(groupId)) {
      next.delete(groupId);
    } else {
      next.add(groupId);
    }
    expandedGroups$.set(next);
  };

  // Recompute step roles when messages or visibility settings change.
  // All .get() calls inside are auto-tracked, so this re-runs when any of
  // conversation log, showHiddenMessages, showInitialSystem, or firstNonSystemIndex changes.
  useObserveEffect(() => {
    const messages = conversation$.data.log.get();
    if (!messages?.length) {
      stepRoles$.set(new Map());
      return;
    }

    const firstNonSystem = firstNonSystemIndex$.get();
    const showInitial = showInitialSystem$.get();
    const showHidden = showHiddenMessages$.get();

    const isHidden = (idx: number) => {
      const msg = messages[idx];
      if (!msg) return false;
      const isInitial = msg.role === 'system' && (firstNonSystem === -1 || idx < firstNonSystem);
      if (isInitial && !showInitial) return true;
      if (msg.hide && !showHidden) return true;
      return false;
    };

    stepRoles$.set(buildStepRoles(messages, isHidden));
  });

  // Create a ref for the scroll container
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Observable for if the conversation is auto-scrolling
  const isAutoScrolling$ = useObservable(false);

  // Observable for if the user scrolled during generation
  const autoScrollAborted$ = useObservable(false);

  // Observable for if the user is scrolled away from the bottom
  // (used to show the scroll-to-bottom button)
  const isScrolledUp$ = useObservable(false);

  // Compute fork points once (reactive: recomputes when branches/currentBranch change)
  const forkPoints$ = useObservable(() => {
    const branches = conversation$.data.branches?.get();
    const currentBranch = conversation$.currentBranch?.get() || 'main';
    if (!branches || Object.keys(branches).length <= 1) return new Map();
    return computeForkPoints(currentBranch, branches);
  });

  // Reset the autoScrollAborted flag when generation is complete or starts again
  useObserveEffect(conversation$?.isGenerating, () => {
    autoScrollAborted$.set(false);
  });

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    isAutoScrolling$.set(true);
    container.scrollTop = container.scrollHeight;
    requestAnimationFrame(() => {
      isAutoScrolling$.set(false);
    });
  }, [isAutoScrolling$]);

  // Auto-scroll when the conversation is updated (e.g., streaming response)
  useObserveEffect(conversation$.data.log, () => {
    if (!autoScrollAborted$.get()) {
      requestAnimationFrame(scrollToBottom);
    }
  });

  // Scroll to bottom when switching conversations so the latest response is visible
  useEffect(() => {
    requestAnimationFrame(scrollToBottom);
  }, [conversationId, scrollToBottom]);

  const handleSendMessage = (message: string, options?: ChatOptions) => {
    sendMessage({ message, options });
  };

  const clearSearchHighlights = useCallback(() => {
    scrollContainerRef.current
      ?.querySelectorAll<HTMLElement>('[data-message-index]')
      .forEach((el) => {
        el.style.outline = '';
        el.style.outlineOffset = '';
      });
  }, [scrollContainerRef]);

  const isMessageHidden = useCallback(
    (idx: number) => {
      const messages = conversation$.data.log.get();
      const msg = messages?.[idx];
      if (!msg) return false;

      const firstNonSystemIndex = firstNonSystemIndex$.get();
      const isInitialSystem =
        msg.role === 'system' && (firstNonSystemIndex === -1 || idx < firstNonSystemIndex);
      if (isInitialSystem && !showInitialSystem$.get()) return true;
      if (msg.hide && !showHiddenMessages$.get()) return true;

      const stepRole = stepRoles$.get().get(idx);
      if (
        (stepRole?.type === 'group-start' || stepRole?.type === 'grouped') &&
        !expandedGroups$.get().has(stepRole.groupId)
      ) {
        return true;
      }

      return false;
    },
    [
      conversation$,
      expandedGroups$,
      firstNonSystemIndex$,
      showHiddenMessages$,
      showInitialSystem$,
      stepRoles$,
    ]
  );

  // Search helpers: imperative DOM highlight + scroll, avoids re-rendering all messages
  const highlightSearchMatch = useCallback(
    (msgIndex: number) => {
      clearSearchHighlights();
      const el = scrollContainerRef.current?.querySelector<HTMLElement>(
        `[data-message-index="${msgIndex}"]`
      );
      if (el) {
        el.style.outline = '2px solid rgba(234,179,8,0.6)';
        el.style.outlineOffset = '-2px';
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    },
    [clearSearchHighlights, scrollContainerRef]
  );

  const computeSearchMatches = useCallback(
    (query: string): number[] => {
      if (!query.trim()) return [];
      const q = query.toLowerCase();
      const messages = conversation$.data.log.get();
      if (!messages) return [];
      return messages
        .map((msg, i) => {
          const content = typeof msg.content === 'string' ? msg.content.toLowerCase() : '';
          return !isMessageHidden(i) && content.includes(q) ? i : -1;
        })
        .filter((i) => i >= 0);
    },
    [conversation$, isMessageHidden]
  );

  const resetSearchState = useCallback(() => {
    searchVisible$.set(false);
    searchQuery$.set('');
    searchMatchIndices$.set([]);
    searchCurrentMatch$.set(0);
    clearSearchHighlights();
  }, [
    clearSearchHighlights,
    searchCurrentMatch$,
    searchMatchIndices$,
    searchQuery$,
    searchVisible$,
  ]);

  const handleSearchQueryChange = useCallback(
    (query: string) => {
      searchQuery$.set(query);
      const matches = computeSearchMatches(query);
      searchMatchIndices$.set(matches);
      searchCurrentMatch$.set(0);
      if (matches.length > 0) highlightSearchMatch(matches[0]);
      else clearSearchHighlights();
    },
    [
      clearSearchHighlights,
      searchQuery$,
      searchMatchIndices$,
      searchCurrentMatch$,
      computeSearchMatches,
      highlightSearchMatch,
    ]
  );

  const handleSearchNext = useCallback(() => {
    const matches = searchMatchIndices$.get();
    if (!matches.length) return;
    const next = (searchCurrentMatch$.get() + 1) % matches.length;
    searchCurrentMatch$.set(next);
    highlightSearchMatch(matches[next]);
  }, [searchMatchIndices$, searchCurrentMatch$, highlightSearchMatch]);

  const handleSearchPrev = useCallback(() => {
    const matches = searchMatchIndices$.get();
    if (!matches.length) return;
    const prev = (searchCurrentMatch$.get() - 1 + matches.length) % matches.length;
    searchCurrentMatch$.set(prev);
    highlightSearchMatch(matches[prev]);
  }, [searchMatchIndices$, searchCurrentMatch$, highlightSearchMatch]);

  const handleSearchClose = useCallback(() => {
    resetSearchState();
  }, [resetSearchState]);

  useEffect(() => {
    resetSearchState();
  }, [conversationId, resetSearchState]);

  // Handle tool confirmation
  const handleConfirmTool = async () => {
    await confirmTool('confirm');
  };

  const handleEditTool = async (content: string) => {
    await confirmTool('edit', { content });
  };

  const handleSkipTool = async () => {
    await confirmTool('skip');
  };

  const handleAutoConfirmTool = async (count: number) => {
    await confirmTool('auto', { count });
  };

  const handleScrollToBottom = () => {
    const container = scrollContainerRef.current;
    if (container) {
      isAutoScrolling$.set(true);
      container.scrollTo({
        top: container.scrollHeight,
        behavior: 'smooth',
      });
      container.addEventListener('scrollend', () => isAutoScrolling$.set(false), {
        once: true,
      });
    }
    autoScrollAborted$.set(false);
    isScrolledUp$.set(false);
  };

  const showConnectionBanner =
    !isReadOnly && (connectionStatus === 'reconnecting' || connectionStatus === 'disconnected');

  // Live countdown timer — decrements every second while reconnecting
  const [reconnectRetrySeconds, setReconnectRetrySeconds] = useState<number | null>(null);
  useEffect(() => {
    if (connectionStatus !== 'reconnecting' || !reconnectRetryInMs) {
      setReconnectRetrySeconds(null);
      return;
    }
    // Compute remaining seconds from the retry interval
    const computeRemaining = () => {
      if (!conversation$?.reconnectRetryStartedAt?.get()) return null;
      const elapsed = Date.now() - conversation$.reconnectRetryStartedAt.get()!;
      const remaining = Math.max(0, reconnectRetryInMs! - elapsed);
      return Math.ceil(remaining / 1000);
    };
    setReconnectRetrySeconds(computeRemaining());
    const interval = setInterval(() => {
      const remaining = computeRemaining();
      if (remaining !== null && remaining <= 0) {
        setReconnectRetrySeconds(null);
        clearInterval(interval);
      } else {
        setReconnectRetrySeconds(remaining);
      }
    }, 250); // update 4×/s for smooth countdown
    return () => clearInterval(interval);
  }, [connectionStatus, reconnectRetryInMs, conversation$]);

  if (!conversation$) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-muted-foreground">Loading conversation...</div>
      </div>
    );
  }

  return (
    <main className="relative flex h-full flex-col">
      <Memo>
        {() =>
          searchVisible$.get() ? (
            <MessageSearchBar
              query={searchQuery$.get()}
              matchCount={searchMatchIndices$.get().length}
              currentMatch={searchCurrentMatch$.get() + 1}
              onQueryChange={handleSearchQueryChange}
              onNext={handleSearchNext}
              onPrev={handleSearchPrev}
              onClose={handleSearchClose}
            />
          ) : null
        }
      </Memo>
      <div
        className="flex-1 overflow-y-auto"
        ref={scrollContainerRef}
        onScroll={() => {
          if (!scrollContainerRef.current || isAutoScrolling$.get()) return;
          const isBottom =
            Math.abs(
              scrollContainerRef.current.scrollHeight -
                (scrollContainerRef.current.scrollTop + scrollContainerRef.current.clientHeight)
            ) <= 1;
          if (isBottom) {
            autoScrollAborted$.set(false);
            isScrolledUp$.set(false);
          } else {
            autoScrollAborted$.set(true);
            isScrolledUp$.set(true);
          }
        }}
      >
        <Memo>
          {() => (
            <OpenConversationPathButton
              logdir={conversation$.data.logdir.get()}
              baseUrl={connectionConfig.baseUrl}
            />
          )}
        </Memo>

        <For each={conversation$.data.log}>
          {(msg$) => {
            const index = getObservableIndex(msg$);
            // Hide all system messages before the first non-system message by default
            const firstNonSystemIndex = firstNonSystemIndex$.get();
            const isInitialSystem =
              msg$.role.get() === 'system' &&
              (firstNonSystemIndex === -1 || index < firstNonSystemIndex);
            if (isInitialSystem && !showInitialSystem$.get()) {
              return <div key={`${index}-${msg$.timestamp.get()}`} />;
            }

            // Hide messages with hide=true (e.g., auto-included lessons)
            if (msg$.hide?.get() && !showHiddenMessages$.get()) {
              return <div key={`${index}-${msg$.timestamp.get()}`} />;
            }

            // Get the previous and next *visible* messages for chain context
            // (skip hidden messages so they don't break chain grouping)
            let prevIdx = index - 1;
            while (prevIdx >= 0 && isMessageHidden(prevIdx)) prevIdx--;
            const previousMessage$ = prevIdx >= 0 ? conversation$.data.log[prevIdx] : undefined;

            let nextIdx = index + 1;
            while (conversation$.data.log[nextIdx]?.get() && isMessageHidden(nextIdx)) nextIdx++;
            const nextMessage$ = conversation$.data.log[nextIdx]?.get()
              ? conversation$.data.log[nextIdx]
              : undefined;

            // Step grouping: check if this message should be collapsed
            const stepRole = stepRoles$.get().get(index);

            // If this is a grouped message and the group is collapsed, hide it
            if (stepRole?.type === 'grouped' && !expandedGroups$.get().has(stepRole.groupId)) {
              return <div key={`${index}-${msg$.timestamp.get()}`} />;
            }

            // If this is a group-start, render the summary bar
            // (when collapsed, replaces the message; when expanded, shown above messages)
            const groupSummary =
              stepRole?.type === 'group-start' ? (
                <CollapsedStepGroup
                  count={stepRole.count}
                  tools={stepRole.tools}
                  steps={stepRole.steps}
                  isExpanded={expandedGroups$.get().has(stepRole.groupId)}
                  onToggle={() => toggleGroup(stepRole.groupId)}
                />
              ) : null;

            // When group is collapsed and this is group-start, show only the summary bar
            if (stepRole?.type === 'group-start' && !expandedGroups$.get().has(stepRole.groupId)) {
              return <div key={`${index}-${msg$.timestamp.get()}`}>{groupSummary}</div>;
            }

            // Construct agent avatar URL if agent has avatar configured
            // NOTE: must use .get() to read actual values from Legend State observables
            const baseUrl = connectionConfig.baseUrl.replace(/\/+$/, '');
            const agentAvatarUrl = conversation$.data.agent?.avatar?.get()
              ? `${baseUrl}/api/v2/conversations/${conversationId}/agent/avatar`
              : undefined;
            const agentName = conversation$.data.agent?.name?.get();

            return (
              <div key={`${index}-${msg$.timestamp.get()}`} data-message-index={index}>
                {/* Show summary bar above first message when group is expanded */}
                {groupSummary}
                <ChatMessage
                  message$={msg$}
                  previousMessage$={previousMessage$}
                  nextMessage$={nextMessage$}
                  conversationId={conversationId}
                  agentAvatarUrl={agentAvatarUrl}
                  agentName={agentName}
                  onRetry={isReadOnly ? undefined : retryMessage}
                  onEdit={isReadOnly ? undefined : editMessage}
                  onDelete={isReadOnly ? undefined : deleteMessage}
                  onRerun={isReadOnly ? undefined : rerunFromMessage}
                  onRegenerate={isReadOnly ? undefined : regenerateMessage}
                  messageIndex={index}
                />
                {/* Branch indicator at fork points */}
                <Memo>
                  {() => {
                    const forkInfo = forkPoints$.get().get(index);
                    if (!forkInfo) return null;
                    return (
                      <div className="mx-auto max-w-3xl">
                        <div className="md:px-12">
                          <BranchIndicator forkInfo={forkInfo} onSwitchBranch={switchBranch} />
                        </div>
                      </div>
                    );
                  }}
                </Memo>
              </div>
            );
          }}
        </For>

        {/* Inline Tool Confirmation */}
        <InlineToolConfirmation
          pendingTool$={conversation$?.pendingTool}
          onConfirm={handleConfirmTool}
          onEdit={handleEditTool}
          onSkip={handleSkipTool}
          onAuto={handleAutoConfirmTool}
        />

        {/* Inline Tool Execution */}
        <InlineToolExecution executingTool$={conversation$?.executingTool} />

        {/* Tool completion badge — briefly shows after tool finishes */}
        <ToolCompletionBadge lastCompletedTool$={conversation$?.lastCompletedTool} />

        {/* Add padding at the bottom to account for the floating input */}
        <div className="mb-40" />
      </div>

      {/* Scroll-to-bottom button — appears when user scrolls up from bottom */}
      {use$(isScrolledUp$) && (
        <button
          onClick={handleScrollToBottom}
          className="absolute bottom-44 right-6 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-border/50 bg-background/90 text-muted-foreground shadow-md transition-colors hover:bg-accent hover:text-accent-foreground"
          aria-label="Scroll to bottom"
        >
          <ArrowDown className="h-4 w-4" />
        </button>
      )}

      {showConnectionBanner && (
        <div className="absolute bottom-28 left-1/2 z-20 w-[min(calc(100%_-_2rem),42rem)] -translate-x-1/2 rounded-md border border-border bg-background/95 px-3 py-2 text-sm shadow-sm">
          {connectionStatus === 'reconnecting' ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <RefreshCw className="h-4 w-4 shrink-0 animate-spin text-amber-500" />
              <span className="truncate">
                Reconnecting event stream
                {reconnectAttempt && reconnectMaxAttempts
                  ? ` (${reconnectAttempt}/${reconnectMaxAttempts})`
                  : ''}
                {reconnectRetrySeconds ? ` in ${reconnectRetrySeconds}s` : ''}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-muted-foreground">
              <WifiOff className="h-4 w-4 shrink-0 text-destructive" />
              <span className="truncate">{connectionError || 'Event stream disconnected'}</span>
            </div>
          )}
        </div>
      )}

      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-background via-background/80 to-transparent">
        <div className=" mx-auto max-w-2xl">
          <ChatInput
            conversationId={conversationId}
            onSend={handleSendMessage}
            onInterrupt={interruptGeneration}
            isReadOnly={isReadOnly}
            defaultModel={defaultModel || undefined}
            autoFocus$={shouldFocus$}
          />
        </div>
      </div>
    </main>
  );
};
