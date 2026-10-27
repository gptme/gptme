import type { FC } from 'react';
import type { RightSidebarPanelId } from '@/types/sidebar';
import { getRightSidebarPanel } from './rightSidebarPanels';

interface Props {
  conversationId: string;
  activeTab: RightSidebarPanelId;
  /** Server that owns this conversation; defaults to the primary client. */
  serverId?: string;
}

export const RightSidebarContent: FC<Props> = ({ conversationId, activeTab, serverId }) => {
  const panel = getRightSidebarPanel(activeTab);

  return (
    <div className="h-full overflow-hidden border-l bg-background">
      {panel?.render({ conversationId, serverId })}
    </div>
  );
};
