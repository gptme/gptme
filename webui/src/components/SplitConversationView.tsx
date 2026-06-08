import { type FC } from 'react';
import { X } from 'lucide-react';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { ConversationContent } from './ConversationContent';
import { Button } from '@/components/ui/button';

interface Props {
  leftId: string;
  rightId: string;
  serverId?: string;
  /** Stack panes vertically instead of side-by-side (for narrow screens). */
  vertical?: boolean;
  onClose: () => void;
}

export const SplitConversationView: FC<Props> = ({
  leftId,
  rightId,
  serverId,
  vertical = false,
  onClose,
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
      <ResizablePanelGroup
        direction={vertical ? 'vertical' : 'horizontal'}
        className="min-h-0 flex-1"
      >
        <ResizablePanel defaultSize={50} minSize={20}>
          <ConversationContent key={leftId} conversationId={leftId} serverId={serverId} />
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={50} minSize={20}>
          <ConversationContent key={rightId} conversationId={rightId} serverId={serverId} />
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
};
