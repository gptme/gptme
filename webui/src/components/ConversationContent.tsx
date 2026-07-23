import type { FC } from 'react';
import { useRef, useEffect, useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ChatMessage } from './ChatMessage';
import { ChatInput, type ChatOptions } from './ChatInput';
import { CollapsedStepGroup } from './CollapsedStepGroup';
import { useConversation } from '@/hooks/useConversation';
import { BranchIndicator } from './BranchIndicator';
import { computeForkPoints } from '@/utils/branchUtils';
import { buildStepRoles, type StepRole } from '@/utils/stepGrouping';
import type { Message } from '@/types/conversation';

import { InlineToolConfirmation } from './InlineToolConfirmation';
import { MessageSearchBar } from './MessageSearchBar';
import { InlineToolExecution, ToolCompletionBadge } from './InlineToolExecution';
import { OpenConversationPathButton } from './OpenConversationPathButton';
import { For, Memo, use$, useObservable, useObserveEffect } from '@legendapp/state/react';
import { getObservableIndex } from '@legendapp/state';
import { useApi } from '@/contexts/ApiContext';
import { useSettings } from '@/contexts/SettingsContext';
import { useModels } from '@/hooks/useModels';
import { chatRoute } from '@/utils/routes';
import { AlertTriangle, ArrowDown, ChevronUp, RefreshCw, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { isDemoMode } from '@/utils/connectionConfig';
import { isLikelyChromeCorsPna } from '@/utils/api';

interface Props {
  conversationId: string;
  serverId?: string;
  isReadOnly?: boolean;
}

export const ConversationContent: FC<Props> = ({ conversationId, serverId, isReadOnly }) => {
  const {
    conversation$,
    retryLoad,
    sendMessage,
    retryMessage,
    editMessage,
    deleteMessage,
    rerunFromMessage,
    regenerateMessage,
    forkConversation,
    switchBranch,
    confirmTool,
    interruptGeneration,
    isLoadingOlderMessages,
    loadOlderMessages,
  } = useConversation(conversationId, serverId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const loadError = use$(() => conversation$?.loadError.get() ?? null);
  const messageCount = use$(() => conversation$?.data.log.get()?.length ?? 0);
  const connectionStatus = use$(() => conversation$?.connectionStatus.get() ?? 'disconnected');
  const reconnectAttempt = use$(() => conversation$?.reconnectAttempt.get() ?? null);
  const reconnectMaxAttempts = use$(() => conversation$?.reconnectMaxAttempts.get() ?? null);
  const reconnectRetryInMs = use$(() => conversation$?.reconnectRetryInMs.get() ?? null);
  const connectionError = use$(() => conversation$?.connectionError.get() ?? null);
  const hasMoreBefore = use$(() => conversation$?.hasMoreBefore.get() ?? false);
  // State to track when to auto-focus the input
  const shouldFocus$ = useObservable(false);
  // Store the previous conversation ID to detect changes
  const prevConversationIdRef = useRef<string | null>(null);
  const paneRef = useRef<HTMLElement>(null);

  const { api, connectionConfig, connect, getClient } = useApi();
  // Use the conversation's server client when serverId is provided, not the primary.
  const serverClient = serverId ? getClient(serverId) : api;
  const isConnected = use$(serverClient.isConnected$);
  const lastConnectionResult = use$(serverClient.lastConnectionResult$);
  const [isRetryingConnection, setIsRetryingConnection] = useState(false);
  const hasSession$ = useObservable<boolean>(false);
  const { defaultModel } = useModels();

  // Message search state — declared early so keyboard handlers can reference them
  const searchVisible$ = useObservable(false);
  const searchQuery$ = useObservable('');
  const searchMatchIndices$ = useObservable<number[]>([]);
  const searchCurrentMatch$ = useObservable(0);

  const activatePane = useCallback(() => {
    const pane = paneRef.current;
    if (!pane) return;

    document
      .querySelectorAll<HTMLElement>('[data-conversation-pane-active="true"]')
      .forEach((activePane) => {
        if (activePane !== pane) {
          activePane.removeAttribute('data-conversation-pane-active');
        }
      });
    pane.dataset.conversationPaneActive = 'true';
  }, []);

  const isActivePane = useCallback(() => {
    const pane = paneRef.current;
    if (!pane) return false;

    const activePane = document.querySelector<HTMLElement>(
      '[data-conversation-pane-active="true"]'
    );
    if (activePane) {
      return activePane === pane;
    }

    return document.querySelectorAll('[data-conversation-pane]').length <= 1;
  }, []);

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
      if (!isActivePane()) {
        return;
      }

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
  }, [isReadOnly, hasSession$, isActivePane, shouldFocus$]);

  // Ctrl+F / Cmd+F to open message search (or re-focus if already open)
  useEffect(() => {
    const handleSearchKeyDown = (e: KeyboardEvent) => {
      if (!isActivePane()) {
        return;
      }

      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        if (searchVisible$.get()) {
          paneRef.current?.querySelector<HTMLInputElement>('[data-search-input]')?.focus();
        } else {
          searchVisible$.set(true);
        }
      }
    };
    window.addEventListener('keydown', handleSearchKeyDown);
    return () => window.removeEventListener('keydown', handleSearchKeyDown);
  }, [isActivePane, searchVisible$]);

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;

    if (!document.querySelector('[data-conversation-pane-active="true"]')) {
      activatePane();
    }

    return () => {
      if (pane.dataset.conversationPaneActive === 'true') {
        pane.removeAttribute('data-conversation-pane-active');
      }
    };
  }, [activatePane]);

  const firstNonSystemIndex$ = useObservable(() => {
    return conversation$?.get()?.data?.log?.findIndex((msg) => msg.role !== 'system') ?? 0;
  });

  // Update the firstNonSystemIndex$ when the conversationId changes
  useEffect(() => {
    firstNonSystemIndex$.set(
      conversation$?.get()?.data?.log?.findIndex((msg) => msg.role !== 'system') ?? 0
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
  // conversation log, showHiddenMessages, showInitialSystem, firstNonSystemIndex,
  // or logOffset changes.
  useObserveEffect(() => {
    const messages = conversation$?.data.log.get();
    if (!messages?.length) {
      stepRoles$.set(new Map());
      return;
    }

    const logOffset = conversation$?.logOffset?.get() ?? 0;
    const firstNonSystem = firstNonSystemIndex$.get();
    const showInitial = showInitialSystem$.get();
    const showHidden = showHiddenMessages$.get();

    // isHidden receives LOCAL indices (array positions in messages[]).
    const isHidden = (idx: number) => {
      const msg = messages[idx];
      if (!msg) return false;
      const isInitial = msg.role === 'system' && (firstNonSystem === -1 || idx < firstNonSystem);
      if (isInitial && !showInitial) return true;
      if (msg.hide && !showHidden) return true;
      return false;
    };

    // buildStepRoles emits absolute-indexed keys (localIdx + logOffset).
    stepRoles$.set(buildStepRoles(messages as Message[], isHidden, logOffset));
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
    const branches = conversation$?.data.branches?.get();
    const currentBranch = conversation$?.currentBranch?.get() || 'main';
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
  useObserveEffect(conversation$?.data.log, () => {
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

  const handleLoadOlderMessages = useCallback(async () => {
    const container = scrollContainerRef.current;
    if (!container) {
      await loadOlderMessages();
      return;
    }

    const previousScrollHeight = container.scrollHeight;
    const previousScrollTop = container.scrollTop;
    autoScrollAborted$.set(true);
    isScrolledUp$.set(true);

    await loadOlderMessages();

    requestAnimationFrame(() => {
      container.scrollTop = previousScrollTop + (container.scrollHeight - previousScrollHeight);
    });
  }, [autoScrollAborted$, isScrolledUp$, loadOlderMessages]);

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
      // idx is a LOCAL index (array position in the current log window).
      const messages = conversation$.data.log.get();
      const msg = messages?.[idx];
      if (!msg) return false;

      const firstNonSystemIndex = firstNonSystemIndex$.get();
      const isInitialSystem =
        msg.role === 'system' && (firstNonSystemIndex === -1 || idx < firstNonSystemIndex);
      if (isInitialSystem && !showInitialSystem$.get()) return true;
      if (msg.hide && !showHiddenMessages$.get()) return true;

      // stepRoles$ is keyed by ABSOLUTE index.
      const logOffset = conversation$?.logOffset?.get() ?? 0;
      const stepRole = stepRoles$.get().get(logOffset + idx);
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
      // Read logOffset inside the callback so it's always fresh.
      const logOffset = conversation$?.logOffset?.get() ?? 0;
      return messages
        .map((msg, i) => {
          const content = typeof msg.content === 'string' ? msg.content.toLowerCase() : '';
          // Return ABSOLUTE index so highlightSearchMatch finds the right data-message-index.
          return !isMessageHidden(i) && content.includes(q) ? logOffset + i : -1;
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

  // When no-confirm mode is on, silently auto-confirm any pending tool without showing the dialog.
  const AUTO_CONFIRM_ALL = 999999;
  const pendingToolId = use$(() => conversation$?.pendingTool.get()?.id ?? null);
  useEffect(() => {
    if (pendingToolId && settings.noConfirmMode) {
      void handleAutoConfirmTool(AUTO_CONFIRM_ALL);
    }
    // Safe to omit handleAutoConfirmTool: confirmTool reads pendingTool fresh from the
    // observable store on each call, so a stale closure does not cause incorrect behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingToolId, settings.noConfirmMode]);

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

  const handleForkMessage = useCallback(
    async (index: number) => {
      const forkedConversationId = await forkConversation(index);
      if (!forkedConversationId) return;

      await queryClient.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey[0];
          return typeof key === 'string' && key.startsWith('conversation');
        },
      });

      const params = new URLSearchParams(window.location.search);
      params.delete('split');
      if (serverId) {
        params.set('server', serverId);
      } else {
        params.delete('server');
      }
      navigate(chatRoute(forkedConversationId, params.toString()));
    },
    [forkConversation, navigate, queryClient, serverId]
  );

  const showConnectionBanner =
    !isReadOnly && (connectionStatus === 'reconnecting' || connectionStatus === 'disconnected');

  // Top-of-view banner when the API server itself is unreachable (not in intentional demo mode).
  // This is distinct from the SSE-level reconnect banner above which fires after a successful
  // connection drops mid-session. This fires on load when the server was never reachable.
  const showServerDisconnectedBanner = !isConnected && !isDemoMode();

  // Classify failure reason to give actionable guidance (mirrors WelcomeView logic).
  const disconnectedDesc = (() => {
    if (!lastConnectionResult || lastConnectionResult.ok) return null;
    const { reason, url } = lastConnectionResult;
    if (reason === 'cors') {
      return isLikelyChromeCorsPna(url)
        ? 'Chrome blocked this connection (Local Network Access). Allow the permission prompt, then retry.'
        : `The server rejected cross-origin requests from this page. Restart it with --cors-origin='${window.location.origin}' to allow this page.`;
    }
    if (reason === 'network')
      return 'Cannot reach the server — check that it is running and the URL is correct.';
    if (reason === 'timeout')
      return 'Connection timed out. The server may be starting up — retry in a moment.';
    return null;
  })();

  const handleRetryConnection = async () => {
    setIsRetryingConnection(true);
    try {
      if (serverId) {
        // Retry the conversation's specific server, not the primary.
        await serverClient.checkConnection();
      } else {
        await connect();
      }
    } catch {
      // connect()/checkConnection() shows toast on failure; swallow to avoid double-toast
    } finally {
      setIsRetryingConnection(false);
    }
  };

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

  if (loadError && messageCount === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="flex max-w-md flex-col items-center gap-3 text-center">
          <AlertTriangle className="h-8 w-8 text-destructive" />
          <div className="font-medium">Failed to load conversation</div>
          <div className="break-words text-sm text-muted-foreground">{loadError}</div>
          <Button variant="outline" size="sm" onClick={() => void retryLoad()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <main
      ref={paneRef}
      data-conversation-pane
      className="relative flex h-full flex-col"
      onFocus={activatePane}
      onPointerDown={activatePane}
    >
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

      {showServerDisconnectedBanner && (
        <div className="flex shrink-0 items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm">
          <WifiOff className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <span className="min-w-0 flex-1 text-amber-800 dark:text-amber-200">
            <span className="font-medium">Server not connected</span>
            {disconnectedDesc
              ? ` — ${disconnectedDesc}`
              : ' — browsing demo data. Connect a server to start a real conversation.'}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 shrink-0 gap-1.5 text-xs text-amber-700 hover:bg-amber-500/20 hover:text-amber-900 dark:text-amber-300 dark:hover:text-amber-100"
            onClick={() => void handleRetryConnection()}
            disabled={isRetryingConnection}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isRetryingConnection ? 'animate-spin' : ''}`} />
            {isRetryingConnection ? 'Retrying…' : 'Retry'}
          </Button>
        </div>
      )}

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
          {() => {
            const log = conversation$.data.log.get();
            let activeModel: string | undefined;
            if (log) {
              for (let i = log.length - 1; i >= 0; i--) {
                const msg = log[i];
                if (msg.role === 'assistant' && msg.metadata?.model) {
                  activeModel = msg.metadata.model;
                  break;
                }
              }
            }
            return (
              <OpenConversationPathButton
                logdir={conversation$.data.logdir.get()}
                baseUrl={connectionConfig.baseUrl}
                activeModel={activeModel}
              />
            );
          }}
        </Memo>

        {hasMoreBefore && (
          <div className="flex justify-center py-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleLoadOlderMessages()}
              disabled={isLoadingOlderMessages}
              className="gap-1 text-xs text-muted-foreground"
            >
              <ChevronUp className={`h-3 w-3 ${isLoadingOlderMessages ? 'animate-pulse' : ''}`} />
              {isLoadingOlderMessages ? 'Loading older messages' : 'Load older messages'}
            </Button>
          </div>
        )}

        <For each={conversation$?.data.log ?? []}>
          {(msg$) => {
            // index is the LOCAL array position in the current log window.
            const index = getObservableIndex(msg$);
            // absoluteIndex is the position in the full conversation (server-space).
            // All server-bound operations and index-keyed maps use absoluteIndex.
            const logOffset = conversation$?.logOffset?.get() ?? 0;
            const absoluteIndex = logOffset + index;

            // Hide all system messages before the first non-system message by default
            const firstNonSystemIndex = firstNonSystemIndex$.get();
            const isInitialSystem =
              msg$.role.get() === 'system' &&
              (firstNonSystemIndex === -1 || index < firstNonSystemIndex);
            if (isInitialSystem && !showInitialSystem$.get()) {
              return <div key={`${absoluteIndex}-${msg$.timestamp.get()}`} />;
            }

            // Hide messages with hide=true (e.g., auto-included lessons)
            if (msg$.hide?.get() && !showHiddenMessages$.get()) {
              return <div key={`${absoluteIndex}-${msg$.timestamp.get()}`} />;
            }

            // Get the previous and next *visible* messages for chain context
            // (skip hidden messages so they don't break chain grouping)
            // prevIdx/nextIdx are LOCAL for array traversal.
            let prevIdx = index - 1;
            while (prevIdx >= 0 && isMessageHidden(prevIdx)) prevIdx--;
            const previousMessage$ = prevIdx >= 0 ? conversation$.data.log[prevIdx] : undefined;

            let nextIdx = index + 1;
            while (conversation$.data.log[nextIdx]?.get() && isMessageHidden(nextIdx)) nextIdx++;
            const nextMessage$ = conversation$.data.log[nextIdx]?.get()
              ? conversation$.data.log[nextIdx]
              : undefined;

            // Step grouping: stepRoles$ is keyed by ABSOLUTE index.
            const stepRole = stepRoles$.get().get(absoluteIndex);

            // If this is a grouped message and the group is collapsed, hide it
            if (stepRole?.type === 'grouped' && !expandedGroups$.get().has(stepRole.groupId)) {
              return <div key={`${absoluteIndex}-${msg$.timestamp.get()}`} />;
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
              return <div key={`${absoluteIndex}-${msg$.timestamp.get()}`}>{groupSummary}</div>;
            }

            // Construct agent avatar URL if agent has avatar configured
            // NOTE: must use .get() to read actual values from Legend State observables
            const baseUrl = connectionConfig.baseUrl.replace(/\/+$/, '');
            const agentAvatarUrl = conversation$.data.agent?.avatar?.get()
              ? `${baseUrl}/api/v2/conversations/${conversationId}/agent/avatar`
              : undefined;
            const agentName = conversation$.data.agent?.name?.get();

            return (
              <div
                key={`${absoluteIndex}-${msg$.timestamp.get()}`}
                data-message-index={absoluteIndex}
              >
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
                  onFork={isReadOnly ? undefined : handleForkMessage}
                  messageIndex={absoluteIndex}
                  hideAvatar={
                    stepRole?.type === 'group-start' && expandedGroups$.get().has(stepRole.groupId)
                  }
                />
                {/* Branch indicator at fork points */}
                <Memo>
                  {() => {
                    // forkPoints$ is computed from branches and keyed by absolute index.
                    const forkInfo = forkPoints$.get().get(absoluteIndex);
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

        {/* Inline Tool Confirmation — hidden when no-confirm mode is active */}
        {!settings.noConfirmMode && (
          <InlineToolConfirmation
            pendingTool$={conversation$?.pendingTool}
            onConfirm={handleConfirmTool}
            onEdit={handleEditTool}
            onSkip={handleSkipTool}
            onAuto={handleAutoConfirmTool}
          />
        )}

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
