import { useState, useEffect, type FC } from 'react';
import { DeleteConversationConfirmationDialog } from './DeleteConversationConfirmationDialog';
import { Trash, Loader2, Download } from 'lucide-react';
import { conversations$ } from '@/stores/conversations';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
} from '@/utils/exportConversation';
import { toast } from 'sonner';
import { ModelPickerField } from './ModelPicker';
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormDescription,
  FormMessage,
} from '@/components/ui/form';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { EnvironmentVariables } from './settings/EnvironmentVariables';
import { ToolsConfiguration } from './settings/ToolsConfiguration';
import { McpConfiguration } from './settings/McpConfiguration';
import { useConversationSettings } from '@/hooks/useConversationSettings';
import { demoConversations } from '@/democonversations';
import { SessionCostSummary } from './SessionCostSummary';

// Decimal input that avoids mid-typing snapping (Number('0.') === 0 issue).
// Keeps a raw string in local state and only commits a number on blur.
function DecimalInput({
  field,
  min,
  max,
  placeholder,
  disabled,
}: {
  field: {
    value: number | undefined | null;
    onChange: (value: number | undefined) => void;
    onBlur: () => void;
    name: string;
  };
  min: number;
  max: number;
  placeholder: string;
  disabled: boolean;
}) {
  const [raw, setRaw] = useState<string>(field.value != null ? String(field.value) : '');

  // Sync raw when field.value changes externally (e.g. form reset), but not
  // while the user is mid-typing a partial like "0.".
  useEffect(() => {
    if (!raw.endsWith('.')) {
      setRaw(field.value != null ? String(field.value) : '');
    }
  }, [field.value]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Input
      name={field.name}
      type="text"
      inputMode="decimal"
      placeholder={placeholder}
      disabled={disabled}
      value={raw}
      onChange={(e) => {
        const v = e.target.value;
        if (v === '' || /^\d*\.?\d*$/.test(v)) {
          setRaw(v);
          if (v === '' || v === '.') {
            field.onChange(undefined);
          } else {
            const n = Number(v);
            if (!isNaN(n)) {
              field.onChange(Math.min(max, Math.max(min, n)));
            }
          }
        }
      }}
      onBlur={() => {
        if (raw !== '' && raw !== '.') {
          const n = Number(raw);
          if (!isNaN(n)) {
            const clamped = Math.min(max, Math.max(min, n));
            setRaw(String(clamped));
            field.onChange(clamped);
          } else {
            setRaw(field.value != null ? String(field.value) : '');
          }
        } else {
          setRaw('');
          field.onChange(undefined);
        }
        field.onBlur();
      }}
    />
  );
}

interface ConversationSettingsProps {
  conversationId: string;
}

export const ConversationSettings: FC<ConversationSettingsProps> = ({ conversationId }) => {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const {
    form,
    toolFields,
    envFields,
    serverFields,
    onSubmit,
    chatConfig,
    configError,
    isLoadingConfig,
  } = useConversationSettings(conversationId);

  const {
    handleSubmit,
    control,
    formState: { isDirty, isSubmitting },
  } = form;

  const selectedModel = form.watch('chat.model');
  const currentTemp = form.watch('chat.temperature');
  const showTempProviderWarning =
    currentTemp != null &&
    currentTemp > 1 &&
    /^(anthropic\/|claude-|google\/|gemini-)/.test(selectedModel ?? '');

  const onInvalid = (errors: Record<string, unknown>) => {
    // Surface validation errors so the user knows why save doesn't work
    const messages: string[] = [];
    const extractErrors = (obj: Record<string, unknown>, prefix = '') => {
      for (const [key, val] of Object.entries(obj)) {
        if (
          val &&
          typeof val === 'object' &&
          'message' in val &&
          typeof (val as { message: unknown }).message === 'string'
        ) {
          messages.push(`${prefix}${key}: ${(val as { message: string }).message}`);
        } else if (val && typeof val === 'object') {
          extractErrors(val as Record<string, unknown>, `${prefix}${key}.`);
        }
      }
    };
    extractErrors(errors);
    const errorMsg = messages.length > 0 ? messages.join(', ') : 'Please fix form errors';
    toast.error(errorMsg);
  };

  const isDemo = demoConversations.some((conv) => conv.id === conversationId);
  const isLoading = isLoadingConfig || (!chatConfig && !configError && !isDemo);

  return (
    <div className="flex h-full flex-col">
      {isLoading ? (
        <div className="flex h-full items-center justify-center p-8">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Loading configuration...</p>
          </div>
        </div>
      ) : configError ? (
        <div className="flex h-full items-center justify-center p-8">
          <div className="flex flex-col items-center gap-4 text-center">
            <div className="rounded-full bg-destructive/10 p-3">
              <Trash className="h-6 w-6 text-destructive" />
            </div>
            <div>
              <h3 className="font-medium text-destructive">Failed to Load Configuration</h3>
              <p className="mt-1 text-sm text-muted-foreground">{configError}</p>
            </div>
            <Button variant="outline" onClick={() => window.location.reload()} className="mt-2">
              Retry
            </Button>
          </div>
        </div>
      ) : isDemo ? (
        <div className="flex h-full items-center justify-center p-8">
          <div className="flex flex-col items-center gap-4 text-center">
            <div>
              <h3 className="font-medium">Read-Only Conversation</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Chat settings are not available for demo conversations. Create a new conversation to
                configure settings.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <Form {...form}>
          <form onSubmit={handleSubmit(onSubmit, onInvalid)} className="flex h-full flex-col">
            <div className="min-h-0 flex-1 space-y-8 overflow-y-auto p-4 pb-12">
              <h3 className="mt-4 text-lg font-medium">Chat Settings</h3>

              {/* Session Cost Summary */}
              <SessionCostSummary conversationId={conversationId} />

              {/* Conversation Name Field */}
              <FormField
                control={control}
                name="chat.name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Enter name..."
                        {...field}
                        value={field.value || ''}
                        disabled={isSubmitting}
                      />
                    </FormControl>
                    <FormDescription>A display name for this conversation.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Model Field */}
              <ModelPickerField
                control={control}
                name="chat.model"
                disabled={isSubmitting}
                placeholder="Select a model"
              />

              {/* Workspace Field */}
              <FormField
                control={control}
                name="chat.workspace"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Workspace</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="e.g., /path/to/project or ."
                        {...field}
                        value={field.value || ''}
                        disabled={isSubmitting}
                      />
                    </FormControl>
                    <FormDescription>Directory where the conversation takes place.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Environment Variables */}
              <EnvironmentVariables
                form={form}
                fieldArray={envFields}
                isSubmitting={isSubmitting}
                description="Environment variables available to the agent and tools."
              />

              {/* Tools Configuration */}
              <ToolsConfiguration form={form} toolFields={toolFields} isSubmitting={isSubmitting} />

              {/* MCP Configuration */}
              <McpConfiguration
                form={form}
                serverFields={serverFields}
                isSubmitting={isSubmitting}
              />

              {/* Sampling parameters */}
              <h3 className="text-lg font-medium">Sampling</h3>
              <div className="space-y-4 rounded-lg border px-3 py-3 shadow-sm">
                <FormField
                  control={control}
                  name="chat.temperature"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Temperature</FormLabel>
                      <FormControl>
                        <DecimalInput
                          field={field}
                          min={0}
                          max={2}
                          placeholder="Model default"
                          disabled={isSubmitting}
                        />
                      </FormControl>
                      <FormDescription>
                        Sampling temperature. 0–2 (OpenAI) · 0–1 (Anthropic/Gemini). Empty = model
                        default.
                        {showTempProviderWarning && (
                          <span className="block text-amber-500 dark:text-amber-400">
                            ⚠ Temperature &gt; 1 may be rejected by this provider.
                          </span>
                        )}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={control}
                  name="chat.top_p"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Top P</FormLabel>
                      <FormControl>
                        <DecimalInput
                          field={field}
                          min={0}
                          max={1}
                          placeholder="Model default"
                          disabled={isSubmitting}
                        />
                      </FormControl>
                      <FormDescription>
                        Nucleus sampling probability mass (0–1). Empty = model default.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={control}
                  name="chat.max_tokens"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Max Tokens</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          min={1}
                          step={1}
                          placeholder="Model default"
                          value={field.value ?? ''}
                          onChange={(e) => {
                            const v = e.target.value;
                            const n = Math.round(Number(v));
                            field.onChange(v === '' || isNaN(n) ? undefined : Math.max(1, n));
                          }}
                          disabled={isSubmitting}
                        />
                      </FormControl>
                      <FormDescription>
                        Maximum tokens in the model&apos;s response. Empty = provider/model default.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              {/* Advanced/rare toggles */}
              <h3 className="text-lg font-medium">Advanced Settings</h3>
              <div className="space-y-2 rounded-lg border px-3 py-2 shadow-sm">
                <FormField
                  control={control}
                  name="chat.system_prompt"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>System Prompt Override</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder="Optional conversation-only system prompt override..."
                          {...field}
                          value={field.value || ''}
                          disabled={isSubmitting}
                          rows={8}
                        />
                      </FormControl>
                      <FormDescription>
                        Appended as an extra system message for this conversation only. It does not
                        edit global config, profiles, or agent bootstrap files.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Stream Field */}
                <FormField
                  control={control}
                  name="chat.stream"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between">
                      <div className="space-y-0.5">
                        <FormLabel>Stream Response</FormLabel>
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

                {/* Interactive Field */}
                <FormField
                  control={control}
                  name="chat.interactive"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between">
                      <div className="space-y-0.5">
                        <FormLabel>Interactive Mode</FormLabel>
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
              </div>

              {/* Export */}
              <div className="mt-8 space-y-4">
                <h3 className="text-lg font-medium">Export</h3>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const conv = conversations$.get(conversationId)?.get();
                      if (!conv?.data?.log?.length) {
                        toast.error('No messages to export');
                        return;
                      }

                      const exportableMessages = getExportableMessages(conv.data.log);
                      if (!exportableMessages.length) {
                        toast.error('No visible messages to export');
                        return;
                      }

                      exportConversationAsMarkdown(
                        conversationId,
                        conv.data.name || conversationId,
                        exportableMessages
                      );
                      toast.success('Exported as Markdown');
                    }}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    Markdown
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const conv = conversations$.get(conversationId)?.get();
                      if (!conv?.data?.log?.length) {
                        toast.error('No messages to export');
                        return;
                      }
                      exportConversationAsJSON(
                        conversationId,
                        conv.data.name || conversationId,
                        conv.data.log
                      );
                      toast.success('Exported as JSON');
                    }}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    JSON
                  </Button>
                </div>
              </div>

              {/* Danger Zone */}
              <div className="mt-8 space-y-6">
                <h3 className="text-lg font-medium text-destructive">Danger Zone</h3>
                <div className="rounded-lg border-2 border-destructive/20 p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h4 className="font-medium">Delete Conversation</h4>
                      <p className="text-sm text-muted-foreground">
                        Permanently delete this conversation and all its messages.
                      </p>
                    </div>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => setDeleteDialogOpen(true)}
                      className="shrink-0"
                    >
                      <Trash className="mr-2 h-4 w-4" />
                      Delete
                    </Button>
                  </div>
                </div>
              </div>
            </div>

            {/* Submit Button */}
            <div className="flex-shrink-0 border-t bg-background p-4">
              <Button
                type="submit"
                disabled={!isDirty || isSubmitting}
                variant={isDirty ? 'default' : 'secondary'}
                className="w-full"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Saving...
                  </>
                ) : isDirty ? (
                  'Save Changes'
                ) : (
                  'Everything saved'
                )}
              </Button>
            </div>

            {/* Delete Dialog */}
            <DeleteConversationConfirmationDialog
              conversationName={conversationId}
              open={deleteDialogOpen}
              onOpenChange={setDeleteDialogOpen}
              onDelete={() => {
                window.location.href = '/';
              }}
            />
          </form>
        </Form>
      )}
    </div>
  );
};
