import { type FC } from 'react';
import { MenuBar } from '@/components/MenuBar';
import { HistoryView } from '@/components/HistoryView';
import { SidebarIcons } from '@/components/SidebarIcons';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { useTasksQuery } from '@/stores/tasks';

const History: FC = () => {
  const { data: tasks = [] } = useTasksQuery();

  return (
    <div className="flex h-screen flex-col">
      <MenuBar />
      <div className="flex min-h-0 flex-1">
        <SidebarIcons tasks={tasks} />
        <div className="flex-1 overflow-hidden">
          <HistoryView />
        </div>
      </div>
      <MobileBottomNav />
    </div>
  );
};

export default History;
