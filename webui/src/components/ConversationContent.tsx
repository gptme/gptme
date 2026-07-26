import type { FC } from 'react';
import { useRef, useEffect, useCallback, useMemo, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
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
import { Memo, use$, useObservable, useObserveEffect } from '@legendapp/state/react';
import { useApi } from '@/contexts/ApiContext';
import { useSettings } from '@/contexts/SettingsContext';
import { useModels } from '@/hooks/useModels';
import { chatRoute } from '@/utils/routes';
import { AlertTriangle, ArrowDown, ChevronUp, RefreshCw, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { isDemoMode } from '@/utils/connectionConfig';
import { isLikelyChromeCorsPna } from '@/utils/api';
import { getClientForServer } from '@/stores/serverClients';

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
  // When a conversation's server is removed from the registry, getClient(serverId) silently
  // falls back to the primary client. Detect this so the banner can report "server not found"
  // instead of showing the wrong server's connection status.
  const serverNotFound = !!serverId && !getClientForServer(serverId);
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
  const stepRoles$ = useObservable<Record<number, StepRole>>({});
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

  // Structural key: encodes message count, logOffset, and wholesale-log revision.
  // The selector re-runs on every log mutation (including streaming tokens), but its
  // VALUE (e.g. "12:0:3") changes only when messages are added/removed, the window
  // shifts, or the log is replaced after an edit/branch switch/reload. Legend State
  // compares by value, so downstream observers skip per-token content updates.
  const logStructureKey$ = useObservable(() => {
    const count = conversation$?.data.log.get()?.length ?? 0;
    const offset = conversation$?.logOffset?.get() ?? 0;
    const revision = conversation$?.logRevision?.get() ?? 0;
    return `${count}:${offset}:${revision}`;
  });

  // Recompute step roles when the message structure or visibility settings change.
  // Subscribes to logStructureKey$ (not the full log) so it does NOT re-run on
  // every streaming token — only when message count, logOffset, or settings change.
  useObserveEffect(() => {
    const structureKey = logStructureKey$.get();
    const firstNonSystem = firstNonSystemIndex$.get();
    const showInitial = showInitialSystem$.get();
    const showHidden = showHiddenMessages$.get();

    const [messageCountText, logOffsetText] = structureKey.split(':');
    const messageCount = parseInt(messageCountText, 10);
    const logOffset = parseInt(logOffsetText, 10);

    if (!messageCount) {
      stepRoles$.set({});
      return;
    }

    // Peek (non-reactive read) — structural changes are already tracked via logStructureKey$.
    const messages = conversation$?.data.log.peek() as Message[] | undefined;
    if (!messages?.length) {
      stepRoles$.set({});
      return;
    }

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
    // Convert to a plain Record so Legend State can do fine-grained key diffing;
    // rows subscribe to stepRoles$[absoluteIndex] and only re-render on their key changing.
    const roles = buildStepRoles(messages as Message[], isHidden, logOffset);
    stepRoles$.set(Object.fromEntries(roles) as Record<number, StepRole>);
  });

  // Create a ref for the scroll container
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Reactive reads for virtualizer rendering — using use$() so changes trigger
  // a React re-render and the virtualizer map gets fresh values.
  const expandedGroups = use$(expandedGroups$);
  const logOffsetValue = use$(() => conversation$?.logOffset?.get() ?? 0);
  const stepRolesValue = use$(stepRoles$);

  // Virtualize only rows that can render content. Hidden messages otherwise
  // reserve their estimate until ResizeObserver measures a zero-height wrapper,
  // creating blank regions and shifting scroll positions.
  const visibleMessageIndices = useMemo(() => {
    const indices: number[] = [];
    const firstNonSystemIndex = firstNonSystemIndex$.peek();

    for (let index = 0; index < messageCount; index++) {
      const msg = conversation$?.data.log[index]?.peek();
      if (!msg) continue;

      const isInitialSystem =
        msg.role === 'system' && (firstNonSystemIndex === -1 || index < firstNonSystemIndex);
      if (isInitialSystem && !settings.showInitialSystem) continue;
      if (msg.hide && !settings.showHiddenMessages) continue;

      const stepRole = stepRolesValue[logOffsetValue + index];
      if (stepRole?.type === 'grouped' && !expandedGroups.has(stepRole.groupId)) continue;
      indices.push(index);
    }
    return indices;
  }, [
    conversation$,
    expandedGroups,
    firstNonSystemIndex$,
    logOffsetValue,
    messageCount,
    settings.showHiddenMessages,
    settings.showInitialSystem,
    stepRolesValue,
  ]);

  // Virtualizer: renders only visible messages, dramatically reducing DOM nodes
  // for long conversations. estimateSize is a rough average; measureElement
  // (ResizeObserver-based) corrects actual heights after first render.
  const virtualizer = useVirtualizer({
    count: visibleMessageIndices.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 150,
    overscan: 5,
    getItemKey: (virtualIndex) => {
      const index = visibleMessageIndices[virtualIndex];
      const off = conversation$?.logOffset?.peek() ?? 0;
      const msg = conversation$?.data.log[index]?.peek();
      return msg ? `${off + index}-${msg.timestamp}` : `${off + index}`;
    },
  });

  // Mutable refs so effects/callbacks always hold the latest virtualizer and
  // visible index mapping without stale-closure issues.
  const virtualizerRef = useRef(virtualizer);
  const visibleMessageIndicesRef = useRef(visibleMessageIndices);
  virtualizerRef.current = virtualizer;
  visibleMessageIndicesRef.current = visibleMessageIndices;

  // Pending rAF handle for scroll-to-bottom — cancelled before re-scheduling so
  // a burst of log updates queues at most one scroll per animation frame.
  const scrollRAFRef = useRef<number | null>(null);

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
    const count = visibleMessageIndicesRef.current.length;
    if (count <= 0) return;
    isAutoScrolling$.set(true);
    // scrollToIndex ensures the last item is actually rendered before measuring,
    // which is more reliable than container.scrollHeight with virtual lists.
    virtualizerRef.current.scrollToIndex(count - 1, { align: 'end' });
    requestAnimationFrame(() => {
      isAutoScrolling$.set(false);
    });
  }, [isAutoScrolling$]);

  // Auto-scroll when the conversation is updated (e.g., streaming response).
  // Cancel any pending rAF before scheduling a new one so rapid log updates
  // (one per streaming token) collapse to at most one scroll per animation frame
  // instead of queuing hundreds of layout-forcing scrollHeight reads.
  useObserveEffect(conversation$?.data.log, () => {
    if (!autoScrollAborted$.get()) {
      if (scrollRAFRef.current !== null) {
        cancelAnimationFrame(scrollRAFRef.current);
      }
      scrollRAFRef.current = requestAnimationFrame(() => {
        scrollRAFRef.current = null;
        scrollToBottom();
      });
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

  const searchHighlightRAFRef = useRef<number | null>(null);

  const clearSearchHighlights = useCallback(() => {
    if (searchHighlightRAFRef.current !== null) {
      cancelAnimationFrame(searchHighlightRAFRef.current);
      searchHighlightRAFRef.current = null;
    }
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
      const stepRole = stepRoles$[logOffset + idx].get();
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

  // Search helpers: imperative DOM highlight + scroll, avoids re-rendering all messages.
  // With virtualization the target element may not be in the DOM yet, so we first
  // scroll the virtualizer to bring it into the rendered range, then query + highlight.
  const highlightSearchMatch = useCallback(
    (msgIndex: number) => {
      clearSearchHighlights();
      const logOff = conversation$?.logOffset?.get() ?? 0;
      const localIndex = msgIndex - logOff;
      const virtualIndex = visibleMessageIndicesRef.current.indexOf(localIndex);
      if (virtualIndex === -1) return;

      virtualizerRef.current.scrollToIndex(virtualIndex, { align: 'center' });

      // Variable-height rendering can take more than a fixed number of frames.
      // Retry for a short bounded window until the target row mounts.
      let attemptsRemaining = 10;
      const applyHighlight = () => {
        const el = scrollContainerRef.current?.querySelector<HTMLElement>(
          `[data-message-index="${msgIndex}"]`
        );
        if (el) {
          el.style.outline = '2px solid rgba(234,179,8,0.6)';
          el.style.outlineOffset = '-2px';
          searchHighlightRAFRef.current = null;
          return;
        }
        attemptsRemaining--;
        searchHighlightRAFRef.current =
          attemptsRemaining > 0 ? requestAnimationFrame(applyHighlight) : null;
      };
      searchHighlightRAFRef.current = requestAnimationFrame(applyHighlight);
    },
    [clearSearchHighlights, conversation$]
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
    const count = visibleMessageIndicesRef.current.length;
    if (count > 0) {
      isAutoScrolling$.set(true);
      virtualizerRef.current.scrollToIndex(count - 1, { align: 'end', behavior: 'smooth' });
      const container = scrollContainerRef.current;
      if (container) {
        container.addEventListener('scrollend', () => isAutoScrolling$.set(false), {
          once: true,
        });
      }
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
  const showServerDisconnectedBanner = (serverNotFound || !isConnected) && !isDemoMode();

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
            {serverNotFound
              ? ' — the server for this conversation is no longer registered. Add it again in settings to reconnect.'
              : disconnectedDesc
                ? ` — ${disconnectedDesc}`
                : ' — browsing demo data. Connect a server to start a real conversation.'}
          </span>
          {!serverNotFound && (
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
          )}
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

        {/* Virtual message list — only visible rows are in the DOM.
            The outer div establishes the total scroll height; each item is
            absolutely positioned via transform so the container never reflows. */}
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const index = visibleMessageIndices[virtualItem.index];
            // Guard against count/log getting briefly out of sync.
            const msg$ = conversation$?.data.log[index];
            if (!msg$) return null;

            // absoluteIndex is the position in the full conversation (server-space).
            // All server-bound operations and index-keyed maps use absoluteIndex.
            const absoluteIndex = logOffsetValue + index;

            // Wrapper shared by every case: TanStack Virtual needs data-index on
            // the element passed to measureElement so it can track heights.
            const wrapperStyle: React.CSSProperties = {
              position: 'absolute',
              top: 0,
              transform: `translateY(${virtualItem.start}px)`,
              width: '100%',
            };

            // Get the previous and next *visible* messages for chain context
            // (skip hidden messages so they don't break chain grouping).
            let prevIdx = index - 1;
            while (prevIdx >= 0 && isMessageHidden(prevIdx)) prevIdx--;
            const previousMessage$ = prevIdx >= 0 ? conversation$.data.log[prevIdx] : undefined;

            let nextIdx = index + 1;
            while (conversation$.data.log[nextIdx]?.peek() && isMessageHidden(nextIdx)) nextIdx++;
            const nextMessage$ = conversation$.data.log[nextIdx]?.peek()
              ? conversation$.data.log[nextIdx]
              : undefined;

            // stepRolesValue is read via use$() at the component level, so any
            // structural change that updates stepRoles$ triggers a re-render here.
            const stepRole = stepRolesValue[absoluteIndex];

            // If this is a group-start, render the summary bar
            // (when collapsed, replaces the message; when expanded, shown above it)
            const groupSummary =
              stepRole?.type === 'group-start' ? (
                <CollapsedStepGroup
                  count={stepRole.count}
                  tools={stepRole.tools}
                  steps={stepRole.steps}
                  isExpanded={expandedGroups.has(stepRole.groupId)}
                  onToggle={() => toggleGroup(stepRole.groupId)}
                />
              ) : null;

            if (stepRole?.type === 'group-start' && !expandedGroups.has(stepRole.groupId)) {
              return (
                <div
                  key={virtualItem.key}
                  data-index={virtualItem.index}
                  ref={virtualizer.measureElement}
                  style={wrapperStyle}
                >
                  {groupSummary}
                </div>
              );
            }

            const baseUrl = connectionConfig.baseUrl.replace(/\/+$/, '');
            const agentAvatarUrl = conversation$.data.agent?.avatar?.peek()
              ? `${baseUrl}/api/v2/conversations/${conversationId}/agent/avatar`
              : undefined;
            const agentName = conversation$.data.agent?.name?.peek();

            return (
              <div
                key={virtualItem.key}
                data-index={virtualItem.index}
                ref={virtualizer.measureElement}
                style={wrapperStyle}
              >
                {/* data-message-index is used by search highlight and branch indicator */}
                <div data-message-index={absoluteIndex}>
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
                      stepRole?.type === 'group-start' && expandedGroups.has(stepRole.groupId)
                    }
                  />
                  {/* Branch indicator at fork points */}
                  <Memo>
                    {() => {
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
              </div>
            );
          })}
        </div>

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
