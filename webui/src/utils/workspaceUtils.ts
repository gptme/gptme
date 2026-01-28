import type { ConversationSummary } from '@/types/conversation';

export interface WorkspaceProject {
  path: string;
  name: string;
  conversationCount: number;
  lastUsed: string;
}

export interface Agent {
  name: string;
  path: string;
  description?: string;
  conversationCount: number;
  lastUsed: string;
}

// For now, we'll use a simple heuristic to detect agents
// Agents might be workspaces with certain naming patterns or special files
export function isAgentWorkspace(path: string): boolean {
  // Simple heuristic: consider workspaces with "agent" in the name as agents
  const pathLower = path.toLowerCase();
  return (
    pathLower.includes('agent') || pathLower.includes('bot') || pathLower.includes('assistant')
  );
}

// Extract unique workspaces from conversation summaries
export function extractWorkspacesFromConversations(
  conversations: ConversationSummary[]
): WorkspaceProject[] {
  const workspaceMap = new Map<string, WorkspaceProject>();

  console.log('[workspaceUtils] Processing conversations:', conversations.length);

  // Extract workspace information from conversation summaries
  for (const conversation of conversations) {
    // Skip demo conversations (readonly)
    if (conversation.readonly || !conversation.workspace) continue;

    const workspace = conversation.workspace;
    const name = workspace.split('/').pop() || workspace;
    const lastUsed = new Date(conversation.modified * 1000).toISOString(); // Convert Unix timestamp to ISO string

    if (workspaceMap.has(workspace)) {
      const existing = workspaceMap.get(workspace)!;
      existing.conversationCount += 1;
      // Use the most recent timestamp
      if (new Date(lastUsed) > new Date(existing.lastUsed)) {
        existing.lastUsed = lastUsed;
      }
    } else {
      workspaceMap.set(workspace, {
        path: workspace,
        name,
        conversationCount: 1,
        lastUsed,
      });
    }
  }

  const result = Array.from(workspaceMap.values()).sort(
    (a, b) => new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime()
  );

  console.log('[workspaceUtils] Extracted workspaces:', result);
  return result;
}

// Format path by shortening home directory to ~
export function formatPath(path: string): string {
  if (!path) return path;

  // Get home directory - in browser environment, we'll use common patterns
  const homePatterns = ['/Users/', '/home/'];

  for (const homePattern of homePatterns) {
    const homeIndex = path.indexOf(homePattern);
    if (homeIndex === 0) {
      // Find the next slash after the username
      const nextSlashIndex = path.indexOf('/', homePattern.length);
      if (nextSlashIndex > 0) {
        // Replace the home directory part with ~
        return '~' + path.substring(nextSlashIndex);
      } else {
        // The path is just the home directory
        return '~';
      }
    }
  }

  return path;
}

// Extract unique agents from conversation summaries
export function extractAgentsFromConversations(conversations: ConversationSummary[]): Agent[] {
  const agentMap = new Map<string, Agent>();

  console.log('[workspaceUtils] Processing conversations for agents:', conversations.length);

  // Extract agent information from conversation summaries
  for (const conversation of conversations) {
    if (!conversation.agent_path) continue;

    const agentPath = conversation.agent_path;
    const agentName = conversation.agent_name || agentPath.split('/').pop() || 'Unknown Agent';
    const lastUsed = new Date(conversation.modified * 1000).toISOString(); // Convert Unix timestamp to ISO string

    if (agentMap.has(agentPath)) {
      const existing = agentMap.get(agentPath)!;
      existing.conversationCount += 1;
      // Use the most recent timestamp
      if (new Date(lastUsed) > new Date(existing.lastUsed)) {
        existing.lastUsed = lastUsed;
      }
    } else {
      agentMap.set(agentPath, {
        name: agentName,
        path: agentPath,
        description: `Agent: ${agentName}`,
        conversationCount: 1,
        lastUsed,
      });
    }
  }

  const result = Array.from(agentMap.values()).sort(
    (a, b) => new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime()
  );

  console.log('[workspaceUtils] Extracted agents:', result);
  return result;
}
