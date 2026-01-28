import { Button } from '@/components/ui/button';
import { FormDescription, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Plus, X } from 'lucide-react';
import { useState } from 'react';
import { type UseFieldArrayReturn, type UseFormReturn } from 'react-hook-form';
import type { FormSchema } from '@/schemas/conversationSettings';

interface EnvironmentVariablesProps {
  form: UseFormReturn<FormSchema>;
  fieldArray: UseFieldArrayReturn<FormSchema, 'chat.env'>;
  isSubmitting: boolean;
  description?: string;
  className?: string;
}

export const EnvironmentVariables = ({
  form,
  fieldArray,
  isSubmitting,
  description,
  className,
}: EnvironmentVariablesProps) => {
  const [newEnvKey, setNewEnvKey] = useState('');
  const [newEnvValue, setNewEnvValue] = useState('');
  const { fields, append, remove } = fieldArray;

  const handleAddEnvVar = () => {
    const trimmedKey = newEnvKey.trim();
    if (trimmedKey) {
      append({ key: trimmedKey, value: newEnvValue });
      setNewEnvKey('');
      setNewEnvValue('');
    }
  };

  return (
    <FormItem className={className}>
      <FormLabel>Environment Variables</FormLabel>
      <div className="space-y-2">
        {fields.map((field, index) => (
          <div key={field.id} className="flex items-center space-x-2">
            <Input
              placeholder="Key"
              {...form.register(`chat.env.${index}.key`)}
              className="w-1/3"
              disabled={isSubmitting}
            />
            <Input
              placeholder="Value"
              {...form.register(`chat.env.${index}.value`)}
              className="flex-grow"
              disabled={isSubmitting}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => remove(index)}
              disabled={isSubmitting}
              aria-label="Remove variable"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center space-x-2">
        <Input
          placeholder="Key"
          value={newEnvKey}
          onChange={(e) => setNewEnvKey(e.target.value)}
          disabled={isSubmitting}
          className="w-1/3"
        />
        <Input
          placeholder="Value"
          value={newEnvValue}
          onChange={(e) => setNewEnvValue(e.target.value)}
          disabled={isSubmitting}
          className="flex-grow"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              handleAddEnvVar();
            }
          }}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={handleAddEnvVar}
          disabled={!newEnvKey.trim() || isSubmitting}
          aria-label="Add variable"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      {description && <FormDescription>{description}</FormDescription>}
      {form.formState.errors.chat?.env && (
        <FormMessage>{form.formState.errors.chat.env.message}</FormMessage>
      )}
    </FormItem>
  );
};
