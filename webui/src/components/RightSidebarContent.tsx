import type { FC } from 'react';
import type { RightSidebarPanelId } from '@/types/sidebar';
import { getRightSidebarPanel } from './rightSidebarPanels';

interface Props {
  conversationId: string;
  activeTab: RightSidebarPanelId;
}

export const RightSidebarContent: FC<Props> = ({ conversationId, activeTab }) => {
  const panel = getRightSidebarPanel(activeTab);

  return <div className="h-full border-l bg-background">{panel?.render({ conversationId })}</div>;
};
