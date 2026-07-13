import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { observable } from '@legendapp/state';
import { BrowserRouter } from 'react-router-dom';

const getSkills = jest.fn();
const isConnected$ = observable(true);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { getSkills },
    isConnected$,
  }),
}));

jest.mock('@/components/MenuBar', () => ({
  MenuBar: () => <div data-testid="menu-bar" />,
}));

jest.mock('@/components/MobileBottomNav', () => ({
  MobileBottomNav: () => <div data-testid="mobile-bottom-nav" />,
}));

import Skills from '../Skills';

function renderSkills() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Skills />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

describe('Skills page', () => {
  it('renders reputation badges, install count, and descriptions', async () => {
    getSkills.mockResolvedValueOnce({
      skills: [
        {
          name: 'code-review-helper',
          description: 'Review code changes',
          path: '/skills/code-review-helper/SKILL.md',
          category: 'code-review-helper',
          install_count: 12,
          reputation: {
            score: 0.82,
            band: 'excellent',
            band_label: 'Trusted',
            blocked: false,
            computed_at: '2026-07-13T21:00:00+00:00',
          },
        },
        {
          name: 'deploy-checklist',
          description: 'Check deployments',
          path: '/skills/deploy-checklist/SKILL.md',
          category: 'deploy-checklist',
          install_count: 0,
          reputation: {
            score: null,
            band: 'neutral',
            band_label: 'Unproven',
            blocked: false,
            computed_at: null,
          },
        },
      ],
    });

    renderSkills();

    expect(await screen.findAllByText('code-review-helper')).toHaveLength(2);
    expect(screen.getByText('Review code changes')).toBeInTheDocument();
    expect(screen.getByText('12 installs')).toBeInTheDocument();
    expect(screen.getByText('Trusted')).toBeInTheDocument();
    expect(screen.getByText('82')).toBeInTheDocument();
    expect(screen.getAllByText('deploy-checklist')).toHaveLength(2);
    expect(screen.getByText('Unproven')).toBeInTheDocument();
  });
});
