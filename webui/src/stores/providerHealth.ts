import { observable } from '@legendapp/state';

export type ProviderHealthStatus = 'ok' | 'configured' | 'error';

export type ProviderHealthEntry = {
  status: ProviderHealthStatus;
  latency_ms: number | null;
  error: string | null;
};

export type ProviderHealthResponse = {
  providers: Record<string, ProviderHealthEntry>;
};

export const providerHealth$ = observable<{
  data: ProviderHealthResponse | null;
  isLoading: boolean;
  error: string | null;
}>({
  data: null,
  isLoading: false,
  error: null,
});

export function hasAnyProviderError(data: ProviderHealthResponse | null): boolean {
  if (!data) return false;
  return Object.values(data.providers).some((p) => p.status === 'error');
}
