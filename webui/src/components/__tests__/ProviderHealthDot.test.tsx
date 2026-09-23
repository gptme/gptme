import '@testing-library/jest-dom';
import { act, render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { ProviderHealthDot } from '../ProviderHealthDot';
import { providerHealth$ } from '@/stores/providerHealth';

const isConnected$ = observable(true);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: { baseUrl: 'http://localhost:5700', authHeader: null },
    isConnected$,
  }),
}));

describe('ProviderHealthDot', () => {
  beforeEach(() => {
    act(() => {
      isConnected$.set(true);
      providerHealth$.set({
        data: {
          providers: {
            anthropic: { status: 'ok', latency_ms: 42, error: null },
          },
        },
        isLoading: false,
        error: null,
      });
    });
  });

  it('renders a focusable health button when connected and data is fresh', () => {
    render(<ProviderHealthDot provider="anthropic" />);
    const trigger = screen.getByRole('button', { name: 'anthropic health: Reachable' });
    expect(trigger).toBeInTheDocument();
    expect(trigger).not.toHaveAttribute('tabindex', '-1');
  });

  it('hides the badge when the last health fetch failed', () => {
    act(() => {
      providerHealth$.error.set('Failed to fetch provider health');
    });
    const { container } = render(<ProviderHealthDot provider="anthropic" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('hides the badge when the current server is disconnected', () => {
    act(() => {
      isConnected$.set(false);
    });
    const { container } = render(<ProviderHealthDot provider="anthropic" />);
    expect(container).toBeEmptyDOMElement();
  });
});
