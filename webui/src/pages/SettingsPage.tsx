import { useState, useEffect, useCallback, useRef, type FC } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Settings } from 'lucide-react';
import { MenuBar } from '@/components/MenuBar';
import { SidebarIcons } from '@/components/SidebarIcons';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { useTasksQuery } from '@/stores/tasks';
import { SettingsContent } from '@/components/SettingsContent';
import { SETTINGS_CATEGORIES, type SettingsCategory } from '@/stores/settingsModal';
import { use$ } from '@legendapp/state/react';
import { settingsModal$ } from '@/stores/settingsModal';

function toCategoryOrDefault(raw: string | undefined): SettingsCategory {
  return (SETTINGS_CATEGORIES as ReadonlyArray<string>).includes(raw ?? '')
    ? (raw as SettingsCategory)
    : 'servers';
}

/** Full-page settings view — replaces the modal when navigated via /settings route. */
const SettingsPage: FC = () => {
  const { category } = useParams<{ category?: string }>();
  const navigate = useNavigate();
  const { data: tasks = [] } = useTasksQuery();
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>(
    toCategoryOrDefault(category)
  );

  // Auto-focus the container so the Escape key handler works immediately on mount,
  // without requiring the user to click inside the page first.
  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  // Keep state in sync when URL param changes (e.g. browser back/forward)
  useEffect(() => {
    setActiveCategory(toCategoryOrDefault(category));
  }, [category]);

  const handleCategoryChange = useCallback(
    (cat: SettingsCategory) => {
      setActiveCategory(cat);
      navigate(`/settings/${cat}`);
    },
    [navigate]
  );

  // Observe external requests (e.g. ServerSelector configure button) to switch category
  const externalRequest = use$(settingsModal$);
  useEffect(() => {
    if (externalRequest.open && externalRequest.category) {
      handleCategoryChange(externalRequest.category);
      settingsModal$.open.set(false);
    }
  }, [externalRequest.open, externalRequest.category, handleCategoryChange]);

  return (
    <div
      ref={containerRef}
      className="flex h-screen flex-col"
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          // Navigate back if there is prior history,
          // otherwise fall back to /chat (e.g. direct link or new tab).
          if (window.history.length > 1) {
            navigate(-1);
          } else {
            navigate('/chat');
          }
        }
      }}
    >
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

          <SettingsContent
            activeCategory={activeCategory}
            onCategoryChange={handleCategoryChange}
          />
        </div>
      </div>
      <MobileBottomNav />
    </div>
  );
};

export default SettingsPage;
