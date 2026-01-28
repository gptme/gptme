import { Button } from '@/components/ui/button';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '@/contexts/ApiContext';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { use$ } from '@legendapp/state/react';
import { observable } from '@legendapp/state';
import { ChatInput, type ChatOptions } from '@/components/ChatInput';
import { History } from 'lucide-react';
import { ExamplesSection } from '@/components/ExamplesSection';

export const WelcomeView = ({ onToggleHistory }: { onToggleHistory: () => void }) => {
  const [inputValue, setInputValue] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const navigate = useNavigate();
  const { api, isConnected$, connectionConfig } = useApi();
  const queryClient = useQueryClient();
  const isConnected = use$(isConnected$);

  // Create observables that ChatInput expects
  const autoFocus$ = observable(true);

  const handleSend = async (message: string, options?: ChatOptions) => {
    if (!message.trim() || !isConnected) return;

    setIsSubmitting(true);

    try {
      // Create conversation with immediate placeholder and get ID
      const conversationId = await api.createConversationWithPlaceholder(message, {
        model: options?.model,
        stream: options?.stream,
        workspace: options?.workspace || '.',
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

  return (
    <div className="mx-auto flex h-full w-full flex-col items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-bold">How can I help you today?</h1>
        <p className="mt-2 text-muted-foreground">
          I can help you write code, debug issues, and learn new concepts.
        </p>
      </div>

      <div className="my-4 w-full max-w-xl px-4">
        <ChatInput
          onSend={handleSend}
          autoFocus$={autoFocus$}
          value={inputValue}
          onChange={setInputValue}
        />
      </div>

      <div>
        <div className="flex justify-center space-x-4">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onToggleHistory}
            className="text-muted-foreground"
          >
            <History className="mr-2 h-4 w-4" />
            Show history
          </Button>
          <ExamplesSection onExampleSelect={setInputValue} disabled={isSubmitting} />
        </div>
      </div>
    </div>
  );
};
