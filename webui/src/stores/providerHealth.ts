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

/**
 * Whether to surface a provider-health warning on the settings icon.
 *
 * Only true on a *full outage* — every known provider is erroring. A single
 * failing/unconfigured provider (e.g. gemini when the user only uses anthropic)
 * should not constantly nag the user to fix something they don't rely on.
 */
export function allProvidersDown(data: ProviderHealthResponse | null): boolean {
  if (!data) return false;
  const providers = Object.values(data.providers);
  if (providers.length === 0) return false;
  return providers.every((p) => p.status === 'error');
}
