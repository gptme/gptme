import { type FC } from 'react';
import { MenuBar } from '@/components/MenuBar';
import { AdminView } from '@/components/AdminView';
import { ServerHealthPanel } from '@/components/dashboard/ServerHealthPanel';
import { SidebarIcons } from '@/components/SidebarIcons';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { useTasksQuery } from '@/stores/tasks';

const Admin: FC = () => {
  const { data: tasks = [] } = useTasksQuery();

  return (
    <div className="flex h-dvh flex-col">
      <MenuBar />
      <div className="flex min-h-0 flex-1">
        <SidebarIcons tasks={tasks} />
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="border-b px-4 py-3">
            <ServerHealthPanel />
          </div>
          <div className="flex-1 overflow-hidden">
            <AdminView />
          </div>
        </div>
      </div>
      <MobileBottomNav />
    </div>
  );
};

export default Admin;
