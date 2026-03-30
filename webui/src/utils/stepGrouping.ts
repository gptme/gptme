import type { Message } from '@/types/conversation';

/** Role each message index plays in step grouping */
export type StepRole =
  | { type: 'group-start'; groupId: number; count: number; tools: string[] } // first hidden step — render summary
  | { type: 'grouped'; groupId: number } // hidden step (collapsed)
  | { type: 'response' }; // final assistant response — always visible

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

/**
 * Build a per-index lookup of step roles from a flat message array.
 *
 * Groups sequences of 3+ non-user messages between user messages,
 * keeping the last assistant message visible as the "response".
 */
export function buildStepRoles(
  messages: Message[],
  isHidden: (idx: number) => boolean
): Map<number, StepRole> {
  const roles = new Map<number, StepRole>();
  let groupId = 0;

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

    // Find the last assistant message among visible indices — that's the response.
    let responseIdx = -1;
    for (let k = visibleIndices.length - 1; k >= 0; k--) {
      if (messages[visibleIndices[k]].role === 'assistant') {
        responseIdx = visibleIndices[k];
        break;
      }
    }

    // Intermediate steps: everything except the response
    const stepIndices = visibleIndices.filter((idx) => idx !== responseIdx);

    // Only group if there are 3+ intermediate steps
    if (stepIndices.length >= 3) {
      // Detect tools used
      const toolSet = new Set<string>();
      for (const idx of stepIndices) {
        const msg = messages[idx];
        if (msg.role === 'system') {
          const tool = detectTool(msg.content);
          if (tool) toolSet.add(tool);
        }
      }

      // Mark the first step as group-start (renders summary bar)
      const firstIdx = stepIndices[0];
      roles.set(firstIdx, {
        type: 'group-start',
        groupId,
        count: stepIndices.length,
        tools: Array.from(toolSet),
      });

      // Mark the rest as grouped (hidden when collapsed)
      for (let k = 1; k < stepIndices.length; k++) {
        roles.set(stepIndices[k], { type: 'grouped', groupId });
      }

      // Mark response
      if (responseIdx >= 0) {
        roles.set(responseIdx, { type: 'response' });
      }

      groupId++;
    }

    i = j;
  }

  return roles;
}
