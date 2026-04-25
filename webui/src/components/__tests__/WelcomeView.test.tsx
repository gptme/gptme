import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SettingsProvider } from '@/contexts/SettingsContext';
import { WelcomeView } from '../WelcomeView';

const mockNavigate = jest.fn();
const mockInvalidateQueries = jest.fn();
const mockConnect = jest.fn();
const isConnected$ = observable(true);

jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

jest.mock('@/contexts/ApiContext', () => {
  return {
    useApi: () => ({
      api: {
        createConversationWithPlaceholder: jest.fn(),
      },
      isConnected$,
      connect: mockConnect,
      connectionConfig: { baseUrl: 'http://localhost:5700' },
      switchServer: jest.fn(),
    }),
  };
});

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({
    invalidateQueries: mockInvalidateQueries,
  }),
}));

jest.mock('@/stores/servers', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    serverRegistry$: observable({
      servers: [{ id: 'default', name: 'Default' }],
      activeServerId: 'default',
    }),
    getConnectedServers: () => [{ id: 'default', name: 'Default' }],
  };
});

jest.mock('../ChatInput', () => ({
  ChatInput: () => <div data-testid="chat-input" />,
}));

jest.mock('../ExamplesSection', () => ({
  ExamplesSection: () => <div data-testid="examples-section" />,
}));

describe('WelcomeView', () => {
  beforeEach(() => {
    isConnected$.set(true);
    mockConnect.mockReset();
    mockNavigate.mockClear();
    mockInvalidateQueries.mockClear();
  });

  it('renders the refreshed new chat copy and quick suggestions', () => {
    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByRole('heading', { name: 'What are you working on?' })).toBeInTheDocument();
    expect(
      screen.getByText(/Start with a real task, question, or rough idea\./)
    ).toBeInTheDocument();
    expect(screen.getByText('Try one of these')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Write a Python script' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show history' })).toBeInTheDocument();
    expect(screen.queryByText('How can I help you today?')).not.toBeInTheDocument();
  });

  it('shows an actionable disconnected-state banner when no server is connected', () => {
    isConnected$.set(false);

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText('No gptme server connected')).toBeInTheDocument();
    expect(
      screen.getByText(/Start a local gptme server or point the app at another server/i)
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry connection/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy start command/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /server settings/i })).toBeInTheDocument();
  });
});
