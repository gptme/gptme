import type { Message } from '@/types/conversation';
import { isKnownTool } from './toolCallParser';

/** Detail about a single tool call within a step group */
export interface ToolStepDetail {
  tool: string;
  /** Short arg for display (filename, command, path) */
  arg: string;
  /** First line or brief snippet of the tool content */
  snippet: string;
}

/** Role each message index plays in step grouping */
export type StepRole =
  | {
      type: 'group-start';
      groupId: number;
      count: number;
      tools: string[];
      /** Rich detail for each tool call step (in order). Present when we have assistant
       *  messages with codeblock content to parse. */
      steps?: ToolStepDetail[];
    }
  | { type: 'grouped'; groupId: number } // hidden step (collapsed)
  | { type: 'response' }; // final assistant response — always visible

/**
 * Detect tool names from a system message's content.
 * Returns a short label like "shell", "save", "patch", etc.
 * Returns null for non-tool-result messages (e.g. "# Relevant Lessons").
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
 * Extract tool-call details from an assistant message's content.
 *
 * Parses markdown codeblocks like:
 *   ```save hello.py
 *   print("hi")
 *   ```
 *   ```shell
 *   Some output here...
 *   ```
 *
 * Returns the tool name, a short arg for display, and a content snippet.
 */
function extractToolSteps(content: string): ToolStepDetail[] {
  const steps: ToolStepDetail[] = [];
  const regex = /```(\w+)(?:\s+([^\n]*))?\n([\s\S]*?)```/g;

  let match: RegExpExecArray | null;
  while ((match = regex.exec(content)) !== null) {
    const tool = match[1];
    if (!isKnownTool(tool)) continue;
    const inlineArg = (match[2] || '').trim();
    const blockContent = match[3].trim();

    // Build display arg and snippet
    let arg = inlineArg;
    let snippet = '';

    if (!arg) {
      // No inline arg — use first meaningful line of block content
      const firstLine = blockContent.split('\n')[0].trim();
      if (firstLine && !firstLine.startsWith('#') && !firstLine.startsWith('//')) {
        arg = firstLine;
        snippet = blockContent.split('\n').slice(1).join('\n').trim();
      } else {
        snippet = blockContent;
      }
    } else {
      snippet = blockContent;
    }

    // Truncate arg for display
    if (arg && arg.length > 50) {
      arg = arg.substring(0, 47) + '...';
    }

    // Truncate snippet for group summary
    if (snippet && snippet.length > 30) {
      snippet = snippet.split('\n')[0].substring(0, 27) + '...';
    }

    steps.push({ tool, arg, snippet });
  }

  return steps;
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

    // Find the last assistant message that is NOT immediately followed by a tool result.
    // An assistant message is a "tool-use step" only when the very next visible message
    // is a system message that detectTool() recognises as tool output.
    // This correctly keeps post-hook system messages (e.g. "# Relevant Lessons") from
    // causing the preceding assistant response to be absorbed into the step group.
    let responseIdx = -1;
    for (let k = visibleIndices.length - 1; k >= 0; k--) {
      const idx = visibleIndices[k];
      if (messages[idx].role !== 'assistant') {
        continue;
      }

      const nextVisIdx = k < visibleIndices.length - 1 ? visibleIndices[k + 1] : -1;
      const nextMsg = nextVisIdx >= 0 ? messages[nextVisIdx] : null;
      const isFollowedByToolResult =
        nextMsg !== null && nextMsg.role === 'system' && detectTool(nextMsg.content) !== null;
      if (isFollowedByToolResult) {
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
      const allSteps: ToolStepDetail[] = [];

      for (const idx of stepIndices) {
        const msg = messages[idx];
        if (msg.role === 'system') {
          toolCallCount++;
          const tool = detectTool(msg.content);
          if (tool) toolSet.add(tool);
        } else if (msg.role === 'assistant' && msg.content) {
          // Extract rich tool-call detail from assistant messages
          const extracted = extractToolSteps(msg.content);
          allSteps.push(...extracted);
        }
      }

      // Use message index of first step as stable group ID
      // (incrementing counters shift when messages change, breaking expanded state)
      const firstIdx = stepIndices[0];
      const stableGroupId = firstIdx;

      // count = tool-call steps (not raw messages); fall back to message count if no system msgs
      const groupStart: Extract<StepRole, { type: 'group-start' }> = {
        type: 'group-start',
        groupId: stableGroupId,
        count: toolCallCount || stepIndices.length,
        tools: Array.from(toolSet),
      };

      // Only include steps when we have useful detail (don't bloat the object)
      if (allSteps.length > 0) {
        groupStart.steps = allSteps;
      }

      roles.set(firstIdx, groupStart);

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
