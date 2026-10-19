import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { observable } from '@legendapp/state';
import { HistoryView } from '../HistoryView';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { ConnectionConfig } from '@/utils/connectionConfig';
import type { ConversationSummary } from '@/types/conversation';

const mockNavigate = jest.fn();
const mockGetConversations = jest.fn();
const mockSearchConversations = jest.fn();
const mockApi = {
  isConnected$: observable(true),
  getConversations: (...args: unknown[]) => mockGetConversations(...args),
  searchConversations: (...args: unknown[]) => mockSearchConversations(...args),
};

const BASE_URL = 'https://same.synthetic.invalid';

let mockConnectionConfig: ConnectionConfig = {
  baseUrl: BASE_URL,
  authToken: null,
  useAuthToken: false,
};

jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ api: mockApi, connectionConfig: mockConnectionConfig }),
}));

jest.mock('@/components/ActivityCalendar', () => ({
  ActivityCalendar: () => <div data-testid="activity-calendar" />,
}));

const conversation = (id: string, name: string): ConversationSummary => ({
  id,
  name,
  modified: Date.UTC(2026, 4, 31, 12, 0, 0) / 1000,
  messages: 2,
  last_message_preview: 'synthetic preview',
  last_message_role: 'user',
  workspace: '.',
});

/** JWT-shaped token; the signature is irrelevant, only the `sub` claim is read. */
const jwt = (sub: string, issuedAt: number) =>
  `header.${btoa(JSON.stringify({ sub, iat: issuedAt })).replace(/=+$/, '')}.signature`;

const aliceToken = jwt('user-alice', 1);
const aliceRefreshedToken = jwt('user-alice', 2);
const bobToken = jwt('user-bob', 1);

const newClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

const wrap = (client: QueryClient) => (
  <QueryClientProvider client={client}>
    <TooltipProvider>
      <HistoryView />
    </TooltipProvider>
  </QueryClientProvider>
);

const renderHistory = (client: QueryClient) => render(wrap(client));

const switchTo = (
  view: ReturnType<typeof renderHistory>,
  client: QueryClient,
  config: ConnectionConfig
) => {
  mockConnectionConfig = config;
  view.rerender(wrap(client));
};

const pending = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

const account = (authToken: string, useAuthToken = true): ConnectionConfig => ({
  baseUrl: BASE_URL,
  authToken,
  useAuthToken,
});

describe('HistoryView credential-scoped caches', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockGetConversations.mockReset().mockResolvedValue([]);
    mockSearchConversations.mockReset().mockResolvedValue([]);
    mockConnectionConfig = {
      baseUrl: BASE_URL,
      authToken: null,
      useAuthToken: false,
    };
  });

  it('does not render the previous account summaries while the new account request is pending', async () => {
    const client = newClient();
    mockConnectionConfig = account(aliceToken);
    mockGetConversations.mockResolvedValueOnce([conversation('alice-chat', 'Alice chat')]);

    const view = renderHistory(client);
    expect(await screen.findByText('Alice chat')).toBeInTheDocument();

    const bobFetch = pending<ConversationSummary[]>();
    mockGetConversations.mockReturnValueOnce(bobFetch.promise);
    switchTo(view, client, account(bobToken));

    await waitFor(() => expect(mockGetConversations).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Alice chat')).not.toBeInTheDocument();

    await act(async () => {
      bobFetch.resolve([conversation('bob-chat', 'Bob chat')]);
    });

    expect(await screen.findByText('Bob chat')).toBeInTheDocument();
    expect(screen.queryByText('Alice chat')).not.toBeInTheDocument();
  });

  it('does not fall back to previous account data when the new account request fails', async () => {
    const client = newClient();
    mockConnectionConfig = account(aliceToken);
    mockGetConversations.mockResolvedValueOnce([conversation('alice-chat', 'Alice chat')]);

    const view = renderHistory(client);
    expect(await screen.findByText('Alice chat')).toBeInTheDocument();

    mockGetConversations.mockRejectedValueOnce(new Error('synthetic failure'));
    switchTo(view, client, account(bobToken));

    await waitFor(() => expect(mockGetConversations).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText('No conversations yet.')).toBeInTheDocument());
    expect(screen.queryByText('Alice chat')).not.toBeInTheDocument();
  });

  it('retains the same identity across a JWT refresh for the same subject', async () => {
    const client = newClient();
    mockConnectionConfig = account(aliceToken);
    mockGetConversations.mockResolvedValueOnce([conversation('alice-chat', 'Alice chat')]);

    const view = renderHistory(client);
    expect(await screen.findByText('Alice chat')).toBeInTheDocument();

    switchTo(view, client, account(aliceRefreshedToken));

    // Same subject => same cache identity; the summary is reused, not refetched.
    await waitFor(() => expect(screen.getByText('Alice chat')).toBeInTheDocument());
    expect(mockGetConversations).toHaveBeenCalledTimes(1);
    const summaryQueries = client
      .getQueryCache()
      .getAll()
      .filter((query) => query.queryKey[0] === 'conversations-all');
    expect(summaryQueries).toHaveLength(1);
    expect(summaryQueries[0].queryKey[2]).toBe('user-user-alice');
  });

  it('isolates identity for opaque token changes on the same server', async () => {
    const client = newClient();
    mockConnectionConfig = account('opaque-token-a');
    mockGetConversations.mockResolvedValueOnce([conversation('a-chat', 'Opaque A chat')]);

    const view = renderHistory(client);
    expect(await screen.findByText('Opaque A chat')).toBeInTheDocument();

    const nextFetch = pending<ConversationSummary[]>();
    mockGetConversations.mockReturnValueOnce(nextFetch.promise);
    switchTo(view, client, account('opaque-token-b'));

    await waitFor(() => expect(mockGetConversations).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Opaque A chat')).not.toBeInTheDocument();

    await act(async () => {
      nextFetch.resolve([conversation('b-chat', 'Opaque B chat')]);
    });
    expect(await screen.findByText('Opaque B chat')).toBeInTheDocument();
  });

  it('isolates anonymous and auth-mode transitions', async () => {
    const client = newClient();
    mockConnectionConfig = { baseUrl: BASE_URL, authToken: null, useAuthToken: false };
    mockGetConversations.mockResolvedValueOnce([conversation('anon-chat', 'Anonymous chat')]);

    const view = renderHistory(client);
    expect(await screen.findByText('Anonymous chat')).toBeInTheDocument();

    const signedInFetch = pending<ConversationSummary[]>();
    mockGetConversations.mockReturnValueOnce(signedInFetch.promise);
    switchTo(view, client, account(aliceToken));

    await waitFor(() => expect(mockGetConversations).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Anonymous chat')).not.toBeInTheDocument();

    await act(async () => {
      signedInFetch.resolve([conversation('alice-chat', 'Alice chat')]);
    });
    expect(await screen.findByText('Alice chat')).toBeInTheDocument();

    // Turning auth off while a token is still configured must not reuse the
    // signed-in account's cached summaries either: it returns to the anonymous
    // identity (whose list is already cached), never Alice's data.
    switchTo(view, client, { baseUrl: BASE_URL, authToken: aliceToken, useAuthToken: false });

    await waitFor(() => expect(screen.getByText('Anonymous chat')).toBeInTheDocument());
    expect(screen.queryByText('Alice chat')).not.toBeInTheDocument();
    expect(mockGetConversations).toHaveBeenCalledTimes(2);
  });

  it('does not render the previous account search results while the new account search is pending', async () => {
    const client = newClient();
    mockConnectionConfig = account(aliceToken);
    mockSearchConversations.mockResolvedValueOnce([
      conversation('alice-search', 'Alice search hit'),
    ]);

    const view = renderHistory(client);
    fireEvent.change(screen.getByRole('textbox', { name: 'Search conversations' }), {
      target: { value: 'fixture' },
    });

    expect(await screen.findByText('Alice search hit', {}, { timeout: 3000 })).toBeInTheDocument();

    const bobSearch = pending<ConversationSummary[]>();
    mockSearchConversations.mockReturnValueOnce(bobSearch.promise);
    switchTo(view, client, account(bobToken));

    await waitFor(() => expect(mockSearchConversations).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Alice search hit')).not.toBeInTheDocument();

    await act(async () => {
      bobSearch.resolve([conversation('bob-search', 'Bob search hit')]);
    });
    expect(await screen.findByText('Bob search hit')).toBeInTheDocument();
    expect(screen.queryByText('Alice search hit')).not.toBeInTheDocument();
  });

  it('never stores raw bearer tokens in query keys', async () => {
    const client = newClient();
    const opaqueToken = 'opaque-secret-token-value';
    mockConnectionConfig = account(aliceToken);

    const view = renderHistory(client);
    fireEvent.change(screen.getByRole('textbox', { name: 'Search conversations' }), {
      target: { value: 'fixture' },
    });
    await waitFor(() => expect(mockSearchConversations).toHaveBeenCalledTimes(1), {
      timeout: 3000,
    });

    // Move to an opaque token so both scope shapes are exercised.
    switchTo(view, client, account(opaqueToken));
    await waitFor(() => expect(mockGetConversations).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockSearchConversations).toHaveBeenCalledTimes(2));

    const keys = client
      .getQueryCache()
      .getAll()
      .map((query) => JSON.stringify(query.queryKey))
      .join('\n');

    expect(keys).not.toContain(aliceToken);
    expect(keys).not.toContain(opaqueToken);
    expect(keys).toContain('user-user-alice');
    expect(keys).toMatch(/token-[0-9a-f]{16}/);
  });
});
