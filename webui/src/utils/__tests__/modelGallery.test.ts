import type { ModelInfo } from '@/hooks/useModels';
import { DEFAULT_GALLERY_FILTERS, filterAndSortModels, type GalleryFilters } from '../modelGallery';

function model(provider: string, name: string, overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    id: `${provider}/${name}`,
    provider,
    model: name,
    context: 32_000,
    supports_vision: false,
    supports_reasoning: false,
    supports_streaming: true,
    ...overrides,
  };
}

const alpha = model('openrouter', 'vendor/Alpha', {
  context: 200_000,
  price_input: 2,
  price_output: 3,
  supports_vision: true,
  supports_reasoning: true,
});
const beta = model('openai', 'Beta', {
  context: 128_000,
  price_input: 1,
  price_output: 5,
  supports_vision: true,
});
const delta = model('openrouter', 'Delta', {
  context: 320_000,
  price_input: 0,
  price_output: null,
  supports_vision: true,
  supports_reasoning: true,
  deprecated: true,
});
const epsilon = model('anthropic', 'Epsilon', { context: 64_000, supports_streaming: false });
const catalog = [alpha, beta, delta, epsilon];
const ids = (rows: ModelInfo[]) => rows.map((row) => row.id);

function gallery(filters: Partial<GalleryFilters> = {}, available: string[] | null = null) {
  return filterAndSortModels(
    catalog,
    { ...DEFAULT_GALLERY_FILTERS, ...filters },
    available === null ? null : new Set(available),
    new Set([alpha.id, delta.id]),
    new Set([alpha.id, beta.id])
  );
}

test('combines case-insensitive search across IDs and names with provider, capabilities and context', () => {
  expect(
    ids(
      gallery({
        query: ' OPENROUTER   Alpha ',
        provider: 'openrouter',
        capabilities: ['supports_vision', 'supports_reasoning'],
        minContext: 200_000,
      }).rows
    )
  ).toEqual([alpha.id]);
  expect(gallery({ query: 'alpha', minContext: 200_001 }).rows).toEqual([]);
  expect(ids(gallery({ capabilities: ['supports_streaming'], sort: 'context' }).rows)).toEqual([
    alpha.id,
    beta.id,
  ]);
});

test('provider counts follow all non-provider filters, including zero matches', () => {
  const result = gallery({ capabilities: ['supports_vision'], provider: 'openai' });
  expect(ids(result.rows)).toEqual([beta.id]);
  expect(result.total).toBe(2);
  expect(result.providers).toEqual([
    ['anthropic', 0],
    ['openai', 1],
    ['openrouter', 1],
  ]);
});

test('intersects favorites, recommendations and deprecation independently', () => {
  expect(ids(gallery({ favoritesOnly: true }).rows)).toEqual([alpha.id]);
  expect(ids(gallery({ favoritesOnly: true, hideDeprecated: false }).rows)).toEqual([
    delta.id,
    alpha.id,
  ]);
  expect(
    ids(gallery({ favoritesOnly: true, recommendedOnly: true, hideDeprecated: false }).rows)
  ).toEqual([alpha.id]);
});

test('available only means configured providers, not merely present in the catalog', () => {
  expect(ids(gallery({ availableOnly: true }, ['openai']).rows)).toEqual([beta.id]);
  expect(gallery({ availableOnly: true }, []).rows).toEqual([]);
  expect(gallery({ availableOnly: true }, null).total).toBe(3);
});

test('sorts by each price independently, with zero, null and missing quotes last', () => {
  expect(ids(gallery({ sort: 'input', hideDeprecated: false }).rows)).toEqual([
    beta.id,
    alpha.id,
    delta.id,
    epsilon.id,
  ]);
  expect(ids(gallery({ sort: 'output', hideDeprecated: false }).rows)).toEqual([
    alpha.id,
    beta.id,
    delta.id,
    epsilon.id,
  ]);
});

test('sorts by name, provider or descending context without mutating the catalog', () => {
  expect(ids(gallery().rows)).toEqual([beta.id, epsilon.id, alpha.id]);
  expect(ids(gallery({ sort: 'provider' }).rows)).toEqual([epsilon.id, beta.id, alpha.id]);
  expect(ids(gallery({ sort: 'context', hideDeprecated: false }).rows)).toEqual([
    delta.id,
    alpha.id,
    beta.id,
    epsilon.id,
  ]);
  expect(ids(catalog)).toEqual([alpha.id, beta.id, delta.id, epsilon.id]);
});
