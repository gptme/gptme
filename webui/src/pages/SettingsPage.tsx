import { useState, type FC } from 'react';
import { Settings } from 'lucide-react';
import { MenuBar } from '@/components/MenuBar';
import { SidebarIcons } from '@/components/SidebarIcons';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { useTasksQuery } from '@/stores/tasks';
import { SettingsContent } from '@/components/SettingsContent';
import type { SettingsCategory } from '@/stores/settingsModal';

/** Full-page settings view — replaces the modal when navigated via /settings route. */
const SettingsPage: FC = () => {
  const { data: tasks = [] } = useTasksQuery();
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>('appearance');

  return (
    <div className="flex h-screen flex-col">
      <MenuBar />
      <div className="flex min-h-0 flex-1">
        <SidebarIcons tasks={tasks} />
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Page header */}
          <div className="flex items-center gap-2 border-b px-6 py-3">
            <Settings className="h-5 w-5" />
            <h1 className="text-lg font-semibold">Settings</h1>
            <p className="text-sm text-muted-foreground">Customize your gptme experience</p>
          </div>

          <SettingsContent activeCategory={activeCategory} onCategoryChange={setActiveCategory} />
        </div>
      </div>
      <MobileBottomNav />
    </div>
  );
};

export default SettingsPage;
