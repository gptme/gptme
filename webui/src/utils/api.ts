import { type CreateAgentResponse, type CreateAgentRequest } from '@/components/CreateAgentDialog';
import type {
  ApiError,
  ChatConfig,
  ConversationResponse,
  CreateConversationRequest,
  ExternalSessionCatalogItem,
  ExternalSessionDetail,
  SendMessageRequest,
  UserInfo,
} from '@/types/api';
import type { ConversationSummary, Message, ToolUse } from '@/types/conversation';
import { getApiBaseUrl } from '@/utils/connectionConfig';
import { type Observable } from '@legendapp/state';
import { observable } from '@legendapp/state';
import { initConversation } from '@/stores/conversations';

// Add DOM types
type RequestInit = globalThis.RequestInit;
type Response = globalThis.Response;
type HeadersInit = globalThis.HeadersInit;

// Error type for API client
export class ApiClientError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
    this.name = 'ApiClientError';
  }

  static isApiError(error: unknown): error is ApiClientError {
    return error instanceof ApiClientError;
  }
}

// Type guard for API error responses
function isApiErrorResponse(response: unknown): response is ApiError {
  return typeof response === 'object' && response !== null && 'error' in response;
}

// Result of a connection probe — captures enough context for a useful user-facing message.
export type ConnectionProbeResult =
  | { ok: true; url: string }
  | {
      ok: false;
      url: string;
      reason: 'network' | 'http_error' | 'parse_error' | 'timeout' | 'cors';
      status?: number;
      message: string;
    };

export interface ToolPendingEvent {
  type: 'tool_pending';
  tool_id: string;
  tooluse: ToolUse;
  auto_confirm: boolean;
}

export interface ToolConfirmationRequest {
  session_id: string;
  tool_id: string;
  action: 'confirm' | 'edit' | 'skip' | 'auto';
  content?: string;
  count?: number;
}

export class ApiClient {
  public baseUrl: string;
  public authHeader: string | null = null;
  public readonly isConnected$: Observable<boolean> = observable(false);
  public readonly lastConnectionResult$: Observable<ConnectionProbeResult | null> =
    observable<ConnectionProbeResult | null>(null);
  private identifier: string;
  private controller: AbortController | null = null;
  public sessions$: Observable<Map<string, string>> = observable(new Map()); // Map conversation IDs to session IDs
  public userInfo$: Observable<UserInfo | null> = observable<UserInfo | null>(null);
  private eventSources: Map<string, EventSource> = new Map(); // Map conversation IDs to EventSource instances
  private isCleaningUp = false;
  private authCookieSet = false;
  private authCookieSetAt: number | null = null;
  private authCookiePromise: Promise<void> | null = null;

  constructor(baseUrl: string = getApiBaseUrl(), authHeader: string | null = null) {
    this.baseUrl = baseUrl;
    this.authHeader = authHeader;
    this.identifier = crypto.randomUUID();
    console.log(`[ApiClient] Identifier: ${this.identifier}`);

    // Set auth cookie eagerly (for SSE connections that need it later).
    // Skip for cross-origin servers: SameSite=Lax cookies aren't sent on cross-origin
    // EventSource requests, so cookie auth would silently fail with a 401 and the
    // query-param fallback would be suppressed (authCookieSet=true). Use query params instead.
    if (this.authHeader && !this.isBaseUrlCrossOrigin()) {
      this.authCookiePromise = this.ensureAuthCookie();
    }
  }

  /**
   * Returns true if baseUrl is on a different origin than the current page.
   * Cross-origin EventSource requests do not send SameSite=Lax cookies, so
   * we fall back to query-param auth in that case.
   */
  private isBaseUrlCrossOrigin(): boolean {
    try {
      return new URL(this.baseUrl).origin !== window.location.origin;
    } catch {
      return false;
    }
  }

  /**
   * Reset cookie state and re-initiate cookie setup if cookie is near expiry.
   * Called on reconnect to handle expired cookies (24h TTL).
   * Skips refresh when cookie was set recently to avoid hammering the endpoint
   * on flaky connections that reconnect frequently.
   */
  private resetAuthCookie(): void {
    const now = Date.now();
    const ttlMs = 86400 * 1000; // keep in sync with AUTH_COOKIE_MAX_AGE on server
    const isExpired = this.authCookieSetAt === null || now - this.authCookieSetAt > ttlMs * 0.9;
    if (isExpired) {
      this.authCookieSet = false;
      if (this.authHeader && !this.isBaseUrlCrossOrigin()) {
        this.authCookiePromise = this.ensureAuthCookie();
      }
    }
  }

  /**
   * Set an HttpOnly auth cookie via the server's cookie endpoint.
   * This allows SSE/EventSource connections to authenticate via cookies
   * instead of exposing tokens in query parameters.
   */
  private async ensureAuthCookie(): Promise<void> {
    if (this.authCookieSet || !this.authHeader) return;

    try {
      const response = await fetch(`${this.baseUrl}/api/v2/auth/cookie`, {
        method: 'POST',
        headers: { Authorization: this.authHeader },
        credentials: 'include',
      });
      if (response.ok) {
        this.authCookieSet = true;
        this.authCookieSetAt = Date.now();
        console.log('[ApiClient] Auth cookie set for SSE connections');
      } else {
        console.warn('[ApiClient] Failed to set auth cookie:', response.status);
      }
    } catch (error) {
      // Non-fatal: fall back to query param auth for SSE
      console.warn('[ApiClient] Could not set auth cookie, will use query param fallback:', error);
    }
  }

  private async fetchWithTimeout(
    url: string,
    options: RequestInit = {},
    timeoutMs: number = 5000,
    maxRetries: number = 5,
    initialBackoffMs: number = 500
  ): Promise<Response> {
    let retryCount = 0;
    let lastError: Error | null = null;

    while (retryCount <= maxRetries) {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => {
        console.log('[ApiClient] Fetch timeout, aborting request');
        controller.abort('timeout');
      }, timeoutMs);

      let headers = {
        ...options.headers,
      };

      if (this.authHeader) {
        headers = {
          ...headers,
          Authorization: this.authHeader,
        };
      }

      try {
        const response = await fetch(url, {
          ...options,
          headers,
          signal: controller.signal,
        });
        console.log('[ApiClient] Fetch completed, clearing timeout');
        clearTimeout(timeoutId);
        return response;
      } catch (error: unknown) {
        console.log('[ApiClient] Fetch error, clearing timeout');
        clearTimeout(timeoutId);

        // Save the error for potential re-throwing
        lastError = error instanceof Error ? error : new Error(String(error));

        // Check if error can be retried - using type narrowing for safety
        const isAbortError = error instanceof DOMException && error.name === 'AbortError';
        const isTimeout = isAbortError && error.message === 'timeout';
        const isTypeError = error instanceof TypeError;

        // Don't retry if the request was deliberately aborted or for certain error types
        if (
          (isAbortError && !isTimeout) ||
          isTypeError || // for CORS, etc.
          retryCount >= maxRetries
        ) {
          break;
        }

        // Calculate backoff with exponential increase
        const backoffTime = initialBackoffMs * Math.pow(2, retryCount);

        console.log(
          `[ApiClient] Retrying fetch (${retryCount + 1}/${maxRetries + 1}) after ${backoffTime.toFixed(0)}ms`
        );
        retryCount++;

        // Wait for the backoff period
        await new Promise((resolve) => setTimeout(resolve, backoffTime));
      }
    }

    // If we've reached this point, all retries have failed
    throw lastError || new Error('Request failed after multiple retries');
  }

  private async fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
    const headers: HeadersInit = {
      ...options.headers,
      'Content-Type': 'application/json',
    };

    if (this.authHeader) {
      (headers as Record<string, string>)['Authorization'] = this.authHeader;
    }

    const response = await fetch(url, {
      ...options,
      headers,
    });

    const data = await response.json();

    if (!response.ok) {
      // Try to extract error message from response body
      if (isApiErrorResponse(data)) {
        throw new ApiClientError(data.error, response.status);
      } else {
        throw new ApiClientError(`HTTP error! status: ${response.status}`, response.status);
      }
    }

    // Check if the response is an error (for successful HTTP status but API error)
    if (isApiErrorResponse(data)) {
      throw new ApiClientError(data.error, data.status);
    }

    return data as T;
  }

  public async cancelPendingRequests() {
    if (this.isCleaningUp) return;

    try {
      this.isCleaningUp = true;
      if (this.controller) {
        this.controller.abort('cancelPendingRequests called');
        // Wait a bit for abort event handlers to complete
        await new Promise<void>((resolve) => {
          setTimeout(resolve, 50);
        });
        this.controller = null;
      }

      // Close all event sources
      for (const [conversationId, eventSource] of this.eventSources.entries()) {
        eventSource.close();
        this.eventSources.delete(conversationId);
      }
    } finally {
      this.isCleaningUp = false;
    }
  }

  private get isConnected(): boolean {
    return this.isConnected$.get();
  }

  async checkConnection(): Promise<boolean> {
    const url = `${this.baseUrl}/api/v2`;
    console.log('[ApiClient] Checking connection to', this.baseUrl);
    try {
      const response = await this.fetchWithTimeout(url, {}, 3000);
      if (!response.ok) {
        console.error('API endpoint returned non-OK status:', response.status);
        this.isConnected$.set(false);
        this.lastConnectionResult$.set({
          ok: false,
          url,
          reason: 'http_error',
          status: response.status,
          message:
            `Server responded with HTTP ${response.status} ${response.statusText || ''}`.trim(),
        });
        return false;
      }

      // Try to parse the response to ensure it's valid JSON
      try {
        await response.json();
      } catch (parseError) {
        console.error(`[ApiClient] Failed to parse API response from ${url}:`, parseError);
        this.isConnected$.set(false);
        this.lastConnectionResult$.set({
          ok: false,
          url,
          reason: 'parse_error',
          message:
            parseError instanceof Error
              ? `Server response was not valid JSON: ${parseError.message}`
              : 'Server response was not valid JSON',
        });
        return false;
      }

      this.isConnected$.set(true);
      this.lastConnectionResult$.set({ ok: true, url });
      return true;
    } catch (error) {
      const isAbort =
        (error instanceof DOMException && error.name === 'AbortError') ||
        (error instanceof Error && error.name === 'AbortError');
      const isNetwork =
        error instanceof TypeError &&
        (error.message.includes('Failed to fetch') ||
          error.message.includes('NetworkError') ||
          error.message.includes('CORS'));
      let reason: 'network' | 'cors' | 'timeout';
      let message: string;
      if (isAbort) {
        reason = 'timeout';
        message = 'Request timed out after 3s — server may be slow or unreachable';
      } else if (
        error instanceof TypeError &&
        (error.message.includes('CORS') || error.message.includes('NetworkError'))
      ) {
        reason = 'cors';
        message =
          'Network or CORS error — server may not allow requests from this origin: ' +
          (typeof window !== 'undefined' ? window.location.origin : 'unknown');
      } else if (isNetwork) {
        reason = 'network';
        message = 'Could not reach server (connection refused or no DNS)';
      } else {
        reason = 'network';
        message = error instanceof Error ? error.message : String(error);
      }

      if (isNetwork || isAbort) {
        console.error('[ApiClient] Connection check failed:', message);
      } else {
        console.error('[ApiClient] Connection check failed:', error);
      }
      this.isConnected$.set(false);
      this.lastConnectionResult$.set({ ok: false, url, reason, message });
      return false;
    }
  }

  // Add method to explicitly set connection state
  setConnected(connected: boolean) {
    if (connected && !this.isConnected) {
      console.warn('Manually setting connected state without verification');
    }
    this.isConnected$.set(connected);
  }

  async subscribeToEvents(
    conversationId: string,
    callbacks: {
      onMessageStart: () => void;
      onToken: (token: string) => void;
      onMessageComplete: (message: Message) => void;
      onMessageAdded: (message: Message) => void;
      onToolPending: (toolId: string, tooluse: ToolUse, auto_confirm: boolean) => void;
      onToolExecuting: (toolId: string) => void;
      onInterrupted: () => void;
      onError: (error: string) => void;
      onConfigChanged?: (config: ChatConfig, changedFields: string[]) => void;
      onConnected?: () => void;
      onReconnectState?: (state: {
        generating: boolean;
        pendingTools: Array<{
          tool_id: string;
          tooluse: ToolUse;
          auto_confirm: boolean;
        }>;
      }) => void;
      onConversationEdited?: (data: {
        index: number;
        truncated: boolean;
        log: Message[];
        branches: Record<string, Message[]>;
      }) => void;
    }
  ): Promise<void> {
    // Close any existing event stream for this conversation
    this.closeEventStream(conversationId);

    // Function to reconnect to the event stream if it fails, or we fail to get a session ID
    const reconnect = () => {
      console.log(`[ApiClient] Attempting reconnection for ${conversationId}`);
      this.closeEventStream(conversationId);
      // Reset cookie state so expired cookies (24h TTL) are re-fetched on reconnect
      this.resetAuthCookie();
      this.subscribeToEvents(conversationId, callbacks).catch((err) => {
        console.error('[ApiClient] Reconnect failed:', err);
        callbacks.onError?.(String(err));
      });
    };

    // Create a timeout that will reconnect if we don't get a session ID after 5 seconds
    const sessionIdTimeout = setTimeout(() => {
      if (!this.sessions$.has(conversationId)) {
        console.log(
          `[ApiClient] Timed out waiting for session ID for ${conversationId}, reconnecting`
        );
        reconnect();
      }
    }, 5000);

    /**
     * For SSE connections, we prefer cookie-based authentication (set via
     * ensureAuthCookie). If the cookie is set, EventSource sends it
     * automatically with withCredentials: true.
     *
     * Falls back to query parameter auth if the cookie endpoint failed.
     */
    const url = new URL(`${this.baseUrl}/api/v2/conversations/${conversationId}/events`);

    // Pass existing session_id if available to reuse the session
    const existingSessionId = this.sessions$.get(conversationId).get();
    if (existingSessionId) {
      url.searchParams.set('session_id', existingSessionId);
      console.log(`[ApiClient] Reusing existing session ID for SSE: ${existingSessionId}`);
    }

    // Wait for the cookie setup to complete before deciding auth method.
    // This eliminates the race condition where SSE connects before cookie is set.
    if (this.authCookiePromise) {
      await this.authCookiePromise;
    }

    if (this.authHeader && !this.authCookieSet) {
      // Fallback: pass token as query param if cookie endpoint was unavailable
      const token = this.authHeader.split(' ')[1];
      if (!token) {
        console.error('[ApiClient] Invalid auth header format, expected "Bearer <token>"');
        throw new ApiClientError('Invalid auth header format');
      }
      url.searchParams.set('token', token);
      console.warn('[ApiClient] Using query param auth for SSE (cookie not available)');
    } else if (this.authHeader) {
      console.log(`[ApiClient] Connecting to event stream: ${url.toString()} (with auth cookie)`);
    } else {
      console.log(`[ApiClient] Connecting to event stream without auth: ${url.toString()}`);
    }

    const eventSource = new EventSource(url.toString(), { withCredentials: true });

    // Add connect event notification
    let isConnected = false;

    // Track reconnection attempts
    let reconnectCount = 0;
    const maxReconnects = 5;
    let reconnectTimer: number | null = null;

    // Handle connection open
    eventSource.onopen = () => {
      console.log(`[ApiClient] Event stream connected for ${conversationId}`);
      isConnected = true;
      reconnectCount = 0; // Reset reconnect count on successful connection
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Skip logging pings to avoid console spam
        //if (data.type !== 'ping') {
        //  console.log(`[ApiClient] Event received for ${conversationId}:`, {
        //    ...data,
        //    eventSourceUrl: eventSource.url,
        //  });
        //}

        switch (data.type) {
          case 'message_added':
            console.log(`[ApiClient] Message added:`, data.message);
            callbacks.onMessageAdded(data.message);
            break;

          case 'generation_started':
            console.log(`[ApiClient] Generation started`, data);
            callbacks.onMessageStart();
            break;

          case 'generation_progress':
            //console.log(`[ApiClient] Generation progress:`, data.token);
            callbacks.onToken(data.token);
            break;

          case 'generation_complete':
            console.log(`[ApiClient] Generation complete:`, data.message);
            callbacks.onMessageComplete(data.message);
            break;

          case 'tool_pending': {
            console.log(`[ApiClient] Tool pending:`, data);
            const pendingToolEvent = data as ToolPendingEvent;
            callbacks.onToolPending(
              pendingToolEvent.tool_id,
              pendingToolEvent.tooluse,
              pendingToolEvent.auto_confirm
            );
            break;
          }

          case 'tool_executing': {
            console.log(`[ApiClient] Tool executing:`, data);
            const toolExecutingEvent = data as { tool_id: string };
            callbacks.onToolExecuting(toolExecutingEvent.tool_id);
            break;
          }

          case 'error':
            console.error(`[ApiClient] Error event:`, data);
            callbacks.onError(data.error);
            break;

          case 'ping':
            console.log(`[ApiClient] ping`);
            break;

          case 'connected':
            console.log(`[ApiClient] Session connected event:`, data);
            // Resolve the promise with the session ID
            this.sessions$.set(conversationId, data.session_id);
            // Clear the session ID timeout
            clearTimeout(sessionIdTimeout);
            // Notify that connection is established
            callbacks.onConnected?.();
            // Restore state on reconnect (pending tools, generating flag)
            if (callbacks.onReconnectState && (data.pending_tools?.length || data.generating)) {
              callbacks.onReconnectState({
                generating: data.generating ?? false,
                pendingTools: data.pending_tools ?? [],
              });
            }
            break;

          case 'conversation_edited':
            console.log(`[ApiClient] Conversation edited:`, data);
            callbacks.onConversationEdited?.(data);
            break;

          case 'interrupted':
            callbacks.onInterrupted();
            break;

          case 'config_changed':
            console.log(`[ApiClient] Config changed:`, data);
            if (callbacks.onConfigChanged) {
              callbacks.onConfigChanged(data.config, data.changed_fields || []);
            }
            break;

          default:
            console.warn(`[ApiClient] Unknown event type:`, data);
            break;
        }
      } catch (e) {
        console.error('Error parsing event data:', e);
      }
    };

    eventSource.onerror = (error) => {
      console.error(`[ApiClient] Event stream error for ${conversationId}:`, error);

      // Clear the session ID timeout
      clearTimeout(sessionIdTimeout);

      // If we were previously connected, try to reconnect
      if (isConnected) {
        console.log(`[ApiClient] Connection was established before, attempting to reconnect...`);
        isConnected = false;

        // Only auto-reconnect if we haven't exceeded the max reconnects
        if (reconnectCount < maxReconnects) {
          reconnectCount++;

          // Exponential backoff for reconnection (1s, 2s, 4s, 8s, 16s)
          const delay = Math.pow(2, reconnectCount - 1) * 1000;

          console.log(
            `[ApiClient] Reconnecting in ${delay}ms (attempt ${reconnectCount}/${maxReconnects})`
          );

          // Clear any existing timer
          if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
          }

          // Set a new timer for reconnection
          reconnectTimer = window.setTimeout(() => {
            reconnect();
          }, delay);
        } else {
          console.warn(`[ApiClient] Max reconnects (${maxReconnects}) reached, giving up`);
          callbacks.onError?.('Connection lost and max reconnects reached');
        }
      } else {
        // We never established a connection, report the error
        callbacks.onError?.('Failed to connect to event stream');
      }
    };

    // Store the event source
    this.eventSources.set(conversationId, eventSource);
  }

  closeEventStream(conversationId: string): void {
    if (this.eventSources.has(conversationId)) {
      this.eventSources.get(conversationId)!.close();
      this.eventSources.delete(conversationId);
    }
  }

  async getUserInfo(): Promise<UserInfo> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    // Return cached if available
    const cached = this.userInfo$.get();
    if (cached) return cached;
    const info = await this.fetchJson<UserInfo>(`${this.baseUrl}/api/v2/user`);
    this.userInfo$.set(info);
    return info;
  }

  async getServerInfo(): Promise<{ version?: string }> {
    const data = await this.fetchJson<{ version?: string }>(`${this.baseUrl}/api/v2`);
    return data;
  }

  async getConversations(limit: number = 100): Promise<ConversationSummary[]> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    try {
      return await this.fetchJson<ConversationSummary[]>(
        `${this.baseUrl}/api/v2/conversations?limit=${limit}`
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      throw error;
    }
  }

  async searchConversations(query: string, limit: number = 20): Promise<ConversationSummary[]> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    try {
      return await this.fetchJson<ConversationSummary[]>(
        `${this.baseUrl}/api/v2/conversations?search=${encodeURIComponent(query)}&limit=${limit}`
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      throw error;
    }
  }

  async getConversationsPaginated(
    pageParam: number = 0,
    pageSize: number = 20
  ): Promise<{
    conversations: ConversationSummary[];
    nextCursor: number | undefined;
  }> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    try {
      // Fetch one more than needed to detect if there are more conversations
      const fetchLimit = pageParam + pageSize + 1;
      const allConversations = await this.fetchJson<ConversationSummary[]>(
        `${this.baseUrl}/api/v2/conversations?limit=${fetchLimit}`
      );

      // Slice to get only the requested page
      const conversations = allConversations.slice(pageParam, pageParam + pageSize);

      // Check if there are more conversations by seeing if we got the extra one
      const hasMore = allConversations.length > pageParam + pageSize;
      const nextCursor = hasMore ? pageParam + pageSize : undefined;

      console.log(
        `[API] Pagination: pageParam=${pageParam}, pageSize=${pageSize}, fetched=${allConversations.length}, returning=${conversations.length}, hasMore=${hasMore}`
      );

      return { conversations, nextCursor };
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      throw error;
    }
  }

  async getConversation(logfile: string): Promise<ConversationResponse> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    try {
      const response = await this.fetchJson<ConversationResponse>(
        `${this.baseUrl}/api/v2/conversations/${logfile}`,
        {
          signal: this.controller?.signal,
        }
      );
      return response;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      console.log('[ApiClient] getConversation error:', error);
      throw error;
    }
  }

  async createConversation(
    logfile: string,
    messages: Message[],
    config?: { chat?: { model?: string; stream?: boolean; workspace?: string } }
  ): Promise<{ status: string; session_id: string }> {
    if (!this.isConnected) {
      console.error('Attempted to create conversation while disconnected');
      throw new ApiClientError('Not connected to API');
    }
    try {
      const request: CreateConversationRequest = {
        messages,
        ...(config && { config }),
      };
      const response = await this.fetchJson<{
        status: string;
        session_id: string;
        conversation_id: string;
      }>(`${this.baseUrl}/api/v2/conversations/${logfile}`, {
        method: 'PUT',
        body: JSON.stringify(request),
      });

      // Store the session ID only if not already set by SSE
      const existingSessionId = this.sessions$.get(logfile).get();
      if (!existingSessionId) {
        this.sessions$.set(logfile, response.session_id);
        console.log(
          `[ApiClient] Stored session ID from createConversation: ${response.session_id}`
        );
      } else {
        console.log(
          `[ApiClient] Session ID already exists from SSE, not overwriting: ${existingSessionId}`
        );
      }

      return response;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      console.error('Create conversation error:', error);
      throw error;
    }
  }

  /**
   * Create a conversation with immediate placeholder and background sync.
   * This ensures instant navigation while handling server sync transparently.
   */
  async createConversationWithPlaceholder(
    userMessage: string,
    options?: {
      model?: string;
      stream?: boolean;
      workspace?: string;
      pendingFiles?: File[];
    }
  ): Promise<string> {
    // Generate conversation ID immediately
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const conversationId = `chat-${timestamp}`;

    // Create user message (files will be added after upload)
    const message: Message = {
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString(),
    };

    // Create placeholder conversation in store immediately.
    // needsInitialStep is always true: useConversation triggers step() after subscribing,
    // which fixes the race condition where step() was called before the chat page mounted.
    initConversation(
      conversationId,
      {
        id: conversationId,
        name: 'New conversation',
        log: [message],
        logfile: conversationId,
        branches: {},
        workspace: options?.workspace || '.',
      },
      { needsInitialStep: true }
    );

    if (options?.pendingFiles?.length) {
      // When files are attached: create an empty conversation first (no message yet),
      // upload files, then send ONE complete message with file paths.
      // This avoids the duplicate-message bug where createConversation sent msg_no_files
      // and sendMessage sent a second copy of msg_with_files.
      await this.createConversation(conversationId, [], {
        chat: {
          model: options?.model,
          stream: options?.stream,
          workspace: options?.workspace || '.',
        },
      });
      try {
        const uploadResult = await this.uploadFiles(conversationId, options.pendingFiles);
        const filePaths = uploadResult.files.map((f) => f.path);
        await this.sendMessage(conversationId, { ...message, files: filePaths });
      } catch (error) {
        console.error('[API] Failed to upload pending files:', error);
        // Fall back: send original message without files
        await this.sendMessage(conversationId, message);
      }
    } else {
      // No files: create conversation with the initial message.
      // useConversation will call step() after subscribing (needsInitialStep: true above).
      await this.createConversation(conversationId, [message], {
        chat: {
          model: options?.model,
          stream: options?.stream,
          workspace: options?.workspace || '.',
        },
      });
    }

    // NOTE: step() is NOT called here — useConversation calls it after subscribing to SSE.

    // Return ID immediately after server creation
    return conversationId;
  }

  async sendMessage(logfile: string, message: Message, branch: string = 'main'): Promise<void> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    try {
      const request: SendMessageRequest = { ...message, branch };
      await this.fetchJson<{ status: string }>(`${this.baseUrl}/api/v2/conversations/${logfile}`, {
        method: 'POST',
        body: JSON.stringify(request),
        signal: this.controller?.signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      throw error;
    }
  }

  async editMessage(
    logfile: string,
    index: number,
    content?: string,
    truncate: boolean = false,
    files?: string[]
  ): Promise<ConversationResponse> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    const url = `${this.baseUrl}/api/v2/conversations/${logfile}/messages/${index}${truncate ? '?truncate=1' : ''}`;
    const body: Record<string, unknown> = {};
    if (content !== undefined) body.content = content;
    if (files !== undefined) body.files = files;
    return this.fetchJson<ConversationResponse>(url, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  }

  async deleteMessage(logfile: string, index: number): Promise<ConversationResponse> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    const url = `${this.baseUrl}/api/v2/conversations/${logfile}/messages/${index}`;
    return this.fetchJson<ConversationResponse>(url, {
      method: 'DELETE',
    });
  }

  async rerunTools(logfile: string): Promise<{ status: string; tool_ids: string[] }> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }
    const sessionId = this.sessions$.get(logfile);
    if (!sessionId) {
      throw new ApiClientError('No active session for this conversation');
    }
    return this.fetchJson(`${this.baseUrl}/api/v2/conversations/${logfile}/rerun`, {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });
  }

  async uploadFiles(
    conversationId: string,
    files: File[]
  ): Promise<{
    files: Array<{
      name: string;
      path: string;
      type: string;
      size: number;
      modified: string;
      mime_type: string | null;
    }>;
  }> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    const formData = new FormData();
    for (const file of files) {
      formData.append('file', file, file.name);
    }

    const headers: Record<string, string> = {};
    if (this.authHeader) {
      headers['Authorization'] = this.authHeader;
    }
    // Note: do NOT set Content-Type — browser sets it with boundary for multipart

    const response = await fetch(
      `${this.baseUrl}/api/v2/conversations/${conversationId}/workspace/upload`,
      { method: 'POST', headers, body: formData }
    );

    const data = await response.json();
    if (!response.ok) {
      throw new ApiClientError(
        isApiErrorResponse(data) ? data.error : `Upload failed: ${response.status}`,
        response.status
      );
    }
    return data;
  }

  async step(
    logfile: string,
    model?: string,
    stream: boolean = true,
    branch: string = 'main'
  ): Promise<void> {
    if (!this.isConnected) {
      throw new Error('Not connected to API');
    }

    // Only abort the previous step request if there is one
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }

    // Create new controller for this request
    this.controller = new AbortController();

    try {
      // Wait for a valid session ID before proceeding
      const sessionId: string | undefined = this.sessions$.get(logfile).get();
      if (!sessionId) {
        throw new ApiClientError('Session ID not found for conversation', 404);
      }
      console.log(`[ApiClient] Using session ID for generation: ${sessionId}`);

      let headers: {
        [key: string]: string;
      } = {
        'Content-Type': 'application/json',
        Connection: 'keep-alive',
      };

      if (this.authHeader) {
        headers = {
          ...headers,
          Authorization: this.authHeader,
        };
      }

      // Start generation
      const request = await this.fetchJson<{ status: string; message: string; session_id: string }>(
        `${this.baseUrl}/api/v2/conversations/${logfile}/step`,
        {
          method: 'POST',
          headers,
          body: JSON.stringify({ session_id: sessionId, model, branch, stream }),
          signal: this.controller.signal,
        }
      );
      console.log(`[ApiClient] Generation started:`, request);
    } catch (error) {
      if (this.controller?.signal.aborted) {
        console.log('Generation request aborted');
        return;
      }
      throw error;
    }
  }

  async confirmTool(
    logfile: string,
    toolId: string,
    action: 'confirm' | 'edit' | 'skip' | 'auto',
    options?: {
      content?: string;
      count?: number;
    }
  ): Promise<void> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    console.log(`[ApiClient] Confirming tool: ${toolId}, action: ${action}`);

    try {
      // Get the session ID
      const sessionId: string | undefined = this.sessions$.get(logfile).get();
      console.log(`[ApiClient] Using session for tool confirmation: ${sessionId}`);

      if (!sessionId) {
        throw new ApiClientError('Session ID not found for conversation', 404);
      }

      const request: ToolConfirmationRequest = {
        session_id: sessionId,
        tool_id: toolId,
        action,
      };

      if (action === 'edit' && options?.content) {
        request.content = options.content;
      } else if (action === 'auto' && options?.count) {
        request.count = options.count;
      }

      console.log(`[ApiClient] Sending tool confirmation request:`, request);

      await this.fetchJson<{ status: string }>(
        `${this.baseUrl}/api/v2/conversations/${logfile}/tool/confirm`,
        {
          method: 'POST',
          body: JSON.stringify(request),
          signal: this.controller?.signal,
        }
      );

      console.log(`[ApiClient] Tool confirmation successful`);
    } catch (error) {
      console.error(`[ApiClient] Tool confirmation failed:`, error);
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      throw error;
    }
  }

  async interruptGeneration(logfile: string): Promise<void> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    try {
      // Get the session ID
      const sessionId: string | undefined = this.sessions$.get(logfile).get();

      if (!sessionId) {
        throw new ApiClientError('Session ID not found for conversation', 404);
      }

      await this.fetchJson<{ status: string }>(
        `${this.baseUrl}/api/v2/conversations/${logfile}/interrupt`,
        {
          method: 'POST',
          body: JSON.stringify({ session_id: sessionId }),
          signal: this.controller?.signal,
        }
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError('Request aborted', 499);
      }
      throw error;
    }
  }

  async getChatConfig(logfile: string): Promise<ChatConfig> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    const response = await this.fetchJson<ChatConfig>(
      `${this.baseUrl}/api/v2/conversations/${logfile}/config`
    );

    return response;
  }

  async updateChatConfig(logfile: string, config: ChatConfig): Promise<void> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    await this.fetchJson<{ status: string }>(
      `${this.baseUrl}/api/v2/conversations/${logfile}/config`,
      {
        method: 'PATCH',
        body: JSON.stringify(config),
        signal: this.controller?.signal,
      }
    );
  }

  async deleteConversation(logfile: string): Promise<void> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    await this.fetchJson<{ status: string }>(`${this.baseUrl}/api/v2/conversations/${logfile}`, {
      method: 'DELETE',
      signal: this.controller?.signal,
    });
  }

  async createAgent(agentData: CreateAgentRequest): Promise<CreateAgentResponse> {
    if (!this.isConnected) {
      throw new ApiClientError('Not connected to API');
    }

    console.log('[ApiClient] Creating agent:', agentData);

    try {
      const response = await this.fetchJson<CreateAgentResponse>(`${this.baseUrl}/api/v2/agents`, {
        method: 'PUT',
        body: JSON.stringify(agentData),
        signal: this.controller?.signal,
      });
      console.log('[ApiClient] Agent created successfully:', response);
      return response;
    } catch (error) {
      console.error('[ApiClient] Failed to create agent:', error);
      throw error;
    }
  }

  async getExternalSessions(days = 30): Promise<ExternalSessionCatalogItem[]> {
    const url = `${this.baseUrl}/api/v2/external-sessions?days=${days}`;
    const resp = await this.fetchJson<{ sessions: ExternalSessionCatalogItem[] }>(url);
    return resp.sessions ?? [];
  }

  async getExternalSession(id: string, days = 30): Promise<ExternalSessionDetail> {
    const url = `${this.baseUrl}/api/v2/external-sessions/${id}?days=${days}`;
    return await this.fetchJson<ExternalSessionDetail>(url);
  }
}

export const createApiClient = (baseUrl?: string, authHeader?: string | null): ApiClient => {
  return new ApiClient(baseUrl, authHeader);
};
