import type { ExecutingTool } from '@/stores/conversations';
import { Loader2, Cog } from 'lucide-react';
import { type Observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import { CodeDisplay } from '@/components/CodeDisplay';
import { MessageAvatar } from './MessageAvatar';
import { detectToolLanguage } from '@/utils/highlightUtils';
import { observable } from '@legendapp/state';

interface InlineToolExecutionProps {
  executingTool$: Observable<ExecutingTool | null>;
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
    <div className="role-system mb-4 mt-4">
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
              <div className="border-b border-blue-200 px-4 py-3 dark:border-blue-800">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-blue-600 dark:text-blue-400" />
                  <h3 className="font-medium text-blue-800 dark:text-blue-200">Tool Executing</h3>
                </div>
                <p className="mt-1 text-sm text-blue-700 dark:text-blue-300">
                  The assistant is currently using
                  <code className="rounded bg-muted px-2 py-1 font-mono text-sm">
                    {executingTool.tooluse.tool}
                  </code>
                  <Cog className="h-4 w-4 animate-spin text-blue-600 dark:text-blue-400" />
                </p>
              </div>

              <div className="space-y-4 p-4">
                {/* Arguments */}
                {executingTool.tooluse.args.length > 0 && (
                  <div className="space-y-2">
                    <span className="text-sm font-medium text-muted-foreground">Arguments:</span>
                    <CodeDisplay
                      code={formatArgs(executingTool.tooluse.args)}
                      maxHeight="120px"
                      showLineNumbers={false}
                    />
                  </div>
                )}

                {/* Code */}
                <div className="space-y-2">
                  <CodeDisplay
                    code={executingTool.tooluse.content}
                    maxHeight="240px"
                    showLineNumbers={true}
                    language={detectToolLanguage(
                      executingTool.tooluse.tool,
                      executingTool.tooluse.args,
                      executingTool.tooluse.content
                    )}
                  />
                </div>

                {/* Status indicator */}
                <div className="flex items-center gap-2 border-t border-blue-200 pt-3 dark:border-blue-800">
                  <Loader2 className="h-4 w-4 animate-spin text-blue-600 dark:text-blue-400" />
                  <span className="text-sm text-blue-700 dark:text-blue-300">
                    Executing tool...
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
