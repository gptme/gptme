import { useState, type FC } from 'react';
import {
  Terminal,
  Server,
  Globe,
  Eye,
  Code,
  FileText,
  Save,
  ClipboardPlus,
  Scissors,
  Wrench,
  ChevronDown,
  ChevronRight,
  CheckCircle,
  XCircle,
  Loader2,
} from 'lucide-react';
import type { ToolUse } from '@/types/conversation';
import {
  getToolSummary,
  getToolCategory,
  CATEGORY_BORDER_ONLY,
  isKnownTool,
} from '@/utils/toolCallParser';
import { CodeDisplay } from '@/components/CodeDisplay';
import { detectToolLanguage } from '@/utils/highlightUtils';

/**
 * Map tool names to Lucide icons. Covers all common gptme tools.
 */
function getToolIcon(tool: string): typeof Terminal {
  const t = tool.toLowerCase();
  switch (t) {
    case 'shell':
    case 'tmux':
      return Terminal;
    case 'ipython':
    case 'python':
      return Code;
    case 'save':
      return Save;
    case 'append':
      return ClipboardPlus;
    case 'patch':
    case 'morph':
      return Scissors;
    case 'read':
      return FileText;
    case 'browser':
      return Globe;
    case 'vision':
      return Eye;
    case 'gh':
    case 'mcp':
      return Server;
    default:
      return Wrench;
  }
}

interface RichToolCallProps {
  toolUse: ToolUse;
  /** Whether this tool call is currently executing (shows spinner) */
  isExecuting?: boolean;
  /** Whether the tool completed successfully (shows checkmark/x) */
  completed?: boolean | null;
  /** Tool duration in ms for completed tools */
  durationMs?: number;
  /** Default expanded state */
  defaultExpanded?: boolean;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export const RichToolCall: FC<RichToolCallProps> = ({
  toolUse,
  isExecuting = false,
  completed = null,
  durationMs,
  defaultExpanded = false,
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const category = getToolCategory(toolUse.tool);
  const summary = getToolSummary(toolUse);
  const language = detectToolLanguage(toolUse.tool, toolUse.args, toolUse.content);
  const Icon = getToolIcon(toolUse.tool);
  const borderClass = CATEGORY_BORDER_ONLY[category];

  // Status badge
  const statusBadge = isExecuting ? (
    <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
  ) : completed === true ? (
    <CheckCircle className="h-3.5 w-3.5 text-green-500" />
  ) : completed === false ? (
    <XCircle className="h-3.5 w-3.5 text-red-500" />
  ) : null;

  return (
    <div className="my-2">
      <div
        className={`rounded-md border bg-card ${borderClass} border-l-4`}
        role="button"
        tabIndex={0}
        onClick={() => setIsExpanded(!isExpanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setIsExpanded(!isExpanded);
          }
        }}
        aria-expanded={isExpanded}
      >
        {/* Summary header */}
        <div className="flex items-center gap-2 px-3 py-2">
          {isExpanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
          <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium">{toolUse.tool}</code>
          <span className="truncate text-xs text-muted-foreground">{summary}</span>
          {durationMs != null && (
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
              {formatDuration(durationMs)}
            </span>
          )}
          {statusBadge}
        </div>

        {/* Expandable details */}
        {isExpanded && (
          <div className="space-y-3 border-t px-3 py-3">
            {/* Args when there are non-trivial args */}
            {toolUse.args &&
              toolUse.args.length > 0 &&
              toolUse.args.some((a) => a.trim().length > 0) && (
                <div className="space-y-1">
                  <span className="text-xs font-medium text-muted-foreground">Arguments:</span>
                  <CodeDisplay
                    code={toolUse.args.join('\n')}
                    maxHeight="80px"
                    showLineNumbers={false}
                    language=""
                  />
                </div>
              )}

            {/* Content / code body */}
            {toolUse.content && toolUse.content.trim().length > 0 && (
              <div className="space-y-1">
                <span className="text-xs font-medium text-muted-foreground">Content:</span>
                <CodeDisplay
                  code={toolUse.content}
                  maxHeight="300px"
                  showLineNumbers={true}
                  language={language}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

/**
 * Parse and render tool calls from message content, replacing codeblock
 * tool markers with structured RichToolCall cards.
 *
 * Used by ChatMessage to transform assistant messages that contain tool calls.
 */
export function renderToolCallsFromContent(
  content: string,
  completedTools?: Map<number, { success: boolean; durationMs: number }>
): { content: string; toolCalls: React.ReactNode[] } {
  const codeblockRegex = /```(\w+)(\s+[^\n]*)?\n([\s\S]*?)```/g;
  const toolCalls: React.ReactNode[] = [];
  let cleanedContent = content;

  let match: RegExpExecArray | null;
  let callIndex = 0;
  const matches: Array<{
    start: number;
    end: number;
    tool: string;
    args: string;
    body: string;
    index: number;
  }> = [];

  // Collect matches first — skip non-tool language fences
  while ((match = codeblockRegex.exec(content)) !== null) {
    const tool = match[1];
    if (!isKnownTool(tool)) continue;

    matches.push({
      start: match.index,
      end: match.index + match[0].length,
      tool,
      args: (match[2] || '').trim(),
      body: match[3].trim(),
      index: callIndex++,
    });
  }

  // If no tool calls found, return original content
  if (matches.length === 0) {
    return { content, toolCalls: [] };
  }

  // Process matches in reverse to preserve indices
  for (let i = matches.length - 1; i >= 0; i--) {
    const m = matches[i];
    const args: string[] = [];
    if (m.args) args.push(m.args);

    // Check if first line of body looks like an arg
    const bodyLines = m.body.split('\n');
    if (bodyLines.length > 0) {
      const firstLine = bodyLines[0].trim();
      if (firstLine && !firstLine.startsWith('#') && !firstLine.startsWith('//') && !m.args) {
        args.push(firstLine);
      }
    }

    const toolUse: ToolUse = { tool: m.tool, args, content: m.body };
    const key = `toolcall-${m.index}`;
    const compMeta = completedTools?.get(m.index);
    toolCalls.unshift(
      <RichToolCall
        key={key}
        toolUse={toolUse}
        completed={compMeta?.success ?? null}
        durationMs={compMeta?.durationMs}
      />
    );

    // Remove the codeblock from the content (replace with placeholder for later reconstruction,
    // or just remove it entirely since RichToolCall replaces it visually)
    cleanedContent = cleanedContent.substring(0, m.start) + cleanedContent.substring(m.end);
  }

  // Clean up double-newlines created by removal
  cleanedContent = cleanedContent.replace(/\n{3,}/g, '\n\n').trim();

  return { content: cleanedContent, toolCalls };
}
