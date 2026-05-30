import type { FC } from 'react';
import { rightSidebarVisible$, rightSidebarActiveTab$ } from '@/stores/sidebar';
import type { RightSidebarPanelId } from '@/types/sidebar';
import { use$ } from '@legendapp/state/react';
import { NavigationIcons } from './NavigationIcons';
import { rightSidebarPanels } from './rightSidebarPanels';

interface Props {
  conversationId: string;
}

export const RightSidebar: FC<Props> = ({ conversationId: _conversationId }) => {
  const activeTab = use$(rightSidebarActiveTab$);

  const handleTabSelect = (tabId: string) => {
    if (tabId === activeTab) {
      rightSidebarVisible$.set(false);
      rightSidebarActiveTab$.set(null);
    } else {
      rightSidebarVisible$.set(true);
      rightSidebarActiveTab$.set(tabId as RightSidebarPanelId);
    }
  };

  return (
    <div className="flex h-full w-12 flex-col border-l bg-background p-1">
      <NavigationIcons
        navItems={rightSidebarPanels}
        activeTab={activeTab || ''}
        onTabSelect={handleTabSelect}
      />
    </div>
  );
};
