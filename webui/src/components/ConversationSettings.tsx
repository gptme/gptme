import { useState, type FC } from 'react';
import { DeleteConversationConfirmationDialog } from './DeleteConversationConfirmationDialog';
import { Trash, Loader2 } from 'lucide-react';
import { ModelSelector } from './ModelSelector';
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
import { EnvironmentVariables } from './settings/EnvironmentVariables';
import { ToolsConfiguration } from './settings/ToolsConfiguration';
import { McpConfiguration } from './settings/McpConfiguration';
import { useConversationSettings } from '@/hooks/useConversationSettings';
import { demoConversations } from '@/democonversations';

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
          <form onSubmit={handleSubmit(onSubmit)} className="flex h-full flex-col">
            <div className="flex-1 space-y-8 overflow-y-auto p-4 pb-12">
              <h3 className="mt-4 text-lg font-medium">Chat Settings</h3>

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
              <ModelSelector
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

              {/* Advanced/rare toggles */}
              <h3 className="text-lg font-medium">Advanced Settings</h3>
              <div className="space-y-2 rounded-lg border px-3 py-2 shadow-sm">
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
