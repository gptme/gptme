import type { ConversationState, ExecutingTool } from '@/stores/conversations';
import { Loader2, CheckCircle, XCircle, Terminal } from 'lucide-react';
import { type Observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import { useEffect, useState } from 'react';
import { CodeDisplay } from '@/components/CodeDisplay';
import { MessageAvatar } from './MessageAvatar';
import { detectToolLanguage } from '@/utils/highlightUtils';
import { observable } from '@legendapp/state';

interface InlineToolExecutionProps {
  executingTool$: Observable<ExecutingTool | null>;
}

interface ToolCompletionBadgeProps {
  lastCompletedTool$: Observable<ConversationState['lastCompletedTool']>;
}

const BADGE_DISPLAY_MS = 3000;

export function ToolCompletionBadge({ lastCompletedTool$ }: ToolCompletionBadgeProps) {
  const lastCompletedTool = use$(lastCompletedTool$);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (lastCompletedTool) {
      const age = Date.now() - lastCompletedTool.completedAt;
      if (age >= BADGE_DISPLAY_MS) return;
      setVisible(true);
      const timer = setTimeout(() => setVisible(false), BADGE_DISPLAY_MS - age);
      return () => clearTimeout(timer);
    }
  }, [lastCompletedTool]);

  if (!visible || !lastCompletedTool) return null;

  const { toolName, durationMs, success } = lastCompletedTool;
  return (
    <div className="mx-auto max-w-3xl px-4 md:px-16">
      <div
        className={`flex items-center gap-1.5 py-1 text-xs ${
          success ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
        }`}
      >
        {success ? <CheckCircle className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
        <code className="font-mono">{toolName}</code>
        <span>
          {success ? 'completed' : 'failed'} in {formatElapsed(durationMs)}
        </span>
      </div>
    </div>
  );
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() => Date.now() - startedAt);

  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() - startedAt), 100);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <span className="ml-1 font-mono text-xs text-blue-500 dark:text-blue-400">
      {formatElapsed(elapsed)}
    </span>
  );
}

export function InlineToolExecution({ executingTool$ }: InlineToolExecutionProps) {
  const executingTool = use$(executingTool$);

  // Format args for display
  const formatArgs = (args: string[]) => {
    if (!args || args.length === 0) return 'No arguments';
    if (args.length === 1) return args[0];
    return args.map((arg, i) => `${i + 1}. ${arg}`).join('\n');
  };

  if (!executingTool) return null;

  return (
    <div className="role-system mb-2 mt-2">
      <div className="mx-auto max-w-3xl px-4">
        <div className="relative">
          <MessageAvatar
            role$={observable('system' as const)}
            isError$={observable(false)}
            isSuccess$={observable(false)}
            chainType$={observable('standalone' as const)}
          />
          <div className="md:px-12">
            <div className="rounded-lg border border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/20">
              {/* Compact header */}
              <div className="flex items-center gap-2 border-b border-blue-200 px-3 py-2 dark:border-blue-800">
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-blue-600 dark:text-blue-400" />
                <span className="text-sm font-medium text-blue-800 dark:text-blue-200">
                  Running{' '}
                  <code className="rounded bg-blue-100 px-1.5 py-0.5 font-mono text-xs dark:bg-blue-900/40">
                    {executingTool.tooluse.tool}
                  </code>
                </span>
                <ElapsedTimer startedAt={executingTool.startedAt} />
              </div>

              <div className="space-y-3 p-3">
                {/* Arguments */}
                {executingTool.tooluse.args.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-xs font-medium text-muted-foreground">Arguments:</span>
                    <CodeDisplay
                      code={formatArgs(executingTool.tooluse.args)}
                      maxHeight="80px"
                      showLineNumbers={false}
                    />
                  </div>
                )}

                {/* Code */}
                <div className="space-y-1">
                  <CodeDisplay
                    code={executingTool.tooluse.content}
                    maxHeight="200px"
                    showLineNumbers={true}
                    language={detectToolLanguage(
                      executingTool.tooluse.tool,
                      executingTool.tooluse.args,
                      executingTool.tooluse.content
                    )}
                  />
                </div>

                {/* Partial output */}
                {executingTool.partialOutput && executingTool.partialOutput.length > 0 && (
                  <div className="space-y-1">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Terminal className="h-3.5 w-3.5" />
                      Output
                    </div>
                    <CodeDisplay
                      code={executingTool.partialOutput}
                      maxHeight="160px"
                      showLineNumbers={false}
                      language=""
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
