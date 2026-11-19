import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { AgentOverview } from '../AgentOverview';
import type { ConversationSummary } from '@/types/conversation';

const conversations: ConversationSummary[] = [
  {
    id: 'shared',
    name: 'Local conversation',
    modified: 100,
    agent_name: 'Bob',
    agent_path: '/agents/bob',
    serverId: 'local',
  },
  {
    id: 'shared',
    name: 'Remote conversation',
    modified: 200,
    agent_name: 'Bob',
    agent_path: '/agents/bob',
    serverId: 'remote',
    serverName: 'Remote',
    agent_urls: { dashboard: 'https://example.com/dashboard', unsafe: 'javascript:alert(1)' },
  },
  {
    id: 'other',
    name: 'Different workspace',
    modified: 300,
    agent_name: 'Bob',
    agent_path: '/other/bob',
    serverId: 'remote',
  },
];

jest.mock('@/stores/servers', () => ({
  serverRegistry$: {
    get: () => ({
      servers: [
        { id: 'local', baseUrl: 'http://localhost:5700' },
        { id: 'remote', baseUrl: 'https://remote.example.com' },
      ],
    }),
  },
}));

function Location() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function renderOverview(query = 'path=%2Fagents%2Fbob&server=remote') {
  return render(
    <MemoryRouter initialEntries={[`/agents/overview?${query}`]}>
      <Routes>
        <Route path="/agents/overview" element={<AgentOverview conversations={conversations} />} />
        <Route path="*" element={<Location />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('AgentOverview', () => {
  it('resolves a deep link by path and server, not by display name', () => {
    renderOverview();
    expect(screen.getByRole('heading', { name: 'Bob' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Remote conversation/ })).toHaveAttribute(
      'href',
      '/chat/shared?server=remote'
    );
    expect(screen.queryByText('Local conversation')).not.toBeInTheDocument();
    expect(screen.queryByText('Different workspace')).not.toBeInTheDocument();
    expect(screen.getByText(/Runtime status is not available/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute(
      'href',
      'https://example.com/dashboard'
    );
    expect(screen.queryByRole('link', { name: /unsafe/ })).not.toBeInTheDocument();
  });

  it('offers explicit conversation navigation with the same identity', () => {
    renderOverview();
    fireEvent.click(screen.getByRole('link', { name: 'View conversations' }));
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/chat?agentPath=%2Fagents%2Fbob&server=remote'
    );
  });

  it('does not silently fall back to another server for an unknown agent', () => {
    renderOverview('path=%2Fagents%2Fbob&server=missing');
    expect(screen.getByText('Agent not found in loaded conversations')).toBeInTheDocument();
    expect(screen.queryByText('Remote conversation')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /All agents/ })).toHaveAttribute('href', '/agents');
  });
});
