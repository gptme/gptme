import type { FC } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';

interface Props {
  count: number;
  tools: string[];
  isExpanded: boolean;
  onToggle: () => void;
}

export const CollapsedStepGroup: FC<Props> = ({ count, tools, isExpanded, onToggle }) => {
  const toolSummary = tools.length > 0 ? ` — ${tools.join(', ')}` : '';

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
          <span>
            {count} step{count !== 1 ? 's' : ''}
            {toolSummary}
          </span>
        </button>
      </div>
    </div>
  );
};
