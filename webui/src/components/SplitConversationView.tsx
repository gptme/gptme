import { type FC, useState } from 'react';
import { X, ChevronDown } from 'lucide-react';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { ConversationContent } from './ConversationContent';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import type { ConversationSummary } from '@/types/conversation';

interface Props {
  leftId: string;
  rightId: string;
  serverId?: string;
  leftIsReadOnly?: boolean;
  rightIsReadOnly?: boolean;
  isLoading?: boolean;
  /** Stack panes vertically instead of side-by-side (for narrow screens). */
  vertical?: boolean;
  onClose: () => void;
  /** Full conversation list for the pane selector dropdowns. */
  allConversations?: ConversationSummary[];
  /** Called when the left pane switches to a different conversation. */
  onNavigateLeft?: (id: string) => void;
  /** Called when the right pane switches to a different conversation. */
  onNavigateRight?: (id: string) => void;
}

function ConversationSelector({
  currentId,
  allConversations,
  onSelect,
}: {
  currentId: string;
  allConversations: ConversationSummary[];
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = allConversations.find((c) => c.id === currentId);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 max-w-[180px] gap-1 truncate px-1 text-xs font-normal text-muted-foreground hover:text-foreground"
        >
          <span className="truncate">{current?.name || currentId}</span>
          <ChevronDown className="h-3 w-3 flex-shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[250px] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search conversations..." />
          <CommandList>
            <CommandEmpty>No conversations found.</CommandEmpty>
            <CommandGroup>
              {allConversations.map((conv) => (
                <CommandItem
                  key={conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id}
                  value={conv.id}
                  keywords={[conv.name || '', conv.id]}
                  onSelect={() => {
                    onSelect(conv.id);
                    setOpen(false);
                  }}
                >
                  <span className="truncate">{conv.name || conv.id}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export const SplitConversationView: FC<Props> = ({
  leftId,
  rightId,
  serverId,
  leftIsReadOnly,
  rightIsReadOnly,
  isLoading = false,
  vertical = false,
  onClose,
  allConversations = [],
  onNavigateLeft,
  onNavigateRight,
}) => {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex flex-shrink-0 items-center justify-between border-b px-3 py-1">
        <span className="text-xs text-muted-foreground">Split view</span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={onClose}
          title="Close split view"
        >
          <X className="h-3 w-3" />
        </Button>
      </div>
      {isLoading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <div className="text-center">
            <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
            <p className="text-sm text-muted-foreground">Loading split view...</p>
          </div>
        </div>
      ) : (
        <ResizablePanelGroup
          direction={vertical ? 'vertical' : 'horizontal'}
          className="min-h-0 flex-1"
        >
          <ResizablePanel defaultSize={50} minSize={20}>
            <div className="flex h-full flex-col">
              <div className="flex flex-shrink-0 items-center border-b px-2 py-0.5">
                <ConversationSelector
                  currentId={leftId}
                  allConversations={allConversations}
                  onSelect={onNavigateLeft || (() => {})}
                />
              </div>
              <div className="min-h-0 flex-1">
                <ConversationContent
                  key={leftId}
                  conversationId={leftId}
                  serverId={serverId}
                  isReadOnly={leftIsReadOnly}
                />
              </div>
            </div>
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={50} minSize={20}>
            <div className="flex h-full flex-col">
              <div className="flex flex-shrink-0 items-center border-b px-2 py-0.5">
                <ConversationSelector
                  currentId={rightId}
                  allConversations={allConversations}
                  onSelect={onNavigateRight || (() => {})}
                />
              </div>
              <div className="min-h-0 flex-1">
                <ConversationContent
                  key={rightId}
                  conversationId={rightId}
                  serverId={serverId}
                  isReadOnly={rightIsReadOnly}
                />
              </div>
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
    </div>
  );
};
