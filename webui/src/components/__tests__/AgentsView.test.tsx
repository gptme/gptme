import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { AgentsView } from '../AgentsView';
import type { ConversationSummary } from '@/types/conversation';

const mockIsDemoMode = jest.fn(() => false);
const mockExtractAgents = jest.fn();

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { createAgent: jest.fn() },
    connectionConfig: { baseUrl: 'demo://offline' },
  }),
}));

jest.mock('../CreateAgentDialog', () => ({
  __esModule: true,
  default: () => <div data-testid="create-agent-dialog" />,
}));

jest.mock('@/utils/workspaceUtils', () => {
  const actual = jest.requireActual('@/utils/workspaceUtils');
  return {
    ...actual,
    extractAgentsFromConversations: (...args: unknown[]) => mockExtractAgents(...args),
  };
});

const renderAgentsView = (conversations: ConversationSummary[] = []) =>
  render(
    <BrowserRouter>
      <AgentsView conversations={conversations} />
    </BrowserRouter>
  );

describe('AgentsView', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
    mockExtractAgents.mockClear();
    mockExtractAgents.mockReturnValue([]);
  });

  it('offers agent creation with a live server', () => {
    renderAgentsView();

    expect(screen.getAllByRole('button', { name: 'Create Agent' })).toHaveLength(2);
  });

  it('does not offer an unsupported create action in offline demo mode', () => {
    mockIsDemoMode.mockReturnValue(true);
    renderAgentsView();

    expect(screen.queryByRole('button', { name: 'Create Agent' })).not.toBeInTheDocument();
    expect(screen.getByText('Agent creation requires a live gptme server.')).toBeInTheDocument();
  });

  describe('memoization', () => {
    it('does not re-extract agents on re-render with stable conversations reference', () => {
      const conversations: ConversationSummary[] = [];

      const { rerender } = render(
        <BrowserRouter>
          <AgentsView conversations={conversations} />
        </BrowserRouter>
      );
      expect(mockExtractAgents).toHaveBeenCalledTimes(1);

      // Re-render with the same reference — useMemo must skip re-extraction
      rerender(
        <BrowserRouter>
          <AgentsView conversations={conversations} />
        </BrowserRouter>
      );
      expect(mockExtractAgents).toHaveBeenCalledTimes(1);
    });

    it('re-extracts agents when conversations reference changes', () => {
      const { rerender } = render(
        <BrowserRouter>
          <AgentsView conversations={[]} />
        </BrowserRouter>
      );
      expect(mockExtractAgents).toHaveBeenCalledTimes(1);

      // New array reference → useMemo must recompute
      rerender(
        <BrowserRouter>
          <AgentsView conversations={[]} />
        </BrowserRouter>
      );
      expect(mockExtractAgents).toHaveBeenCalledTimes(2);
    });
  });
});
