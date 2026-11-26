import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TooltipProvider } from '@/components/ui/tooltip';
import { VoiceButton } from '../VoiceButton';
import type { VoiceStatus } from '@/types/api';

const mockGetVoiceStatus = jest.fn();
const mockStart = jest.fn();
const mockStop = jest.fn();
let mockCall = {
  state: 'idle' as string,
  error: null as string | null,
  level: 0,
  conversationId: null as string | null,
  serverId: null as string | null,
  provider: null as string | null,
};

// The primary (active) server; calls record it as their concrete owner.
// (Defined inside the factories: jest.mock is hoisted above module-level consts.)
jest.mock('@/stores/servers', () => ({
  serverRegistry$: { activeServerId: { get: () => 'srv-primary', isActiveServerMock: true } },
}));
jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { isActiveServerMock?: boolean } | undefined) =>
    obs?.isActiveServerMock ? 'srv-primary' : true,
}));

const mockClient = {
  baseUrl: 'http://server.local',
  isConnected$: {},
  getVoiceStatus: (...args: unknown[]) => mockGetVoiceStatus(...args),
};
const mockGetClient = jest.fn((_serverId?: string) => mockClient);
jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ getClient: mockGetClient }),
}));

// The call itself (ticket, WebSocket, audio) lives in VoiceCallProvider.
jest.mock('@/contexts/VoiceCallContext', () => ({
  ...jest.requireActual('@/contexts/VoiceCallContext'),
  useVoiceCall: () => ({ ...mockCall, start: mockStart, stop: mockStop }),
}));

const bothProviders: VoiceStatus = {
  available: true,
  reason: null,
  default_provider: 'openai',
  providers: [
    { id: 'openai', label: 'OpenAI Realtime', available: true, reason: null },
    { id: 'grok', label: 'xAI Grok Realtime', available: true, reason: null },
  ],
};

function renderButton(props: Parameters<typeof VoiceButton>[0] = { conversationId: 'conv-1' }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <VoiceButton {...props} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

describe('VoiceButton', () => {
  beforeEach(() => {
    localStorage.clear();
    mockGetVoiceStatus.mockReset();
    mockStart.mockReset();
    mockStop.mockReset();
    mockCall = {
      state: 'idle',
      error: null,
      level: 0,
      conversationId: null,
      serverId: null,
      provider: null,
    };
    mockGetClient.mockClear();
  });

  it('renders nothing when the server has no working voice', async () => {
    mockGetVoiceStatus.mockResolvedValue({
      available: false,
      reason: 'voice is not enabled on this server',
      providers: [],
      default_provider: null,
    });
    const { container } = renderButton();
    await waitFor(() => expect(mockGetVoiceStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('starts a call for the conversation with the default provider', async () => {
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    renderButton();
    const button = await screen.findByRole('button', {
      name: 'Start realtime voice call (OpenAI Realtime)',
    });
    button.click();
    expect(mockStart).toHaveBeenCalledWith('conv-1', {
      client: mockClient,
      serverId: 'srv-primary',
      provider: 'openai',
    });
  });

  it('on a new chat, hands the conversation factory to the call', async () => {
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    const startConversation = jest.fn(async () => 'new-conv');
    renderButton({ startConversation });
    (await screen.findByRole('button', { name: /Start realtime voice call/ })).click();
    expect(mockStart).toHaveBeenCalledWith(startConversation, {
      client: mockClient,
      serverId: 'srv-primary',
      provider: 'openai',
    });
  });

  it('shows a call running in another chat and ends it on click', async () => {
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    mockCall = { ...mockCall, state: 'recording', conversationId: 'other-conv' };
    renderButton();
    const button = await screen.findByRole('button', {
      name: 'Voice call active in another chat. Click to end',
    });
    button.click();
    expect(mockStop).toHaveBeenCalled();
    expect(mockStart).not.toHaveBeenCalled();
  });

  it('uses the remembered provider when it is available', async () => {
    localStorage.setItem('gptme_voice_provider', 'grok');
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    renderButton();
    expect(
      await screen.findByRole('button', { name: 'Start realtime voice call (xAI Grok Realtime)' })
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose voice provider' })).toBeInTheDocument();
  });

  it('uses the owning server of a secondary-server conversation', async () => {
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    renderButton({ conversationId: 'conv-1', serverId: 'srv-2' });
    (await screen.findByRole('button', { name: /Start realtime voice call/ })).click();
    expect(mockGetClient).toHaveBeenCalledWith('srv-2');
    expect(mockStart).toHaveBeenCalledWith('conv-1', {
      client: mockClient,
      serverId: 'srv-2',
      provider: 'openai',
    });
  });

  it('treats a same-named conversation on another server as another chat', async () => {
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    mockCall = { ...mockCall, state: 'recording', conversationId: 'conv-1', serverId: 'srv-2' };
    renderButton({ conversationId: 'conv-1' });
    expect(
      await screen.findByRole('button', {
        name: 'Voice call active in another chat. Click to end',
      })
    ).toBeInTheDocument();
  });

  it('records the concrete primary server, so a later server switch is "another chat"', async () => {
    mockGetVoiceStatus.mockResolvedValue(bothProviders);
    // Started from the old primary; the active server is now srv-primary.
    mockCall = { ...mockCall, state: 'recording', conversationId: 'conv-1', serverId: 'srv-old' };
    renderButton({ conversationId: 'conv-1' });
    expect(
      await screen.findByRole('button', {
        name: 'Voice call active in another chat. Click to end',
      })
    ).toBeInTheDocument();
  });

  it('hides the provider picker with a single provider', async () => {
    mockGetVoiceStatus.mockResolvedValue({
      ...bothProviders,
      providers: [
        bothProviders.providers[0],
        { ...bothProviders.providers[1], available: false, reason: 'XAI_API_KEY not set' },
      ],
    });
    renderButton();
    await screen.findByRole('button', { name: 'Start realtime voice call (OpenAI Realtime)' });
    expect(screen.queryByRole('button', { name: 'Choose voice provider' })).toBeNull();
  });
});
