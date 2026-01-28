import { type FC, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Bot, GitBranch, FolderOpen, Settings, ChevronDown, ChevronRight } from 'lucide-react';
import { useForm } from 'react-hook-form';
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormDescription,
  FormMessage,
} from '@/components/ui/form';
import { toast } from 'sonner';
import { ApiClientError } from '@/utils/api';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { selectedAgent$ } from '@/stores/sidebar';

export interface CreateAgentRequest {
  name: string;
  template_repo: string;
  template_branch: string;
  path?: string; // Now optional - auto-generated if not provided
  fork_command: string;
  project_config?: Record<string, unknown>;
}

export interface CreateAgentResponse {
  status: string;
  message: string;
  initial_conversation_id: string;
  agent_path: string;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAgentCreated: (agent: CreateAgentRequest) => Promise<CreateAgentResponse>;
}

const CreateAgentDialog: FC<Props> = ({ open, onOpenChange, onAgentCreated }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const form = useForm<CreateAgentRequest>({
    defaultValues: {
      name: '',
      path: '',
      template_repo: 'https://github.com/gptme/gptme-agent-template',
      template_branch: 'master',
      fork_command: '',
    },
  });

  const handleSubmit = async (data: CreateAgentRequest) => {
    if (!data.name.trim() || !data.template_repo.trim() || !data.template_branch.trim()) {
      return;
    }

    // Generate fork_command if not explicitly set
    const processedData = { ...data };
    if (!processedData.fork_command.trim()) {
      processedData.fork_command = `./fork.sh ${processedData.path} ${processedData.name}`;
    }

    setIsLoading(true);
    try {
      const response = await onAgentCreated(processedData);

      // Reset form
      form.reset();
      onOpenChange(false);

      toast.success('Agent created successfully!');

      // Refresh conversations data to include the new conversation
      await queryClient.invalidateQueries({ queryKey: ['conversations'] });

      // Set selected agent
      selectedAgent$.set({
        name: data.name,
        path: response.agent_path,
        description: `Agent: ${data.name}`,
        conversationCount: 0,
        lastUsed: new Date().toISOString(),
      });

      // Navigate and force a refresh if needed
      const conversationId = response.initial_conversation_id;
      navigate(`/chat/${conversationId}`);
    } catch (error) {
      console.error('Error creating agent:', error);

      let errorMessage = 'Failed to create agent. Please try again.';

      if (ApiClientError.isApiError(error)) {
        // Extract the actual error message from the server response
        errorMessage = `Failed to create agent: ${error.message}`;
      } else if (error instanceof Error) {
        errorMessage = `Failed to create agent: ${error.message}`;
      }

      toast.error(errorMessage, {
        duration: 10000, // Show error longer so user can read it
        style: {
          maxWidth: '500px',
        },
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5" />
            Create New Agent
          </DialogTitle>
          <DialogDescription>
            Set up a new agent by forking a template repository.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
            {/* Agent Name */}
            <FormField
              control={form.control}
              name="name"
              rules={{ required: 'Agent name is required' }}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Agent Name *</FormLabel>
                  <FormControl>
                    <Input placeholder="e.g. Alice, Bob, Charlie" {...field} disabled={isLoading} />
                  </FormControl>
                  <FormDescription>The name of your agent</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Advanced Options */}
            <Collapsible open={showAdvanced} onOpenChange={setShowAdvanced}>
              <CollapsibleTrigger asChild>
                <Button
                  variant="ghost"
                  className="flex w-full items-center justify-between p-0 hover:bg-transparent"
                  type="button"
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <Settings className="h-4 w-4" />
                    Advanced Options
                  </span>
                  {showAdvanced ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-4">
                {/* Workspace Settings */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <FolderOpen className="h-4 w-4" />
                      Workspace Configuration
                    </CardTitle>
                    <CardDescription>
                      Configure the agent's workspace directory (auto-generated if not specified)
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <FormField
                      control={form.control}
                      name="path"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Workspace Path (Optional)</FormLabel>
                          <FormControl>
                            <Input
                              placeholder="Auto-generated from agent name if not specified"
                              {...field}
                              disabled={isLoading}
                            />
                          </FormControl>
                          <FormDescription>
                            The directory where the agent will store itself. If not specified, will
                            be auto-generated from the current directory and agent name.
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </CardContent>
                </Card>

                {/* Template Repository Settings */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <GitBranch className="h-4 w-4" />
                      Template Repository
                    </CardTitle>
                    <CardDescription>
                      Configure the repository template to fork for this agent
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <FormField
                      control={form.control}
                      name="template_repo"
                      rules={{ required: 'Template repository is required' }}
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Repository URL *</FormLabel>
                          <FormControl>
                            <Input
                              placeholder="https://github.com/username/template-repo"
                              {...field}
                              disabled={isLoading}
                            />
                          </FormControl>
                          <FormDescription>
                            The git repository URL to clone as the agent template
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="template_branch"
                      rules={{ required: 'Template branch is required' }}
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Branch *</FormLabel>
                          <FormControl>
                            <Input placeholder="main" {...field} disabled={isLoading} />
                          </FormControl>
                          <FormDescription>
                            The branch to clone from the template repository
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </CardContent>
                </Card>

                {/* Fork Command */}
                <Card>
                  <CardHeader>
                    <CardTitle>Setup Command</CardTitle>
                    <CardDescription>Configuration for post-setup commands</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <FormField
                      control={form.control}
                      name="fork_command"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Fork Command</FormLabel>
                          <FormControl>
                            <Textarea
                              placeholder="./fork.sh <workspace-path> <agent-name>"
                              {...field}
                              disabled={isLoading}
                              rows={3}
                            />
                          </FormControl>
                          <FormDescription>
                            Command to run after cloning the template. Leave empty to use the
                            default command. This should execute a script that properly copies over
                            the template files to the agent's workspace.
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </CardContent>
                </Card>
              </CollapsibleContent>
            </Collapsible>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? 'Creating...' : 'Create Agent'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
};

export default CreateAgentDialog;
