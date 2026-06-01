import type { FC } from 'react';
import {
  ChevronRight,
  ChevronDown,
  Terminal,
  FileText,
  Code,
  Globe,
  Eye,
  Wrench,
} from 'lucide-react';
import type { ToolStepDetail } from '@/utils/stepGrouping';
import { getToolCategory, CATEGORY_BORDER_ONLY } from '@/utils/toolCallParser';

/** Map tool name to icon */
function getStepIcon(tool: string): typeof Terminal {
  switch (tool.toLowerCase()) {
    case 'shell':
    case 'tmux':
      return Terminal;
    case 'ipython':
    case 'python':
      return Code;
    case 'save':
    case 'append':
      return FileText;
    case 'patch':
    case 'morph':
      return FileText;
    case 'read':
      return FileText;
    case 'browser':
      return Globe;
    case 'vision':
      return Eye;
    default:
      return Wrench;
  }
}

interface Props {
  count: number;
  tools: string[];
  isExpanded: boolean;
  onToggle: () => void;
  /** Rich per-step detail (from assistant codeblock parsing) */
  steps?: ToolStepDetail[];
}

export const CollapsedStepGroup: FC<Props> = ({ count, tools, isExpanded, onToggle, steps }) => {
  // When we have rich step data, render per-tool summaries
  const hasStepDetail = steps && steps.length > 0;
  const toolSummary = !hasStepDetail ? (tools.length > 0 ? ` — ${tools.join(', ')}` : '') : '';

  return (
    <div className="mx-auto max-w-3xl px-4">
      <div className="md:px-12">
        <button
          onClick={onToggle}
          className="my-2 flex w-full items-center gap-2 rounded-md border border-border/50 bg-muted/30 px-3 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60"
        >
          {isExpanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0" />
          )}
          <span className="shrink-0">
            {count} step{count !== 1 ? 's' : ''}
            {toolSummary}
          </span>

          {/* Rich per-tool summary chips */}
          {hasStepDetail && (
            <div className="flex flex-wrap items-center gap-1.5">
              {steps!.map((step, i) => {
                const category = getToolCategory(step.tool);
                const borderClass = CATEGORY_BORDER_ONLY[category];
                const Icon = getStepIcon(step.tool);
                return (
                  <span
                    key={i}
                    className={`inline-flex items-center gap-1 rounded border bg-card/50 px-1.5 py-0.5 ${borderClass} border-l-2`}
                    title={step.arg}
                  >
                    <Icon className="h-3 w-3 shrink-0" />
                    <code className="text-[11px] font-medium">{step.tool}</code>
                    {step.arg && (
                      <span className="max-w-[120px] truncate text-[10px] opacity-70">
                        {step.arg}
                      </span>
                    )}
                  </span>
                );
              })}
            </div>
          )}
        </button>
      </div>
    </div>
  );
};
