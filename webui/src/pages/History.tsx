import { type FC } from 'react';
import { MenuBar } from '@/components/MenuBar';
import { HistoryView } from '@/components/HistoryView';
import { SidebarIcons } from '@/components/SidebarIcons';

const History: FC = () => {
  return (
    <div className="flex h-screen flex-col">
      <MenuBar />
      <div className="flex min-h-0 flex-1">
        <SidebarIcons tasks={[]} />
        <div className="flex-1 overflow-hidden">
          <HistoryView />
        </div>
      </div>
    </div>
  );
};

export default History;
