import { Star, Check, ChevronsUpDown } from 'lucide-react';
import { useMemo, useState, type FC } from 'react';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import {
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { ProviderIcon, hasProviderIcon } from '@/components/ProviderIcon';
import { useModels, type ModelInfo } from '@/hooks/useModels';
import type { Control, FieldPath, FieldValues } from 'react-hook-form';

// --- Shared internals ---

const ModelItem: FC<{
  model: ModelInfo;
  isSelected: boolean;
  isFavorite: boolean;
  showProvider: boolean;
  onToggleFavorite: () => void;
}> = ({ model, isSelected, isFavorite, showProvider, onToggleFavorite }) => (
  <div className="flex w-full items-center justify-between gap-2">
    <div className="flex min-w-0 flex-col">
      <div className="flex items-center gap-2">
        {showProvider && hasProviderIcon(model.provider) && (
          <ProviderIcon provider={model.provider} />
        )}
        <span className="truncate">
          {showProvider && !hasProviderIcon(model.provider)
            ? `${model.provider}/${model.model}`
            : model.model}
        </span>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {model.context > 0 && <span>{Math.round(model.context / 1000)}k ctx</span>}
        {model.supports_vision && <span>vision</span>}
        {model.supports_reasoning && <span>reasoning</span>}
      </div>
    </div>
    <div className="flex flex-shrink-0 items-center gap-1">
      {isSelected && <Check className="h-4 w-4" />}
      <button
        type="button"
        aria-label={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
        title={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onToggleFavorite();
        }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <Star className={`h-3.5 w-3.5 ${isFavorite ? 'fill-yellow-400 text-yellow-400' : ''}`} />
      </button>
    </div>
  </div>
);

function useModelGroups() {
  const { models, availableModels, recommendedModels, favorites, toggleFavorite } = useModels();

  const recommendedSet = useMemo(() => new Set(recommendedModels), [recommendedModels]);
  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);

  const availableFavorites = useMemo(
    () =>
      favorites
        .filter((id) => availableModels.includes(id))
        .map((id) => models.find((m) => m.id === id)!)
        .filter(Boolean),
    [favorites, availableModels, models]
  );

  // Favorites take precedence over the Recommended group to avoid duplicates.
  const availableRecommended = useMemo(
    () =>
      recommendedModels
        .filter((id) => availableModels.includes(id) && !favoriteSet.has(id))
        .map((id) => models.find((m) => m.id === id)!)
        .filter(Boolean),
    [recommendedModels, availableModels, models, favoriteSet]
  );

  const providerGroups = useMemo(() => {
    const groups: Record<string, ModelInfo[]> = {};
    for (const model of models) {
      if (recommendedSet.has(model.id) || favoriteSet.has(model.id)) continue;
      if (!groups[model.provider]) {
        groups[model.provider] = [];
      }
      groups[model.provider].push(model);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [models, recommendedSet, favoriteSet]);

  return {
    models,
    availableFavorites,
    availableRecommended,
    providerGroups,
    favoriteSet,
    toggleFavorite,
  };
}

const ModelCommandList: FC<{
  value?: string;
  onSelect: (modelId: string) => void;
}> = ({ value, onSelect }) => {
  const { availableFavorites, availableRecommended, providerGroups, favoriteSet, toggleFavorite } =
    useModelGroups();

  // Substring filter instead of cmdk's default fuzzy match
  const filter = (value: string, search: string, keywords?: string[]) => {
    const haystack = [value, ...(keywords || [])].join(' ').toLowerCase();
    const terms = search.toLowerCase().split(/\s+/);
    return terms.every((term) => haystack.includes(term)) ? 1 : 0;
  };

  const renderItem = (model: ModelInfo, showProvider: boolean) => (
    <CommandItem
      key={model.id}
      value={model.id}
      keywords={[model.provider, model.model]}
      onSelect={() => onSelect(model.id)}
    >
      <ModelItem
        model={model}
        isSelected={model.id === value}
        isFavorite={favoriteSet.has(model.id)}
        showProvider={showProvider}
        onToggleFavorite={() => void toggleFavorite(model.id)}
      />
    </CommandItem>
  );

  return (
    <Command className="rounded-lg" filter={filter}>
      <CommandInput placeholder="Search models..." />
      {/* stopPropagation lets the wheel scroll this list even when rendered
          inside a Radix Dialog, whose react-remove-scroll otherwise eats the
          wheel event on document. Harmless outside a dialog. */}
      <CommandList className="max-h-[350px]" onWheel={(e) => e.stopPropagation()}>
        <CommandEmpty>No models found.</CommandEmpty>

        {availableFavorites.length > 0 && (
          <CommandGroup heading="Favorites">
            {availableFavorites.map((model) => renderItem(model, true))}
          </CommandGroup>
        )}

        {availableRecommended.length > 0 && (
          <CommandGroup heading="Recommended">
            {availableRecommended.map((model) => renderItem(model, true))}
          </CommandGroup>
        )}

        {providerGroups.map(([provider, providerModels]) => (
          <CommandGroup
            key={provider}
            heading={
              <span className="flex items-center gap-1.5">
                {hasProviderIcon(provider) && <ProviderIcon provider={provider} size={12} />}
                {provider}
              </span>
            }
          >
            {providerModels.map((model) => renderItem(model, false))}
          </CommandGroup>
        ))}
      </CommandList>
    </Command>
  );
};

// --- Public API ---

/** Inline model picker (renders the Command list directly, no wrapper) */
export const ModelPicker: FC<{
  value?: string;
  onSelect: (modelId: string) => void;
}> = ({ value, onSelect }) => <ModelCommandList value={value} onSelect={onSelect} />;

/** Model picker as a popover button without form bindings. */
export function ModelPickerButton({
  value,
  onSelect,
  disabled = false,
  placeholder = 'Select model',
}: {
  value?: string;
  onSelect: (modelId: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const { models } = useModels();
  const modelInfo = models.find((m) => m.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="w-full justify-between font-normal"
        >
          {value ? (
            <span className="flex items-center gap-2 truncate">
              {modelInfo?.provider && <ProviderIcon provider={modelInfo.provider} />}
              {modelInfo?.model || value}
            </span>
          ) : (
            <span className="text-muted-foreground">{placeholder}</span>
          )}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <ModelCommandList
          value={value}
          onSelect={(id) => {
            onSelect(id);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

/** Model picker as a form field with popover trigger (for use in settings forms) */
export function ModelPickerField<T extends FieldValues = FieldValues>({
  control,
  name,
  disabled = false,
  placeholder = 'Select model',
  label = 'Model',
}: {
  control: Control<T>;
  name: FieldPath<T>;
  disabled?: boolean;
  placeholder?: string;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const { models } = useModels();

  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => {
        const modelInfo = models.find((m) => m.id === field.value);
        return (
          <FormItem className="flex flex-col">
            <FormLabel>{label}</FormLabel>
            <Popover open={open} onOpenChange={setOpen}>
              <PopoverTrigger asChild>
                <FormControl>
                  <Button
                    variant="outline"
                    role="combobox"
                    aria-expanded={open}
                    disabled={disabled}
                    className="w-full justify-between font-normal"
                  >
                    {field.value ? (
                      <span className="flex items-center gap-2 truncate">
                        {modelInfo?.provider && <ProviderIcon provider={modelInfo.provider} />}
                        {modelInfo?.model || field.value}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">{placeholder}</span>
                    )}
                    <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                  </Button>
                </FormControl>
              </PopoverTrigger>
              <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                <ModelCommandList
                  value={field.value}
                  onSelect={(id) => {
                    field.onChange(id);
                    setOpen(false);
                  }}
                />
              </PopoverContent>
            </Popover>
            <FormDescription>The model to use.</FormDescription>
            <FormMessage />
          </FormItem>
        );
      }}
    />
  );
}
