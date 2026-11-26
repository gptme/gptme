import { useMemo, useState } from 'react';
import { Bookmark, BookmarkCheck, Check, Star } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { ProviderIcon, hasProviderIcon } from '@/components/ProviderIcon';
import { useToast } from '@/components/ui/use-toast';
import type { ModelInfo, useModels } from '@/hooks/useModels';
import { useUserSettings } from '@/hooks/useUserSettings';
import {
  DEFAULT_GALLERY_FILTERS,
  filterAndSortModels,
  MODEL_CAPABILITIES,
  MODEL_SORTS,
  quotedPrice,
  type GalleryFilters,
} from '@/utils/modelGallery';

const PAGE_SIZE = 40;
const tokens = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 });
const dollars = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumSignificantDigits: 4,
});

function priceLabel(amount: number | null | undefined) {
  const price = quotedPrice(amount);
  return price === undefined ? '—' : dollars.format(price);
}

function ModelRow({
  model,
  selected,
  favorite,
  recommended,
  isDefault,
  defaultSaving,
  favoriteSaving,
  onSelect,
  onFavorite,
  onSetDefault,
}: {
  model: ModelInfo;
  selected: boolean;
  favorite: boolean;
  recommended: boolean;
  isDefault: boolean;
  defaultSaving: boolean;
  favoriteSaving: boolean;
  onSelect: () => void;
  onFavorite: () => void;
  onSetDefault: () => void;
}) {
  const DefaultIcon = isDefault ? BookmarkCheck : Bookmark;
  return (
    <li className="flex min-w-0 items-start gap-1 rounded-md border bg-card text-card-foreground">
      {hasProviderIcon(model.provider) && (
        <div className="shrink-0 pl-3 pt-3.5">
          <ProviderIcon provider={model.provider} />
        </div>
      )}
      <button
        type="button"
        aria-label={`Select ${model.model}`}
        onClick={onSelect}
        className="min-w-0 flex-1 space-y-1 rounded-md p-3 text-left hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="flex items-center gap-2 text-sm font-medium">
          <span className="[overflow-wrap:anywhere]">{model.model}</span>
          {selected && (
            <>
              <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
              <span className="sr-only">Current model</span>
            </>
          )}
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span>{model.provider}</span>
          <span>Context: {model.context > 0 ? tokens.format(model.context) : '—'}</span>
          <span>Max output: {model.max_output ? tokens.format(model.max_output) : '—'}</span>
          <span className="tabular-nums">
            {priceLabel(model.price_input)} in / {priceLabel(model.price_output)} out
          </span>
        </span>
        <span className="flex flex-wrap gap-1 text-xs">
          {[
            ...Object.entries(MODEL_CAPABILITIES)
              .filter(([key]) => model[key as keyof typeof MODEL_CAPABILITIES])
              .map(([, label]) => label),
            ...(recommended ? ['Recommended'] : []),
            ...(model.deprecated ? ['Deprecated'] : []),
          ].map((label) => (
            <span key={label} className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
              {label}
            </span>
          ))}
        </span>
      </button>
      <div className="flex shrink-0 gap-0.5 p-1">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`Favorite ${model.model}`}
          aria-pressed={favorite}
          disabled={favoriteSaving}
          title={favorite ? 'Remove from favorites' : 'Add to favorites'}
          onClick={onFavorite}
        >
          <Star className={`h-4 w-4 ${favorite ? 'fill-yellow-400 text-yellow-500' : ''}`} />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={isDefault ? `${model.model} is the default` : `Set ${model.model} as default`}
          disabled={isDefault || defaultSaving}
          title={isDefault ? 'Default for new chats' : 'Set as default for new chats'}
          onClick={onSetDefault}
        >
          <DefaultIcon className={`h-4 w-4 ${isDefault ? 'fill-current text-primary' : ''}`} />
        </Button>
      </div>
    </li>
  );
}

export function ModelGallery({
  catalog,
  value,
  onSelect,
  onSetDefault,
  defaultSaving,
  onCloseAutoFocus,
}: {
  catalog: ReturnType<typeof useModels>;
  value: string;
  onSelect: (id: string) => void;
  onSetDefault: (id: string) => void;
  defaultSaving: boolean;
  onCloseAutoFocus: (event: Event) => void;
}) {
  const { models, isLoading, error, favorites, recommendedModels, defaultModel, toggleFavorite } =
    catalog;
  const { settings, isLoading: settingsLoading } = useUserSettings();
  const { toast } = useToast();
  const [favoriteSaving, setFavoriteSaving] = useState(false);
  const [filters, setFilters] = useState(DEFAULT_GALLERY_FILTERS);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const available = useMemo(
    () => (settings?.providers_configured ? new Set(settings.providers_configured) : null),
    [settings]
  );
  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);
  const recommended = useMemo(() => new Set(recommendedModels), [recommendedModels]);
  const { rows, total, providers } = useMemo(
    () => filterAndSortModels(models, filters, available, favoriteSet, recommended),
    [models, filters, available, favoriteSet, recommended]
  );
  const hasDeprecated = models.some((model) => model.deprecated);
  const update = (changes: Partial<GalleryFilters>) => {
    setFilters((current) => ({ ...current, ...changes }));
    setLimit(PAGE_SIZE);
  };
  const favorite = async (id: string) => {
    setFavoriteSaving(true);
    if (!(await toggleFavorite(id))) {
      toast({ variant: 'destructive', title: 'Failed to update favorites' });
    }
    setFavoriteSaving(false);
  };
  const providerButton = (id: string | null, count: number) => (
    <Button
      key={id}
      type="button"
      size="sm"
      variant={filters.provider === id ? 'secondary' : 'outline'}
      aria-pressed={filters.provider === id}
      onClick={() => update({ provider: id })}
      className="h-8 whitespace-nowrap"
    >
      {id ?? 'All providers'} ({count})
    </Button>
  );

  return (
    <DialogContent
      onCloseAutoFocus={onCloseAutoFocus}
      className="flex h-dvh max-w-none flex-col gap-3 overflow-y-auto overscroll-contain p-4 sm:h-[min(86dvh,50rem)] sm:w-[calc(100%-2rem)] sm:max-w-4xl sm:p-5"
    >
      <header className="space-y-1.5 pr-8">
        <DialogTitle>Model gallery</DialogTitle>
        <DialogDescription>
          Compare models and pick one for this chat. Prices in USD per 1M tokens; — means unknown.
        </DialogDescription>
      </header>
      <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
        <Input
          type="search"
          aria-label="Search model gallery"
          placeholder="Search by model name or ID…"
          value={filters.query}
          onChange={(e) => update({ query: e.target.value })}
        />
        <select
          aria-label="Sort models"
          className="h-10 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={filters.sort}
          onChange={(e) => update({ sort: e.target.value as GalleryFilters['sort'] })}
        >
          {Object.entries(MODEL_SORTS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div
        role="group"
        aria-label="Providers"
        className="flex shrink-0 gap-1.5 overflow-x-auto p-1"
      >
        {providerButton(null, total)}
        {providers.map(([id, count]) => providerButton(id, count))}
      </div>
      <div
        role="group"
        aria-label="Filter models"
        className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm"
      >
        <label className="flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            title="Show models from providers with configured credentials or subscriptions"
            disabled={available === null}
            checked={filters.availableOnly && available !== null}
            onChange={(e) => update({ availableOnly: e.target.checked })}
          />
          Available only
        </label>
        {available === null && (
          <span className="text-xs text-muted-foreground">
            {settingsLoading ? 'Checking configured providers…' : 'Provider availability unknown'}
          </span>
        )}
        {Object.entries(MODEL_CAPABILITIES).map(([key, label]) => {
          const capability = key as keyof typeof MODEL_CAPABILITIES;
          return (
            <label key={key} className="flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                checked={filters.capabilities.includes(capability)}
                onChange={(e) =>
                  update({
                    capabilities: e.target.checked
                      ? [...filters.capabilities, capability]
                      : filters.capabilities.filter((item) => item !== capability),
                  })
                }
              />
              {label}
            </label>
          );
        })}
        {(
          [
            ['favoritesOnly', 'Favorites only'],
            ['recommendedOnly', 'Recommended only'],
            ...(hasDeprecated ? ([['hideDeprecated', 'Hide deprecated']] as const) : []),
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={filters[key]}
              onChange={(e) => update({ [key]: e.target.checked })}
            />
            {label}
          </label>
        ))}
        <label className="flex items-center gap-1.5">
          Min. context
          <select
            className="rounded-md border border-input bg-background p-1"
            value={filters.minContext}
            onChange={(e) => update({ minContext: Number(e.target.value) })}
          >
            {[0, 16000, 32000, 64000, 128000, 200000, 1000000].map((size) => (
              <option key={size} value={size}>
                {size ? `${tokens.format(size)}+ tokens` : 'Any'}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="min-h-44 flex-1 space-y-2 border-t pt-2 sm:overflow-y-auto sm:pr-1">
        <p role="status" className="text-xs text-muted-foreground">
          {isLoading
            ? 'Loading models…'
            : (error ?? `${rows.length} ${rows.length === 1 ? 'model' : 'models'}`)}
        </p>
        {!isLoading && !error && rows.length === 0 && (
          <p className="rounded-md bg-muted p-4 text-sm text-muted-foreground">
            No models match these filters.
          </p>
        )}
        <ul className="space-y-1.5" aria-label="Matching models">
          {rows.slice(0, limit).map((model) => (
            <ModelRow
              key={model.id}
              model={model}
              selected={model.id === value}
              favorite={favoriteSet.has(model.id)}
              recommended={recommended.has(model.id)}
              isDefault={model.id === defaultModel}
              defaultSaving={defaultSaving}
              favoriteSaving={favoriteSaving}
              onSelect={() => onSelect(model.id)}
              onFavorite={() => void favorite(model.id)}
              onSetDefault={() => onSetDefault(model.id)}
            />
          ))}
        </ul>
        {rows.length > limit && (
          <Button type="button" variant="outline" onClick={() => setLimit(limit + PAGE_SIZE)}>
            Show more ({rows.length - limit} remaining)
          </Button>
        )}
      </div>
    </DialogContent>
  );
}
