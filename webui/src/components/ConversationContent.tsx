import type { FC } from 'react';
import { useRef, useEffect, useState, useCallback } from 'react';
import { ChatMessage } from './ChatMessage';
import { ChatInput, type ChatOptions } from './ChatInput';
import { CollapsedStepGroup } from './CollapsedStepGroup';
import { useConversation } from '@/hooks/useConversation';
import { BranchIndicator } from './BranchIndicator';
import { computeForkPoints } from '@/utils/branchUtils';
import { buildStepRoles, type StepRole } from '@/utils/stepGrouping';

import { InlineToolConfirmation } from './InlineToolConfirmation';
import { InlineToolExecution } from './InlineToolExecution';
import { For, Memo, use$, useObservable, useObserveEffect } from '@legendapp/state/react';
import { getObservableIndex } from '@legendapp/state';
import { useApi } from '@/contexts/ApiContext';
import { useSettings } from '@/contexts/SettingsContext';
import { useModels } from '@/hooks/useModels';
import { ArrowDown } from 'lucide-react';

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
  // State to track when to auto-focus the input
  const shouldFocus$ = useObservable(false);
  // Store the previous conversation ID to detect changes
  const prevConversationIdRef = useRef<string | null>(null);

  const { api, connectionConfig } = useApi();
  const hasSession$ = useObservable<boolean>(false);
  const { defaultModel } = useModels();

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
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());

  // Reset expanded state when switching conversations (groupIds reset to 0 per conversation)
  useEffect(() => {
    setExpandedGroups(new Set());
  }, [conversationId]);

  const toggleGroup = useCallback((groupId: number) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }, []);

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

  // Scroll to the bottom when the conversation is updated
  useObserveEffect(conversation$.data.log, () => {
    const scrollToBottom = () => {
      if (scrollContainerRef.current) {
        isAutoScrolling$.set(true);
        scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
        requestAnimationFrame(() => {
          isAutoScrolling$.set(false);
        });
      }
    };

    if (!autoScrollAborted$.get()) {
      requestAnimationFrame(scrollToBottom);
    }
  });

  // Scroll to the bottom when switching conversations
  useEffect(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, [conversationId]);

  const handleSendMessage = (message: string, options?: ChatOptions) => {
    sendMessage({ message, options });
  };

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
    if (scrollContainerRef.current) {
      isAutoScrolling$.set(true);
      scrollContainerRef.current.scrollTo({
        top: scrollContainerRef.current.scrollHeight,
        behavior: 'smooth',
      });
      scrollContainerRef.current.addEventListener('scrollend', () => isAutoScrolling$.set(false), {
        once: true,
      });
    }
    autoScrollAborted$.set(false);
    isScrolledUp$.set(false);
  };

  if (!conversation$) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-muted-foreground">Loading conversation...</div>
      </div>
    );
  }

  return (
    <main className="relative flex h-full flex-col">
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

            // Helper to check if a message at a given index is hidden
            const isHiddenAt = (idx: number) => {
              const m = conversation$.data.log[idx];
              if (!m?.get()) return false;
              const r = m.role.get();
              const h = m.hide?.get();
              const isInitial =
                r === 'system' && (firstNonSystemIndex === -1 || idx < firstNonSystemIndex);
              if (isInitial && !showInitialSystem$.get()) return true;
              if (h && !showHiddenMessages$.get()) return true;
              return false;
            };

            // Get the previous and next *visible* messages for chain context
            // (skip hidden messages so they don't break chain grouping)
            let prevIdx = index - 1;
            while (prevIdx >= 0 && isHiddenAt(prevIdx)) prevIdx--;
            const previousMessage$ = prevIdx >= 0 ? conversation$.data.log[prevIdx] : undefined;

            let nextIdx = index + 1;
            while (conversation$.data.log[nextIdx]?.get() && isHiddenAt(nextIdx)) nextIdx++;
            const nextMessage$ = conversation$.data.log[nextIdx]?.get()
              ? conversation$.data.log[nextIdx]
              : undefined;

            // Step grouping: check if this message should be collapsed
            const stepRole = stepRoles$.get().get(index);

            // If this is a grouped message and the group is collapsed, hide it
            if (stepRole?.type === 'grouped' && !expandedGroups.has(stepRole.groupId)) {
              return <div key={`${index}-${msg$.timestamp.get()}`} />;
            }

            // If this is a group-start, render the summary bar
            // (when collapsed, replaces the message; when expanded, shown above messages)
            const groupSummary =
              stepRole?.type === 'group-start' ? (
                <CollapsedStepGroup
                  count={stepRole.count}
                  tools={stepRole.tools}
                  isExpanded={expandedGroups.has(stepRole.groupId)}
                  onToggle={() => toggleGroup(stepRole.groupId)}
                />
              ) : null;

            // When group is collapsed and this is group-start, show only the summary bar
            if (stepRole?.type === 'group-start' && !expandedGroups.has(stepRole.groupId)) {
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
              <div key={`${index}-${msg$.timestamp.get()}`}>
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
