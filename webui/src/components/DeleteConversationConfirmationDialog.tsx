import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { conversations$, selectedConversation$ } from '@/stores/conversations';
import { useQueryClient } from '@tanstack/react-query';
import { use$ } from '@legendapp/state/react';
import { demoConversations } from '@/democonversations';

interface Props {
  conversationName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDelete: () => void;
}

export function DeleteConversationConfirmationDialog({
  conversationName,
  open,
  onOpenChange,
  onDelete,
}: Props) {
  const { deleteConversation, connectionConfig, isConnected$ } = useApi();
  const queryClient = useQueryClient();
  const isConnected = use$(isConnected$);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleDelete = async () => {
    // Show loading indicator
    setIsDeleting(true);

    // Delete conversation
    try {
      await deleteConversation(conversationName);
    } catch (error) {
      setIsError(true);
      if (error instanceof Error) {
        setErrorMessage(error.message);
      } else {
        setErrorMessage('An unknown error occurred');
      }
      setIsDeleting(false);
      return;
    }
    conversations$.delete(conversationName);
    queryClient.invalidateQueries({
      queryKey: ['conversations', connectionConfig.baseUrl, isConnected],
    });
    selectedConversation$.set(demoConversations[0].name);

    // Reset state
    await onDelete();
    setIsDeleting(false);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Conversation</DialogTitle>
          <DialogDescription>
            Are you sure you want to delete the conversation <strong>{conversationName}</strong>?
            This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        {isError && (
          <div className="mb-4 rounded-md bg-destructive/10 p-4 text-sm text-destructive">
            <p>{errorMessage}</p>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleDelete} disabled={isDeleting}>
            {isDeleting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
