import { GitBranch } from 'lucide-react';
import { use$ } from '@legendapp/state/react';
import type { FC } from 'react';
import { conversations$, setCurrentBranch } from '@/stores/conversations';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface BranchMapPanelProps {
  conversationId: string;
}

export const BranchMapPanel: FC<BranchMapPanelProps> = ({ conversationId }) => {
  const conversation$ = conversations$.get(conversationId);

  const branches = use$(() => conversation$?.data.branches?.get()) ?? {};
  const currentBranch = use$(() => conversation$?.currentBranch?.get()) ?? 'main';

  const branchEntries = Object.entries(branches ?? {});

  if (branchEntries.length <= 1) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <GitBranch className="h-8 w-8 text-muted-foreground/50" />
        <div className="text-sm text-muted-foreground">
          <p className="font-medium">No branches</p>
          <p className="mt-1 text-xs">
            Use the fork tool to create branch points in this conversation.
          </p>
        </div>
      </div>
    );
  }

  const sorted = [...branchEntries].sort(([a], [b]) => {
    if (a === currentBranch) return -1;
    if (b === currentBranch) return 1;
    return a.localeCompare(b);
  });

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b px-3 py-2">
        <span className="text-xs font-semibold text-muted-foreground">
          BRANCHES ({branchEntries.length})
        </span>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto p-1.5">
        {sorted.map(([name, messages]) => {
          const isActive = name === currentBranch;
          const lastUserMsg = [...messages].reverse().find((m) => m.role === 'user');
          const preview = lastUserMsg?.content?.slice(0, 60) ?? '';

          return (
            <button
              key={name}
              onClick={() => setCurrentBranch(conversationId, name)}
              className={cn(
                'w-full rounded-md px-2.5 py-2 text-left transition-colors',
                isActive ? 'bg-accent text-accent-foreground' : 'text-foreground hover:bg-accent/50'
              )}
            >
              <div className="flex items-center gap-2">
                <GitBranch
                  className={cn(
                    'h-3.5 w-3.5 shrink-0',
                    isActive ? 'text-primary' : 'text-muted-foreground'
                  )}
                />
                <span className="flex-1 truncate text-xs font-medium">{name}</span>
                <Badge
                  variant={isActive ? 'default' : 'outline'}
                  className="h-4 shrink-0 px-1 text-[10px]"
                >
                  {messages.length}
                </Badge>
              </div>
              {preview && (
                <p className="ml-[22px] mt-0.5 truncate text-[10px] text-muted-foreground">
                  {preview}
                </p>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
};
