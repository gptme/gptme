import { useRef, useState } from 'react';
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
import { conversationsQueryKey } from '@/hooks/useConversationsInfiniteQuery';
import { getClientForServer } from '@/stores/serverClients';
import { serverRegistry$ } from '@/stores/servers';
import { useLocation, useNavigate } from 'react-router-dom';
import { appRoute } from '@/utils/routes';

interface Props {
  conversationName: string;
  /** Optional friendly display name. Falls back to conversationName if not provided. */
  displayName?: string;
  /** Owning server for sidebar rows. Missing means the primary client owns it. */
  serverId?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDelete: () => void;
}

export function DeleteConversationConfirmationDialog({
  conversationName,
  displayName,
  serverId,
  open,
  onOpenChange,
  onDelete,
}: Props) {
  const { api } = useApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  // Keep the latest route available to the async delete handler; a closure
  // captured at click time would miss a selection change during the request.
  const locationRef = useRef(location);
  locationRef.current = location;
  const [isDeleting, setIsDeleting] = useState(false);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleDelete = async () => {
    // Show loading indicator
    setIsDeleting(true);

    // A row from a secondary server must be deleted through that server. If its
    // owner disappeared after the menu opened, fail closed — never redirect the
    // destructive call to the primary server (which may hold the same ID).
    const owner = serverId ? getClientForServer(serverId) : api;
    if (!owner) {
      setIsError(true);
      setErrorMessage('The owning server is no longer available. Nothing was deleted.');
      setIsDeleting(false);
      return;
    }

    // Snapshot which server this destructive action targets. The row's server
    // identity must not change mid-flight just because the active server did.
    const activeServerAtConfirm = serverRegistry$.peek().activeServerId;
    const targetOwnerId = serverId ?? activeServerAtConfirm;
    const targetIsPrimary = targetOwnerId === activeServerAtConfirm;

    // Delete conversation
    try {
      await owner.deleteConversation(conversationName);
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
    // Selection identity includes the server. Read the *current* route after
    // the await: the same ID may have been selected from another server while
    // the delete request was in flight, and that selection must survive.
    const currentSearchParams = new URLSearchParams(locationRef.current.search);
    const currentSelectedOwnerId =
      currentSearchParams.get('server') ?? serverRegistry$.peek().activeServerId;
    const deletingSelectedConversation =
      selectedConversation$.peek() === conversationName && currentSelectedOwnerId === targetOwnerId;
    // Navigate away before clearing local state to avoid a blank pane flash
    // (conversations$.delete triggers a re-render; if we're still on the
    // deleted conversation's URL, that render shows an empty view).
    if (deletingSelectedConversation) {
      selectedConversation$.set('');
      navigate(appRoute('/chat'), { replace: true });
    }
    // The global conversation store is keyed by bare ID and carries no owner
    // metadata. Mutating it for a secondary delete could evict the primary's
    // same-ID cache, so only the primary flow writes it. The owner query is
    // still invalidated below and will reload from the correct server.
    const selectionMovedToOtherOwnerSameId =
      selectedConversation$.peek() === conversationName && currentSelectedOwnerId !== targetOwnerId;
    if (targetIsPrimary && !selectionMovedToOtherOwnerSameId) {
      conversations$.delete(conversationName);
    }
    queryClient.invalidateQueries({
      queryKey: targetIsPrimary
        ? conversationsQueryKey(owner.baseUrl)
        : ['secondary-conversations', serverId],
    });

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
            Are you sure you want to delete the conversation{' '}
            <strong>{displayName || conversationName}</strong>? This action cannot be undone.
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
