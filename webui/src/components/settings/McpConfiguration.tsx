import { Button } from '@/components/ui/button';
import {
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ChevronDown, ChevronRight, Plus, X } from 'lucide-react';
import { useState } from 'react';
import { type UseFieldArrayReturn, type UseFormReturn } from 'react-hook-form';
import type { FormSchema } from '@/schemas/conversationSettings';
import { defaultMcpServer } from '@/schemas/conversationSettings';

interface McpConfigurationProps {
  form: UseFormReturn<FormSchema>;
  serverFields: UseFieldArrayReturn<FormSchema, 'mcp.servers'>;
  isSubmitting: boolean;
}

export const McpConfiguration = ({ form, serverFields, isSubmitting }: McpConfigurationProps) => {
  const [mcpOpen, setMcpOpen] = useState(false);
  const [newServerEnvInputs, setNewServerEnvInputs] = useState<
    Record<number, { key: string; value: string }>
  >({});
  const { fields, append, remove, update } = serverFields;

  const handleAddServer = () => {
    append(defaultMcpServer);
    setNewServerEnvInputs((prev) => ({ ...prev, [fields.length]: { key: '', value: '' } }));
  };

  const handleAddServerEnvVar = (serverIndex: number) => {
    const inputState = newServerEnvInputs[serverIndex];
    if (inputState && inputState.key.trim()) {
      const fieldArrayName = `mcp.servers.${serverIndex}.env` as const;
      const currentServerEnv = form.getValues(fieldArrayName) || [];
      update(serverIndex, {
        ...form.getValues(`mcp.servers.${serverIndex}`),
        env: [...currentServerEnv, { key: inputState.key.trim(), value: inputState.value }],
      });

      setNewServerEnvInputs((prev) => ({
        ...prev,
        [serverIndex]: { key: '', value: '' },
      }));
    }
  };

  const handleServerEnvInputChange = (
    serverIndex: number,
    field: 'key' | 'value',
    value: string
  ) => {
    setNewServerEnvInputs((prev) => ({
      ...prev,
      [serverIndex]: {
        ...(prev[serverIndex] || { key: '', value: '' }),
        [field]: value,
      },
    }));
  };

  const handleRemoveServerEnvVar = (serverIndex: number, envIndex: number) => {
    const fieldName = `mcp.servers.${serverIndex}.env` as const;
    const currentEnvVars = form.getValues(fieldName) || [];
    const newEnvVars = currentEnvVars.filter((_, idx) => idx !== envIndex);
    update(serverIndex, {
      ...form.getValues(`mcp.servers.${serverIndex}`),
      env: newEnvVars,
    });
  };

  return (
    <div className="space-y-6">
      <h3 className="text-lg font-medium">MCP Configuration</h3>

      <FormField
        control={form.control}
        name="mcp.enabled"
        render={({ field }) => (
          <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3 shadow-sm">
            <div className="space-y-0.5">
              <FormLabel>Enable MCP</FormLabel>
            </div>
            <FormControl>
              <Switch
                checked={field.value}
                onCheckedChange={field.onChange}
                disabled={isSubmitting}
              />
            </FormControl>
          </FormItem>
        )}
      />

      <FormField
        control={form.control}
        name="mcp.auto_start"
        render={({ field }) => (
          <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3 shadow-sm">
            <div className="space-y-0.5">
              <FormLabel>Auto-Start MCP Servers</FormLabel>
            </div>
            <FormControl>
              <Switch
                checked={field.value}
                onCheckedChange={field.onChange}
                disabled={isSubmitting}
              />
            </FormControl>
          </FormItem>
        )}
      />

      <Collapsible open={mcpOpen} onOpenChange={setMcpOpen}>
        <FormItem>
          <CollapsibleTrigger>
            <div className="flex w-full items-center justify-start">
              <FormLabel>MCP Servers</FormLabel>
              {mcpOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </div>
            <FormDescription className="mt-3">Add or remove MCP servers.</FormDescription>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="space-y-4">
              {fields.map((serverField, serverIndex) => (
                <div key={serverField.id} className="relative space-y-4 rounded-lg border p-4">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => remove(serverIndex)}
                    disabled={isSubmitting}
                    aria-label="Remove Server"
                    className="absolute right-2 top-2 h-6 w-6"
                  >
                    <X className="h-4 w-4" />
                  </Button>

                  <FormField
                    control={form.control}
                    name={`mcp.servers.${serverIndex}.name`}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Server Name</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g., my_api_server"
                            {...field}
                            disabled={isSubmitting}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name={`mcp.servers.${serverIndex}.enabled`}
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3 shadow-sm">
                        <div className="space-y-0.5">
                          <FormLabel>Enabled</FormLabel>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                            disabled={isSubmitting}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name={`mcp.servers.${serverIndex}.command`}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Command</FormLabel>
                        <FormControl>
                          <Input placeholder="e.g., python" {...field} disabled={isSubmitting} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name={`mcp.servers.${serverIndex}.args`}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Arguments (comma-separated)</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g., -m, my_module, --port, 8000"
                            {...field}
                            disabled={isSubmitting}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormItem>
                    <FormLabel>Server Environment Variables</FormLabel>
                    <div className="space-y-2 border-l-2">
                      {(form.getValues(`mcp.servers.${serverIndex}.env`) || []).map(
                        (_, envIndex) => (
                          <div
                            key={`${serverField.id}-env-${envIndex}`}
                            className="flex items-center space-x-2"
                          >
                            <Input
                              placeholder="Variable Name"
                              {...form.register(`mcp.servers.${serverIndex}.env.${envIndex}.key`)}
                              className="w-1/3"
                              disabled={isSubmitting}
                            />
                            <Input
                              placeholder="Value"
                              {...form.register(`mcp.servers.${serverIndex}.env.${envIndex}.value`)}
                              className="flex-grow"
                              disabled={isSubmitting}
                            />
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              onClick={() => handleRemoveServerEnvVar(serverIndex, envIndex)}
                              disabled={isSubmitting}
                              aria-label="Remove server variable"
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          </div>
                        )
                      )}
                    </div>
                    <div className="mt-2 flex items-center space-x-2">
                      <Input
                        placeholder="Key"
                        value={newServerEnvInputs[serverIndex]?.key || ''}
                        onChange={(e) =>
                          handleServerEnvInputChange(serverIndex, 'key', e.target.value)
                        }
                        disabled={isSubmitting}
                        className="w-1/3"
                      />
                      <Input
                        placeholder="Value"
                        value={newServerEnvInputs[serverIndex]?.value || ''}
                        onChange={(e) =>
                          handleServerEnvInputChange(serverIndex, 'value', e.target.value)
                        }
                        disabled={isSubmitting}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            handleAddServerEnvVar(serverIndex);
                          }
                        }}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => handleAddServerEnvVar(serverIndex)}
                        disabled={!newServerEnvInputs[serverIndex]?.key?.trim() || isSubmitting}
                        aria-label="Add server variable"
                      >
                        <Plus className="h-4 w-4" />
                      </Button>
                    </div>
                    {form.formState.errors.mcp?.servers?.[serverIndex]?.env && (
                      <FormMessage>Error in server environment variables.</FormMessage>
                    )}
                  </FormItem>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={handleAddServer}
              className="mt-4"
              disabled={isSubmitting}
            >
              Add MCP Server
            </Button>
            <FormDescription>Configure external processes managed by MCP.</FormDescription>
            {form.formState.errors.mcp?.servers && (
              <FormMessage>
                {form.formState.errors.mcp.servers.message ||
                  form.formState.errors.mcp.servers.root?.message}
              </FormMessage>
            )}
          </CollapsibleContent>
        </FormItem>
      </Collapsible>
    </div>
  );
};
