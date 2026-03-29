import { Star, Check } from 'lucide-react';
import { useMemo, type FC } from 'react';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { ProviderIcon } from '@/components/ProviderIcon';
import { useModels, type ModelInfo } from '@/hooks/useModels';

interface ModelPickerProps {
  value?: string;
  onSelect: (modelId: string) => void;
}

const ModelItem: FC<{
  model: ModelInfo;
  isSelected: boolean;
  isRecommended: boolean;
  showProvider: boolean;
}> = ({ model, isSelected, isRecommended, showProvider }) => (
  <div className="flex w-full items-center justify-between gap-2">
    <div className="flex min-w-0 flex-col">
      <div className="flex items-center gap-2">
        {showProvider && <ProviderIcon provider={model.provider} />}
        <span className="truncate">{model.model}</span>
        {isRecommended && <Star className="h-3 w-3 flex-shrink-0 fill-yellow-400 text-yellow-400" />}
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

export const ModelPicker: FC<ModelPickerProps> = ({ value, onSelect }) => {
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

  return (
    <Command className="rounded-lg">
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
                <ProviderIcon provider={provider} size={12} />
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
                  isRecommended={false}
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
