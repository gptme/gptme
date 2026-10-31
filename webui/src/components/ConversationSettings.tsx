import { useCallback, useState, useEffect, type FC } from 'react';
import { DeleteConversationConfirmationDialog } from './DeleteConversationConfirmationDialog';
import { Trash, Loader2, Download, Copy, Check } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { conversations$ } from '@/stores/conversations';
import { serverRegistry$ } from '@/stores/servers';
import { getClientGeneration, type IApiClient } from '@/utils/api';
import type { Message } from '@/types/conversation';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  copyConversationToClipboard,
  getExportableMessages,
  reportDownloadOutcome,
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
  /** Server that owns this conversation; defaults to the primary client. */
  serverId?: string;
}

export const ConversationSettings: FC<ConversationSettingsProps> = ({
  conversationId,
  serverId,
}) => {
  const { getClient } = useApi();
  const isDemo = demoConversations.some((conv) => conv.id === conversationId);

  // Resolve the owner at click time, not render time: the registry can lose a
  // server while this panel is open. getClient() falls back to the primary for
  // unknown IDs, so the registry check is what makes a removed owner fail closed
  // instead of silently reading a same-ID conversation from another server.
  const resolveExportClient = useCallback((): IApiClient | null => {
    if (serverId && !serverRegistry$.get().servers.some((server) => server.id === serverId)) {
      toast.error('Cannot export: conversation server is unavailable');
      return null;
    }
    return getClient(serverId);
  }, [getClient, serverId]);

  /**
   * Snapshot the selected branch before fetching and replace a paginated window
   * with the complete owner log. A named branch resolved from the loaded cache
   * must come from the fresh branch map; never fall back to main or a cached
   * partial window.
   */
  const loadExportLog = useCallback(
    async (
      loadedLog: Message[],
      hasMoreBefore: boolean,
      client: IApiClient
    ): Promise<{ messages: Message[]; name: string } | null> => {
      const loadedName = conversations$.get(conversationId)?.data?.name?.peek() || conversationId;
      if (isDemo) return { messages: loadedLog, name: loadedName };

      const storeConv = conversations$.get(conversationId);
      const cachedGeneration = storeConv?.streamClientGeneration?.peek();
      const cachedOwnerId = storeConv?.serverId?.peek();
      const ownerId = serverId ?? serverRegistry$.peek().activeServerId;
      const sameClient =
        typeof cachedGeneration === 'number' && cachedGeneration === getClientGeneration(client);
      // The selected branch belongs to the server, not a particular token's
      // client instance. Prefer stable ownership; generations cover legacy entries.
      const cacheBelongsToOwner =
        cachedOwnerId !== undefined ? cachedOwnerId === ownerId : sameClient;
      // Completeness is not ownership: the ID-keyed cache can still contain
      // another server's full transcript while the new owner's fetch is pending.
      if (!hasMoreBefore && cacheBelongsToOwner && sameClient) {
        return { messages: loadedLog, name: loadedName };
      }
      const selectedBranch = cacheBelongsToOwner
        ? (storeConv?.currentBranch?.peek() ?? 'main')
        : 'main';

      try {
        const full = await client.getConversation(conversationId);
        const branchLog = selectedBranch !== 'main' ? full.branches?.[selectedBranch] : undefined;
        if (selectedBranch !== 'main' && !branchLog) {
          toast.error(`Cannot export: branch "${selectedBranch}" is unavailable`);
          return null;
        }
        return {
          messages: branchLog ?? full.log,
          name: full.name || (cacheBelongsToOwner ? loadedName : conversationId),
        };
      } catch {
        toast.error('Failed to load full conversation history');
        return null;
      }
    },
    [conversationId, isDemo, serverId]
  );

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [includeThinking, setIncludeThinking] = useState(false);
  const [includeTools, setIncludeTools] = useState(true);
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

                {/* Detail level toggles */}
                <div className="space-y-2 rounded-lg border px-3 py-2 shadow-sm">
                  <p className="text-sm font-medium text-muted-foreground">Detail level</p>
                  <div className="flex items-center justify-between">
                    <span className="text-sm">Include thinking blocks</span>
                    <Switch checked={includeThinking} onCheckedChange={setIncludeThinking} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm">Include tool calls &amp; results</span>
                    <Switch checked={includeTools} onCheckedChange={setIncludeTools} />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Filters drop real assistant thinking and tool syntax. Fenced or inline-code
                    examples are kept. A top-level think tag or tool fence is treated as real. This
                    is not secret redaction.
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  {/* Copy to clipboard */}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const conv = conversations$.get(conversationId)?.get();
                      const client = resolveExportClient();
                      if (!client) return;
                      if (!conv?.data?.log?.length) {
                        toast.error('No messages to copy');
                        return;
                      }
                      // The store may only hold the currently-loaded window of a
                      // long conversation (virtualized pagination). Fetch the
                      // complete owner history rather than silently copying a
                      // partial slice.
                      const fullLog = await loadExportLog(
                        conv.data.log,
                        conv.hasMoreBefore,
                        client
                      );
                      if (!fullLog) return;
                      const exportOptions = { includeThinking, includeTools };
                      const exportableMessages = getExportableMessages(
                        fullLog.messages,
                        exportOptions
                      );
                      if (!exportableMessages.length) {
                        toast.error('No visible messages to copy');
                        return;
                      }
                      try {
                        await copyConversationToClipboard(
                          fullLog.name,
                          exportableMessages,
                          exportOptions
                        );
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                      } catch {
                        toast.error('Failed to copy to clipboard');
                      }
                    }}
                  >
                    {copied ? (
                      <Check className="mr-2 h-4 w-4 text-green-500" />
                    ) : (
                      <Copy className="mr-2 h-4 w-4" />
                    )}
                    {copied ? 'Copied!' : 'Copy'}
                  </Button>

                  {/* Download Markdown */}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const conv = conversations$.get(conversationId)?.get();
                      const client = resolveExportClient();
                      if (!client) return;
                      if (!conv?.data?.log?.length) {
                        toast.error('No messages to export');
                        return;
                      }

                      // See the Copy handler above: don't silently export a
                      // partial window of a long, paginated conversation.
                      const fullLog = await loadExportLog(
                        conv.data.log,
                        conv.hasMoreBefore,
                        client
                      );
                      if (!fullLog) return;

                      const exportOptions = { includeThinking, includeTools };
                      const exportableMessages = getExportableMessages(
                        fullLog.messages,
                        exportOptions
                      );
                      if (!exportableMessages.length) {
                        toast.error('No visible messages to export');
                        return;
                      }

                      reportDownloadOutcome(
                        exportConversationAsMarkdown(
                          conversationId,
                          fullLog.name,
                          exportableMessages,
                          exportOptions
                        ),
                        'Downloaded as Markdown'
                      );
                    }}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    Download .md
                  </Button>

                  {/* Download JSON */}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const conv = conversations$.get(conversationId)?.get();
                      const client = resolveExportClient();
                      if (!client) return;
                      if (!conv?.data?.log?.length) {
                        toast.error('No messages to export');
                        return;
                      }

                      // See the Copy handler above: don't silently export a
                      // partial window of a long, paginated conversation.
                      const fullLog = await loadExportLog(
                        conv.data.log,
                        conv.hasMoreBefore,
                        client
                      );
                      if (!fullLog) return;

                      reportDownloadOutcome(
                        exportConversationAsJSON(conversationId, fullLog.name, fullLog.messages),
                        'Downloaded as JSON'
                      );
                    }}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    Download .json
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Note: tool output is included verbatim and may contain sensitive data.
                </p>
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
              onDelete={() => setDeleteDialogOpen(false)}
            />
          </form>
        </Form>
      )}
    </div>
  );
};
