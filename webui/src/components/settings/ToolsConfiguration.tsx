import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { X, Plus } from 'lucide-react';
import { useState } from 'react';
import { type UseFieldArrayReturn, type UseFormReturn } from 'react-hook-form';
import type { FormSchema } from '@/schemas/conversationSettings';
import { ToolFormat } from '@/types/api';

interface ToolsConfigurationProps {
  form: UseFormReturn<FormSchema>;
  toolFields: UseFieldArrayReturn<FormSchema, 'chat.tools'>;
  isSubmitting: boolean;
}

export const ToolsConfiguration = ({ form, toolFields, isSubmitting }: ToolsConfigurationProps) => {
  const [newToolName, setNewToolName] = useState('');
  const { fields, append, remove } = toolFields;

  const basicTools = ['shell', 'ipython', 'browser', 'save', 'append', 'gh', 'chats', 'patch'];

  const currentToolNames = fields.map((field) => field.name);
  const availableBasicTools = basicTools.filter((tool) => !currentToolNames.includes(tool));

  const handleAddTool = (toolName?: string) => {
    const name = toolName || newToolName.trim();
    if (name && !currentToolNames.includes(name)) {
      append({ name });
      setNewToolName('');
    }
  };

  return (
    <div className="space-y-6">
      <h3 className="text-lg font-medium">Tools</h3>

      {/* Tools listing */}
      <div className="space-y-4">
        <FormDescription>Tools that the agent can use.</FormDescription>

        {/* Current tools as badges */}
        {fields.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {fields.map((field, index) => (
              <Badge key={field.id} variant="secondary" className="pr-1">
                <span className="mr-1">{field.name}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-4 w-4 p-0 hover:bg-destructive hover:text-destructive-foreground"
                  onClick={() => remove(index)}
                  disabled={isSubmitting}
                  aria-label={`Remove ${field.name} tool`}
                >
                  <X className="h-3 w-3" />
                </Button>
              </Badge>
            ))}
          </div>
        )}

        {/* Quick add buttons for basic tools */}
        {availableBasicTools.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">Quick add:</p>
            <div className="flex flex-wrap gap-2">
              {availableBasicTools.map((tool) => (
                <Button
                  key={tool}
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => handleAddTool(tool)}
                  disabled={isSubmitting}
                  className="h-7 text-xs"
                >
                  <Plus className="mr-1 h-3 w-3" />
                  {tool}
                </Button>
              ))}
            </div>
          </div>
        )}

        {/* Custom tool input */}
        <div className="flex items-center space-x-2">
          <Input
            placeholder="Custom tool name"
            value={newToolName}
            onChange={(e) => setNewToolName(e.target.value)}
            disabled={isSubmitting}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleAddTool();
              }
            }}
            className="flex-1"
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => handleAddTool()}
            disabled={
              !newToolName.trim() || isSubmitting || currentToolNames.includes(newToolName.trim())
            }
          >
            Add
          </Button>
        </div>
      </div>

      <FormField
        control={form.control}
        name="chat.tool_format"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Tool Format</FormLabel>
            <Select
              onValueChange={(value) => field.onChange(value)}
              value={field.value ?? ''}
              disabled={isSubmitting}
            >
              <FormControl>
                <SelectTrigger>
                  <SelectValue placeholder="Select tool format" />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {Object.values(ToolFormat).map((format) => (
                  <SelectItem key={format} value={format}>
                    {format}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  );
};
