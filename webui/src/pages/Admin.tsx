import { type FC } from 'react';
import { MenuBar } from '@/components/MenuBar';
import { AdminView } from '@/components/AdminView';
import { SidebarIcons } from '@/components/SidebarIcons';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { useTasksQuery } from '@/stores/tasks';

const Admin: FC = () => {
  const { data: tasks = [] } = useTasksQuery();

  return (
    <div className="flex h-screen flex-col">
      <MenuBar />
      <div className="flex min-h-0 flex-1">
        <SidebarIcons tasks={tasks} />
        <div className="flex-1 overflow-hidden">
          <AdminView />
        </div>
      </div>
      <MobileBottomNav />
    </div>
  );
};

export default Admin;
