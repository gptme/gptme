import { Loader2, Search, Star } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import {
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { ProviderIcon } from '@/components/ProviderIcon';
import { useModels } from '@/hooks/useModels';
import type { Control, FieldPath, FieldValues } from 'react-hook-form';

interface ModelSelectorProps<T extends FieldValues> {
  control?: Control<T>;
  name?: FieldPath<T>;
  value?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  label?: string;
  showFormField?: boolean;
}

export function ModelSelector<T extends FieldValues = FieldValues>({
  control,
  name,
  value,
  onValueChange,
  disabled = false,
  placeholder,
  label = 'Model',
  showFormField = true,
}: ModelSelectorProps<T>) {
  const { models, availableModels, isLoading, recommendedModels } = useModels();
  const [searchTerm, setSearchTerm] = useState('');
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [shouldFocus, setShouldFocus] = useState(false);

  // Filter models based on search term
  const filterModel = (modelFull: string) => {
    const modelInfo = models.find((m) => m.id === modelFull);
    const searchLower = searchTerm.toLowerCase();
    return (
      modelFull.toLowerCase().includes(searchLower) ||
      (modelInfo?.model && modelInfo.model.toLowerCase().includes(searchLower)) ||
      (modelInfo?.provider && modelInfo.provider.toLowerCase().includes(searchLower))
    );
  };

  // Get available recommended models (only those that exist in availableModels)
  const availableRecommendedModels = recommendedModels.filter((modelId) =>
    availableModels.includes(modelId)
  );

  // Filter recommended models based on search
  const filteredRecommendedModels = availableRecommendedModels.filter(filterModel);

  // Filter regular models (excluding recommended ones) based on search term
  const filteredRegularModels = availableModels
    .filter((modelFull) => !availableRecommendedModels.includes(modelFull))
    .filter(filterModel);

  // Ensure currently selected model is always available, even if it doesn't match the filter
  const ensureSelectedModelAvailable = (
    recommended: string[],
    regular: string[],
    selectedValue?: string
  ) => {
    if (!selectedValue || !availableModels.includes(selectedValue)) {
      return { recommended, regular, selectedModel: null };
    }

    // Check if selected model is already in one of the filtered lists
    if (recommended.includes(selectedValue) || regular.includes(selectedValue)) {
      return { recommended, regular, selectedModel: null };
    }

    // Selected model is not in filtered results, so we need to add it
    const selectedModel = selectedValue;

    // If it's a recommended model, add it to recommended list
    if (availableRecommendedModels.includes(selectedValue)) {
      return { recommended: [selectedValue, ...recommended], regular, selectedModel };
    } else {
      // Add it to regular models list
      return { recommended, regular: [selectedValue, ...regular], selectedModel };
    }
  };

  const {
    recommended: finalRecommendedModels,
    regular: finalRegularModels,
    selectedModel: currentSelectedModel,
  } = ensureSelectedModelAvailable(filteredRecommendedModels, filteredRegularModels, value);

  // Maintain focus on the search input after re-renders
  useEffect(() => {
    if (shouldFocus && searchInputRef.current) {
      searchInputRef.current.focus();
      setShouldFocus(false);
    }
  }, [shouldFocus, finalRecommendedModels, finalRegularModels]);

  const renderModelItem = (
    modelFull: string,
    isRecommended = false,
    isCurrentSelection = false,
    showCurrentBadge = true
  ) => {
    const modelInfo = models.find((m) => m.id === modelFull);
    return (
      <div className="flex flex-col">
        <div className="flex items-center gap-2">
          {modelInfo?.provider && <ProviderIcon provider={modelInfo.provider} />}
          <span className="font-medium">{modelInfo?.model || modelFull}</span>
          {isRecommended && <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />}
          {isCurrentSelection && showCurrentBadge && (
            <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800">Current</span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {modelInfo?.context && <span>{Math.round(modelInfo.context / 1000)}k ctx</span>}
          {modelInfo?.supports_vision && <span className="text-blue-600">👁️ vision</span>}
          {modelInfo?.supports_reasoning && <span className="text-green-600">🧠 reasoning</span>}
        </div>
      </div>
    );
  };

  const searchHeader = (
    <div className="p-2">
      <div className="relative">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          ref={searchInputRef}
          placeholder="Search models..."
          value={searchTerm}
          onChange={(e) => {
            setSearchTerm(e.target.value);
            setShouldFocus(true);
          }}
          className="pl-8"
          onKeyDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onFocus={() => setShouldFocus(false)}
        />
      </div>
    </div>
  );

  const selectContent = (
    <Select value={value} onValueChange={onValueChange} disabled={disabled || isLoading}>
      <SelectTrigger>
        <div className="flex w-full items-center justify-between">
          <SelectValue placeholder={placeholder || 'Select model'}>
            {value &&
              renderModelItem(value, availableRecommendedModels.includes(value), false, false)}
          </SelectValue>
          {isLoading && <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin" />}
        </div>
      </SelectTrigger>
      <SelectContent stickyHeader={searchHeader}>
        {/* Recommended models section */}
        {finalRecommendedModels.length > 0 && (
          <>
            <div className="px-2 py-1 text-xs font-medium text-muted-foreground">Recommended</div>
            {finalRecommendedModels.map((modelFull) => (
              <SelectItem key={modelFull} value={modelFull}>
                {renderModelItem(
                  modelFull,
                  availableRecommendedModels.includes(modelFull),
                  modelFull === currentSelectedModel
                )}
              </SelectItem>
            ))}
          </>
        )}

        {/* Regular models section */}
        {finalRegularModels.length > 0 && (
          <>
            {finalRecommendedModels.length > 0 && (
              <div className="mt-1 border-t px-2 py-1 pt-2 text-xs font-medium text-muted-foreground">
                Other models
              </div>
            )}
            {finalRegularModels.map((modelFull) => (
              <SelectItem key={modelFull} value={modelFull}>
                {renderModelItem(modelFull, false, modelFull === currentSelectedModel)}
              </SelectItem>
            ))}
          </>
        )}

        {/* No results message */}
        {finalRecommendedModels.length === 0 && finalRegularModels.length === 0 && searchTerm && (
          <div className="px-2 py-4 text-center text-sm text-muted-foreground">
            No models found matching "{searchTerm}"
          </div>
        )}
      </SelectContent>
    </Select>
  );

  if (!showFormField || !control || !name) {
    return selectContent;
  }

  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Select
              onValueChange={field.onChange}
              value={field.value ?? ''}
              disabled={disabled || isLoading}
            >
              <SelectTrigger>
                <div className="flex w-full items-center justify-between">
                  <SelectValue placeholder={placeholder || 'Select model'}>
                    {field.value &&
                      renderModelItem(
                        field.value,
                        availableRecommendedModels.includes(field.value),
                        false,
                        false
                      )}
                  </SelectValue>
                  {isLoading && <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin" />}
                </div>
              </SelectTrigger>
              <SelectContent stickyHeader={searchHeader}>
                {/* Recommended models section */}
                {finalRecommendedModels.length > 0 && (
                  <>
                    <div className="px-2 py-1 text-xs font-medium text-muted-foreground">
                      Recommended
                    </div>
                    {finalRecommendedModels.map((modelFull) => (
                      <SelectItem key={modelFull} value={modelFull}>
                        {renderModelItem(
                          modelFull,
                          availableRecommendedModels.includes(modelFull),
                          modelFull === currentSelectedModel
                        )}
                      </SelectItem>
                    ))}
                  </>
                )}

                {/* Regular models section */}
                {finalRegularModels.length > 0 && (
                  <>
                    {finalRecommendedModels.length > 0 && (
                      <div className="mt-1 border-t px-2 py-1 pt-2 text-xs font-medium text-muted-foreground">
                        Other models
                      </div>
                    )}
                    {finalRegularModels.map((modelFull) => (
                      <SelectItem key={modelFull} value={modelFull}>
                        {renderModelItem(modelFull, false, modelFull === currentSelectedModel)}
                      </SelectItem>
                    ))}
                  </>
                )}

                {/* No results message */}
                {finalRecommendedModels.length === 0 &&
                  finalRegularModels.length === 0 &&
                  searchTerm && (
                    <div className="px-2 py-4 text-center text-sm text-muted-foreground">
                      No models found matching "{searchTerm}"
                    </div>
                  )}
              </SelectContent>
            </Select>
          </FormControl>
          <FormDescription>The model to use.</FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
