import { Button } from '@/components/ui/button';
import { useState, useEffect, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '@/contexts/ApiContext';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { use$ } from '@legendapp/state/react';
import { observable } from '@legendapp/state';
import { ChatInput, type ChatOptions } from '@/components/ChatInput';
import { History, Server } from 'lucide-react';
import { useSettings } from '@/contexts/SettingsContext';
import { ExamplesSection } from '@/components/ExamplesSection';
import { serverRegistry$, getConnectedServers } from '@/stores/servers';
import { getExamples } from '@/utils/examples';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export const WelcomeView = () => {
  const [inputValue, setInputValue] = useState(
    () => (typeof window !== 'undefined' ? localStorage.getItem('gptme-draft-new') : null) || ''
  );
  // Persist new-chat draft to localStorage
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (inputValue) {
      localStorage.setItem('gptme-draft-new', inputValue);
    } else {
      localStorage.removeItem('gptme-draft-new');
    }
  }, [inputValue]);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const navigate = useNavigate();
  const { api, isConnected$, connectionConfig, switchServer } = useApi();
  const queryClient = useQueryClient();
  const isConnected = use$(isConnected$);
  const registry = use$(serverRegistry$);
  const connectedServers = getConnectedServers();
  const activeServer = registry.servers.find((s) => s.id === registry.activeServerId);
  const showServerPicker = connectedServers.length > 1;
  const quickSuggestions = getExamples('welcome-suggestions', 'mixed', 4);

  // Create observables that ChatInput expects
  const autoFocus$ = observable(true);

  const handleServerSwitch = async (serverId: string) => {
    try {
      await switchServer(serverId);
    } catch {
      const server = registry.servers.find((s) => s.id === serverId);
      toast.error(`Failed to switch to "${server?.name || 'server'}"`);
    }
  };

  const handleSend = async (message: string, options?: ChatOptions) => {
    if ((!message.trim() && !options?.pendingFiles?.length) || !isConnected) return;

    setIsSubmitting(true);

    try {
      // Create conversation with immediate placeholder and get ID
      const conversationId = await api.createConversationWithPlaceholder(message, {
        model: options?.model,
        stream: options?.stream,
        workspace: options?.workspace || '.',
        pendingFiles: options?.pendingFiles,
      });

      // Navigate immediately - server-side creation happens in background
      // Errors from backend are handled via toast in api.ts
      navigate(`/chat/${conversationId}`);

      // Invalidate conversations query to refresh the list (async, don't block)
      queryClient.invalidateQueries({
        queryKey: ['conversations', connectionConfig.baseUrl, isConnected$.get()],
      });
    } catch (error) {
      // This only catches synchronous errors (e.g., local state issues)
      // Server-side errors are handled in api.ts with toast notifications
      console.error('Failed to create conversation:', error);
      toast.error('Failed to start conversation. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const { settings } = useSettings();
  const bg = settings.welcomeBackground;
  // Determine if the background is an image URL or a CSS gradient/color
  const isImageBg = bg && (bg.startsWith('http') || bg.startsWith('/') || bg.startsWith('data:'));
  const bgStyle: CSSProperties = bg
    ? isImageBg
      ? { backgroundImage: `url("${bg}")`, backgroundSize: 'cover', backgroundPosition: 'center' }
      : { background: bg }
    : {};
  const hasCustomBg = !!bg;

  return (
    <div className="mx-auto flex h-full w-full flex-col" style={bgStyle}>
      <div className="mx-auto flex h-full w-full max-w-5xl flex-col items-center justify-center px-4 pt-12 sm:px-6">
        <div
          className={`w-full max-w-4xl rounded-[28px] border p-6 shadow-[0_30px_120px_-48px_rgba(15,23,42,0.45)] sm:p-8 ${
            hasCustomBg
              ? 'border-white/20 bg-background/60 backdrop-blur-xl'
              : 'border-border/70 bg-background/90 backdrop-blur'
          }`}
        >
          <div className="flex flex-col gap-8">
            <div className="space-y-4 text-center">
              <div className="space-y-3">
                <h1 className="text-3xl font-semibold tracking-tight text-foreground/90 sm:text-5xl">
                  What are you working on?
                </h1>
                <p className="mx-auto max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
                  Start with a real task, question, or rough idea. gptme is best when you give it
                  something concrete to work on.
                </p>
              </div>
            </div>

            <div className="mx-auto w-full max-w-2xl [&_textarea]:min-h-[80px] [&_textarea]:text-base">
              <ChatInput
                onSend={handleSend}
                autoFocus$={autoFocus$}
                value={inputValue}
                onChange={setInputValue}
              />
            </div>

            <div className="space-y-3">
              <p className="text-center text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
                Try one of these
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {quickSuggestions.map((suggestion) => (
                  <Button
                    key={suggestion}
                    type="button"
                    variant="outline"
                    size="sm"
                    className="rounded-full border-border/70 bg-background/70 text-xs text-muted-foreground hover:bg-background hover:text-foreground"
                    onClick={() => setInputValue(suggestion)}
                    disabled={isSubmitting}
                  >
                    {suggestion}
                  </Button>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-center gap-3">
              {showServerPicker && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="rounded-full border border-transparent text-muted-foreground hover:border-border/70 hover:bg-background/70"
                    >
                      <Server className="mr-2 h-4 w-4" />
                      {activeServer?.name || 'Server'}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    {connectedServers.map((server) => (
                      <DropdownMenuItem
                        key={server.id}
                        onClick={() => handleServerSwitch(server.id)}
                      >
                        <span
                          className={server.id === registry.activeServerId ? 'font-medium' : ''}
                        >
                          {server.name}
                        </span>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => navigate('/history')}
                className="rounded-full border border-transparent text-muted-foreground hover:border-border/70 hover:bg-background/70"
              >
                <History className="mr-2 h-4 w-4" />
                Show history
              </Button>
              <ExamplesSection onExampleSelect={setInputValue} disabled={isSubmitting} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
