import type { Message } from '@/types/conversation';

/** Role each message index plays in step grouping */
export type StepRole =
  | { type: 'group-start'; groupId: number; count: number; tools: string[] } // first hidden step — render summary
  | { type: 'grouped'; groupId: number } // hidden step (collapsed)
  | { type: 'response' }; // final assistant response — always visible

const RUNNABLE_TOOL_LANGS = new Set([
  'append',
  'browser',
  'choice',
  'computer',
  'elicit',
  'form',
  'gh',
  'ipython',
  'mcp',
  'patch',
  'rag',
  'restart',
  'save',
  'shell',
  'subagent',
  'tmux',
]);

/**
 * Detect tool names from a system message's content.
 * Returns a short label like "shell", "save", "patch", etc.
 */
function detectTool(content: string): string | null {
  const first = content.toLowerCase().trimStart();
  if (first.startsWith('saved')) return 'save';
  if (first.startsWith('appended')) return 'append';
  if (first.startsWith('patch applied') || first.startsWith('patched')) return 'patch';
  if (first.startsWith('error')) return 'error';
  if (first.startsWith('$') || first.startsWith('```') || first.includes('exit code'))
    return 'shell';
  if (first.startsWith('ran ')) return 'shell';
  return null;
}

function hasRunnableToolUse(content: string): boolean {
  const codeBlockRe = /```([^\n`]+)\n[\s\S]*?```/g;
  let match: RegExpExecArray | null;

  while ((match = codeBlockRe.exec(content)) !== null) {
    const lang = match[1].trim().split(/\s+/)[0].toLowerCase();
    if (RUNNABLE_TOOL_LANGS.has(lang)) {
      return true;
    }
  }

  return false;
}

/**
 * Build a per-index lookup of step roles from a flat message array.
 *
 * Groups intermediate messages in each turn (between user messages),
 * keeping the last assistant message visible as the "response".
 * A "step" = one tool-use cycle (assistant action + system result).
 */
export function buildStepRoles(
  messages: Message[],
  isHidden: (idx: number) => boolean
): Map<number, StepRole> {
  const roles = new Map<number, StepRole>();

  // Walk through messages finding user-to-user spans
  let i = 0;
  while (i < messages.length) {
    // Skip to a user message
    if (messages[i].role !== 'user' || isHidden(i)) {
      i++;
      continue;
    }

    // Found a user message at index i.
    // Scan forward to find the next user message (or end).
    let j = i + 1;
    while (j < messages.length && (messages[j].role !== 'user' || isHidden(j))) {
      j++;
    }

    // messages[i+1..j-1] are the non-user messages in this turn.
    // Collect visible (non-hidden) indices in this range.
    const visibleIndices: number[] = [];
    for (let k = i + 1; k < j; k++) {
      if (!isHidden(k)) {
        visibleIndices.push(k);
      }
    }

    // Find the last assistant message among visible indices that is not just another
    // runnable tool step with later output/messages still in the turn. This keeps
    // post-hook system messages after a real response visible, while collapsing
    // assistant tool-use messages like "```shell" / "```ipython" with their output.
    let responseIdx = -1;
    for (let k = visibleIndices.length - 1; k >= 0; k--) {
      const idx = visibleIndices[k];
      if (messages[idx].role !== 'assistant') {
        continue;
      }

      const hasLaterVisibleMessages = k < visibleIndices.length - 1;
      if (hasLaterVisibleMessages && hasRunnableToolUse(messages[idx].content)) {
        continue;
      }

      responseIdx = idx;
      break;
    }

    // Intermediate steps: everything except the response
    const stepIndices = visibleIndices.filter((idx) => idx !== responseIdx);

    // Only group if there are 2+ intermediate steps (e.g. assistant tool call + system output)
    if (stepIndices.length >= 2) {
      // Detect tools used and count tool-call steps (system messages = tool results)
      const toolSet = new Set<string>();
      let toolCallCount = 0;
      for (const idx of stepIndices) {
        const msg = messages[idx];
        if (msg.role === 'system') {
          toolCallCount++;
          const tool = detectTool(msg.content);
          if (tool) toolSet.add(tool);
        }
      }

      // Use message index of first step as stable group ID
      // (incrementing counters shift when messages change, breaking expanded state)
      const firstIdx = stepIndices[0];
      const stableGroupId = firstIdx;

      // count = tool-call steps (not raw messages); fall back to message count if no system msgs
      roles.set(firstIdx, {
        type: 'group-start',
        groupId: stableGroupId,
        count: toolCallCount || stepIndices.length,
        tools: Array.from(toolSet),
      });

      // Mark the rest as grouped (hidden when collapsed)
      for (let k = 1; k < stepIndices.length; k++) {
        roles.set(stepIndices[k], { type: 'grouped', groupId: stableGroupId });
      }

      // Mark response
      if (responseIdx >= 0) {
        roles.set(responseIdx, { type: 'response' });
      }
    }

    i = j;
  }

  return roles;
}
