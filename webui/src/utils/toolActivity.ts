import type { Message } from '@/types/conversation';
import { parseToolCalls } from './toolCallParser';

export interface ToolCall {
  tool: string;
  args: string[];
  content: string;
  timestamp?: string;
}

export interface ToolActivityEntry {
  tool: string;
  callCount: number;
  lastCall: ToolCall;
  firstSeen?: string;
}

function parseExecutedToolCalls(messages: Message[]): ToolCall[] {
  const calls: ToolCall[] = [];

  for (let index = 0; index < messages.length; index++) {
    const message = messages[index];
    if (message.role !== 'assistant') continue;

    const parsedCalls = parseToolCalls(message.content);
    if (parsedCalls.length === 0) continue;

    // Markdown tool calls have no persisted call ID. A completed tool step has
    // system output followed by the assistant's continuation. TURN_POST hooks
    // can also emit system messages immediately after a final response, so a
    // system message alone is not evidence that a fenced example executed.
    let next = index + 1;
    while (next < messages.length && messages[next].role === 'system') {
      next++;
    }
    if (next >= messages.length || messages[next].role !== 'assistant') continue;

    const resultCount = next - index - 1;
    for (const call of parsedCalls.slice(0, resultCount)) {
      const args = call.args;
      const content = call.content || args[0] || '';
      calls.push({
        tool: call.tool.toLowerCase(),
        args,
        content,
        timestamp: message.timestamp,
      });
    }
  }

  return calls;
}

export function buildToolActivity(messages: Message[]): ToolActivityEntry[] {
  const byTool = new Map<string, ToolActivityEntry>();

  for (const call of parseExecutedToolCalls(messages)) {
    const existing = byTool.get(call.tool);
    if (existing) {
      existing.callCount++;
      existing.lastCall = call;
    } else {
      byTool.set(call.tool, {
        tool: call.tool,
        callCount: 1,
        lastCall: call,
        firstSeen: call.timestamp,
      });
    }
  }

  return Array.from(byTool.values()).sort((a, b) => b.callCount - a.callCount);
}
