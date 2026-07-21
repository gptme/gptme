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
 * Only true on a *full outage* — every known provider is in `error`. This is
 * intentionally conservative to avoid nagging: a single failing provider (e.g.
 * gemini when the user only uses anthropic) must not light up the badge.
 *
 * `ok` and `configured` both count as "not down" on purpose — `configured`
 * means credentials are present (the provider is plausibly usable, just not
 * actively health-checked), so it should not, on its own, suppress nothing nor
 * trigger a warning. The badge fires only when nothing is left but errors.
 */
export function allProvidersDown(data: ProviderHealthResponse | null): boolean {
  const providers = Object.values(data?.providers ?? {});
  if (providers.length === 0) return false;
  return providers.every((p) => p.status === 'error');
}
