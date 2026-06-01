import type { ToolUse } from '@/types/conversation';

/**
 * Parse ToolUse objects from markdown codeblock content.
 *
 * Tool calls in gptme messages appear as fenced markdown codeblocks
 * where the "language" tag is the tool name, the first line(s) of
 * content are args, and the rest is the code/command body.
 */
export function parseToolCalls(content: string): ToolUse[] {
  const results: ToolUse[] = [];
  const codeblockRegex = /```(\w+)(?:\s+([^\n]*))?\n([\s\S]*?)```/g;

  let match: RegExpExecArray | null;
  while ((match = codeblockRegex.exec(content)) !== null) {
    const tool = match[1];
    const inlineArgs = match[2]?.trim() || '';
    const blockContent = match[3].trim();

    // Build args array: first the inline args (after tool name on opening fence),
    // then any leading comment-like lines from the block content that look like args
    const args: string[] = [];
    if (inlineArgs) {
      args.push(inlineArgs);
    }

    // Check if first line of block content looks like an arg description
    // (e.g., "Arguments:" prefix or a path-like string)
    const lines = blockContent.split('\n');
    if (lines.length > 0) {
      const firstLine = lines[0].trim();
      if (firstLine && !firstLine.startsWith('#') && !firstLine.startsWith('//')) {
        // Could be a single-arg tool where the first line IS the arg
        // (like save where first line is filename)
        if (inlineArgs) {
          // Already captured inline arg, first line is the actual content start
        } else {
          args.push(firstLine);
        }
      }
    }

    results.push({
      tool,
      args,
      content: blockContent,
    });
  }

  return results;
}

/**
 * Get a short summary of a ToolUse for display in headers.
 *
 * Prioritizes the most informative one-line description:
 * - For shell: the command itself (first 80 chars)
 * - For save/append: the filename
 * - For patch: the target file
 * - For ipython: first non-empty line of content
 */
export function getToolSummary(toolUse: ToolUse): string {
  const { tool, args, content } = toolUse;

  // Use args if available
  if (args && args.length > 0) {
    const firstArg = args[0].trim();
    if (firstArg.length <= 60) return firstArg;
    return firstArg.substring(0, 57) + '...';
  }

  // Fall back to first line of content
  if (content) {
    const firstLine = content.split('\n')[0].trim();
    if (firstLine && firstLine.length <= 80) return firstLine;
    if (firstLine) return firstLine.substring(0, 77) + '...';
  }

  return tool || 'unknown';
}

/**
 * Get tool category for color-coding and icon selection.
 */
export type ToolCategory = 'file' | 'shell' | 'code' | 'browser' | 'vision' | 'generic';

const TOOL_CATEGORY_MAP: Record<string, ToolCategory> = {
  save: 'file',
  append: 'file',
  patch: 'file',
  read: 'file',
  ls: 'file',
  shell: 'shell',
  tmux: 'shell',
  ipython: 'code',
  python: 'code',
  browser: 'browser',
  'chrome-devtools': 'browser',
  vision: 'vision',
  screenshot: 'vision',
};

export function getToolCategory(tool: string): ToolCategory {
  return TOOL_CATEGORY_MAP[tool.toLowerCase()] || 'generic';
}

export const CATEGORY_COLORS: Record<ToolCategory, string> = {
  file: 'border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/20',
  shell: 'border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20',
  code: 'border-purple-200 bg-purple-50 dark:border-purple-800 dark:bg-purple-950/20',
  browser: 'border-orange-200 bg-orange-50 dark:border-orange-800 dark:bg-orange-950/20',
  vision: 'border-pink-200 bg-pink-50 dark:border-pink-800 dark:bg-pink-950/20',
  generic: 'border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-950/20',
};

export const CATEGORY_BORDER_ONLY: Record<ToolCategory, string> = {
  file: 'border-l-blue-400 dark:border-l-blue-500',
  shell: 'border-l-green-400 dark:border-l-green-500',
  code: 'border-l-purple-400 dark:border-l-purple-500',
  browser: 'border-l-orange-400 dark:border-l-orange-500',
  vision: 'border-l-pink-400 dark:border-l-pink-500',
  generic: 'border-l-gray-400 dark:border-l-gray-500',
};
