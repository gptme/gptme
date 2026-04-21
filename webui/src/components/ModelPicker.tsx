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
  isRecommended: boolean;
  showProvider: boolean;
}> = ({ model, isSelected, isRecommended, showProvider }) => (
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
        {isRecommended && (
          <Star className="h-3 w-3 flex-shrink-0 fill-yellow-400 text-yellow-400" />
        )}
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {model.context > 0 && <span>{Math.round(model.context / 1000)}k ctx</span>}
        {model.supports_vision && <span>vision</span>}
        {model.supports_reasoning && <span>reasoning</span>}
      </div>
    </div>
    {isSelected && <Check className="h-4 w-4 flex-shrink-0" />}
  </div>
);

function useModelGroups() {
  const { models, availableModels, recommendedModels } = useModels();

  const recommendedSet = useMemo(() => new Set(recommendedModels), [recommendedModels]);

  const availableRecommended = useMemo(
    () =>
      recommendedModels
        .filter((id) => availableModels.includes(id))
        .map((id) => models.find((m) => m.id === id)!)
        .filter(Boolean),
    [recommendedModels, availableModels, models]
  );

  const providerGroups = useMemo(() => {
    const groups: Record<string, ModelInfo[]> = {};
    for (const model of models) {
      if (recommendedSet.has(model.id)) continue;
      if (!groups[model.provider]) {
        groups[model.provider] = [];
      }
      groups[model.provider].push(model);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [models, recommendedSet]);

  return { models, availableRecommended, providerGroups, recommendedSet };
}

const ModelCommandList: FC<{
  value?: string;
  onSelect: (modelId: string) => void;
}> = ({ value, onSelect }) => {
  const { availableRecommended, providerGroups, recommendedSet } = useModelGroups();

  // Substring filter instead of cmdk's default fuzzy match
  const filter = (value: string, search: string, keywords?: string[]) => {
    const haystack = [value, ...(keywords || [])].join(' ').toLowerCase();
    const terms = search.toLowerCase().split(/\s+/);
    return terms.every((term) => haystack.includes(term)) ? 1 : 0;
  };

  return (
    <Command className="rounded-lg" filter={filter}>
      <CommandInput placeholder="Search models..." />
      <CommandList className="max-h-[350px]">
        <CommandEmpty>No models found.</CommandEmpty>

        {availableRecommended.length > 0 && (
          <CommandGroup heading="Recommended">
            {availableRecommended.map((model) => (
              <CommandItem
                key={model.id}
                value={model.id}
                keywords={[model.provider, model.model]}
                onSelect={() => onSelect(model.id)}
              >
                <ModelItem
                  model={model}
                  isSelected={model.id === value}
                  isRecommended
                  showProvider
                />
              </CommandItem>
            ))}
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
            {providerModels.map((model) => (
              <CommandItem
                key={model.id}
                value={model.id}
                keywords={[model.provider, model.model]}
                onSelect={() => onSelect(model.id)}
              >
                <ModelItem
                  model={model}
                  isSelected={model.id === value}
                  isRecommended={recommendedSet.has(model.id)}
                  showProvider={false}
                />
              </CommandItem>
            ))}
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
