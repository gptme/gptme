import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import type { ForkInfo } from '@/utils/branchUtils';
import type { FC } from 'react';

interface BranchIndicatorProps {
  forkInfo: ForkInfo;
  onSwitchBranch: (branchName: string) => void;
}

export const BranchIndicator: FC<BranchIndicatorProps> = ({ forkInfo, onSwitchBranch }) => {
  const { branchNames, currentIndex } = forkInfo;
  const canGoLeft = currentIndex > 0;
  const canGoRight = currentIndex < branchNames.length - 1;

  return (
    <div className="flex items-center justify-end gap-0.5 px-3 text-xs text-muted-foreground">
      <Button
        variant="ghost"
        size="sm"
        className="h-5 w-5 p-0"
        disabled={!canGoLeft}
        onClick={() => canGoLeft && onSwitchBranch(branchNames[currentIndex - 1])}
        aria-label="Previous branch"
      >
        <ChevronLeft className="h-3 w-3" />
      </Button>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-default tabular-nums">
              {currentIndex + 1}/{branchNames.length}
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="text-xs">
            {branchNames[currentIndex]}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 w-5 p-0"
        disabled={!canGoRight}
        onClick={() => canGoRight && onSwitchBranch(branchNames[currentIndex + 1])}
        aria-label="Next branch"
      >
        <ChevronRight className="h-3 w-3" />
      </Button>
    </div>
  );
};
