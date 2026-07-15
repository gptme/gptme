import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { AgentsView } from '../AgentsView';

const mockIsDemoMode = jest.fn(() => false);

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

const renderAgentsView = () =>
  render(
    <BrowserRouter>
      <AgentsView conversations={[]} />
    </BrowserRouter>
  );

describe('AgentsView', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
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
});
