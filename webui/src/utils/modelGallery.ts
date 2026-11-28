import type { ModelInfo } from '@/hooks/useModels';

export const MODEL_CAPABILITIES = {
  supports_vision: 'Vision',
  supports_reasoning: 'Reasoning',
  supports_streaming: 'Streaming',
} as const;

type Capability = keyof typeof MODEL_CAPABILITIES;
export const MODEL_SORTS = {
  name: 'Name (A–Z)',
  input: 'Input price (low to high)',
  output: 'Output price (low to high)',
  context: 'Context (largest first)',
  provider: 'Provider (A–Z)',
} as const;

export interface GalleryFilters {
  query: string;
  provider: string | null;
  capabilities: Capability[];
  minContext: number;
  availableOnly: boolean;
  hideDeprecated: boolean;
  favoritesOnly: boolean;
  recommendedOnly: boolean;
  sort: keyof typeof MODEL_SORTS;
}

export const DEFAULT_GALLERY_FILTERS: GalleryFilters = {
  query: '',
  provider: null,
  capabilities: [],
  minContext: 0,
  availableOnly: false,
  hideDeprecated: true,
  favoritesOnly: false,
  recommendedOnly: false,
  sort: 'name',
};

export function quotedPrice(price: number | null | undefined): number | undefined {
  // v2 uses zero as the default for unpriced models. It cannot distinguish an
  // unpriced model from a genuinely free one, so don't promise either is free.
  return price != null && Number.isFinite(price) && price > 0 ? price : undefined;
}

export function filterAndSortModels(
  models: readonly ModelInfo[],
  filters: GalleryFilters,
  availableProviders: ReadonlySet<string> | null,
  favorites: ReadonlySet<string>,
  recommended: ReadonlySet<string>
) {
  const terms = filters.query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const counts = new Map<string, number>();
  const rows: ModelInfo[] = [];
  let total = 0;

  for (const model of models) {
    const { provider, id } = model;
    if (!counts.has(provider)) counts.set(provider, 0);
    if (filters.availableOnly && availableProviders && !availableProviders.has(provider)) continue;
    if (filters.hideDeprecated && model.deprecated) continue;
    if (filters.favoritesOnly && !favorites.has(id)) continue;
    if (filters.recommendedOnly && !recommended.has(id)) continue;
    if (filters.minContext > 0 && !(model.context >= filters.minContext)) continue;
    if (!filters.capabilities.every((capability) => model[capability])) continue;
    const searchable = `${id} ${model.model}`.toLowerCase();
    if (!terms.every((term) => searchable.includes(term))) continue;

    // Show what switching providers would yield under the other filters.
    counts.set(provider, (counts.get(provider) ?? 0) + 1);
    total++;
    if (filters.provider === null || filters.provider === provider) rows.push(model);
  }

  rows.sort((a, b) => {
    const { sort } = filters;
    if (sort === 'context' && a.context !== b.context) return (b.context || 0) - (a.context || 0);
    if (sort === 'provider') {
      const order = a.provider.localeCompare(b.provider);
      if (order) return order;
    }
    if (sort === 'input' || sort === 'output') {
      const key = sort === 'input' ? 'price_input' : 'price_output';
      const first = quotedPrice(a[key]) ?? Infinity;
      const second = quotedPrice(b[key]) ?? Infinity;
      if (first !== second) return first - second;
    }
    return a.model.localeCompare(b.model) || a.id.localeCompare(b.id);
  });

  return {
    rows,
    total,
    providers: [...counts].sort(([a], [b]) => a.localeCompare(b)),
  };
}
