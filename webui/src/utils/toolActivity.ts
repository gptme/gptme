import type { Message } from '@/types/conversation';

// Known gptme tool names that appear as code block language identifiers
const GPTME_TOOLS = new Set([
  'bash',
  'shell',
  'python',
  'ipython',
  'save',
  'append',
  'patch',
  'read',
  'computer',
  'browser',
  'screenshot',
  'tmux',
]);

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

const TOOL_CALL_RE = /```(\w+)(?:[ \t]+([^\n]*))?\n([\s\S]*?)```/g;

function parseToolCallsFromMessage(message: Message): ToolCall[] {
  if (message.role !== 'assistant') return [];
  const calls: ToolCall[] = [];
  let match: RegExpExecArray | null;
  TOOL_CALL_RE.lastIndex = 0;
  while ((match = TOOL_CALL_RE.exec(message.content)) !== null) {
    const toolName = match[1].toLowerCase();
    if (!GPTME_TOOLS.has(toolName)) continue;
    const rawArgs = match[2]?.trim() || '';
    const args = rawArgs ? rawArgs.split(/\s+/) : [];
    const content = match[3] || '';
    calls.push({ tool: toolName, args, content, timestamp: message.timestamp });
  }
  return calls;
}

export function buildToolActivity(messages: Message[]): ToolActivityEntry[] {
  const byTool = new Map<string, ToolActivityEntry>();

  for (const message of messages) {
    const calls = parseToolCallsFromMessage(message);
    for (const call of calls) {
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
  }

  return Array.from(byTool.values()).sort((a, b) => b.callCount - a.callCount);
}
