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

    // Markdown tool calls have no persisted call ID. A call is only known to have
    // executed when its assistant message is followed by one or more system
    // result messages. Plain examples in a final response have no such result.
    let resultCount = 0;
    for (let next = index + 1; next < messages.length && messages[next].role === 'system'; next++) {
      resultCount++;
    }

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
