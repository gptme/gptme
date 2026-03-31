import { observable } from '@legendapp/state';

export type WorkspaceRoot = 'workspace' | 'attachments';

/** Request from another component to navigate the workspace explorer */
export interface WorkspaceNavigateRequest {
  path: string;
  root: WorkspaceRoot;
}

/**
 * When set, WorkspaceExplorer will navigate to this path on next render.
 * Set by ChatMessage "open in workspace" button; consumed by WorkspaceExplorer.
 * Reset to null after navigation is handled.
 */
export const workspaceNavigateTo$ = observable<WorkspaceNavigateRequest | null>(null);
