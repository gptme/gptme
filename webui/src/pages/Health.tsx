import { type FC } from 'react';
import { MenuBar } from '@/components/MenuBar';
import { ServerHealthPanel } from '@/components/dashboard/ServerHealthPanel';
import { SidebarIcons } from '@/components/SidebarIcons';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { useTasksQuery } from '@/stores/tasks';

/** Standalone server health page — a lightweight single-panel view. */
const Health: FC = () => {
  const { data: tasks = [] } = useTasksQuery();

  return (
    <div className="flex h-screen flex-col">
      <MenuBar />
      <div className="flex min-h-0 flex-1">
        <SidebarIcons tasks={tasks} />
        <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
          <h1 className="text-lg font-semibold">Server Health</h1>
          <ServerHealthPanel />
        </div>
      </div>
      <MobileBottomNav />
    </div>
  );
};

export default Health;
