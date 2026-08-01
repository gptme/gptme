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

    // New result messages carry explicit tool provenance. Fall back to call_id
    // counts for older native-tool logs, then to the conservative continuation
    // heuristic for legacy markdown logs. A mixed batch can therefore match
    // identified and untagged results without treating hook output as execution.
    let next = index + 1;
    const resultMessages: Message[] = [];
    while (next < messages.length && messages[next].role === 'system') {
      resultMessages.push(messages[next]);
      next++;
    }
    const remainingByTool = new Map<string, number>();
    for (const result of resultMessages) {
      const tool = result.metadata?.tool?.toLowerCase();
      if (tool) remainingByTool.set(tool, (remainingByTool.get(tool) ?? 0) + 1);
    }
    const identifiedResultCount = resultMessages.filter((result) => result.call_id).length;
    let fallbackCount = remainingByTool.size > 0 ? 0 : identifiedResultCount;
    if (fallbackCount === 0 && remainingByTool.size === 0) {
      fallbackCount =
        next < messages.length && messages[next].role === 'assistant' ? resultMessages.length : 0;
    }

    for (const call of parsedCalls) {
      const tool = call.tool.toLowerCase();
      const matchingResults = remainingByTool.get(tool) ?? 0;
      if (matchingResults > 0) {
        remainingByTool.set(tool, matchingResults - 1);
      } else if (fallbackCount > 0) {
        fallbackCount--;
      } else {
        continue;
      }
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
