import { conversationsCredentialsScope } from '@/hooks/useConversationsInfiniteQuery';
import type { ConnectionConfig } from '@/utils/connectionConfig';

/**
 * localStorage key for an unsent composer draft.
 *
 * Scoped by the owning server URL and credentials as well as the conversation,
 * so the same conversation id (or the shared new-chat composer) on another
 * server or account never reads or overwrites this draft. The credential scope
 * is the same one the conversation list uses: a JWT subject (stable across token
 * refresh) or a hash of an opaque token, never the raw token.
 *
 * Legacy unscoped keys (`gptme-draft-<id>`, `gptme-draft-new`) are neither read
 * nor deleted: their owner is unknown, so adopting them could show one account's
 * text to another.
 */
export function composerDraftKey(
  owner: Pick<ConnectionConfig, 'baseUrl' | 'useAuthToken' | 'authToken'>,
  conversationId?: string
): string {
  return [
    'gptme-draft',
    encodeURIComponent(owner.baseUrl.replace(/\/+$/, '')),
    encodeURIComponent(conversationsCredentialsScope(owner)),
    conversationId ? `c-${encodeURIComponent(conversationId)}` : 'new',
  ].join(':');
}
