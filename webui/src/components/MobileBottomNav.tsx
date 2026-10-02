import { MoreHorizontal, Search } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { appRoute } from '@/utils/routes';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { NAV_ITEMS, type NavSection } from './navItems';
import { useEffect, useState } from 'react';
import type { FC } from 'react';

const itemClass =
  'flex min-h-11 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 py-1 text-muted-foreground transition-colors';

const sectionPath = (section: NavSection) => `/${section}`;

export const MobileBottomNav: FC = () => {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);

  // The nav itself hides at the md breakpoint, but an open sheet does not.
  // Close it when the viewport crosses into desktop so the overlay cannot
  // survive a resize/rotation onto a page that keeps this component mounted.
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 768px)');
    const handleChange = (event: MediaQueryListEvent) => {
      if (event.matches) setMoreOpen(false);
    };
    mq.addEventListener('change', handleChange);
    return () => mq.removeEventListener('change', handleChange);
  }, []);

  const isActive = (section: NavSection) => {
    // Workspaces tab must also activate on /workspace/:id (singular) detail pages.
    // Chat tab must also activate on root path '/' which renders the same Index component.
    if (section === 'workspaces') return location.pathname.startsWith('/workspace');
    if (section === 'chat')
      return location.pathname === '/' || location.pathname.startsWith('/chat');
    return location.pathname.startsWith(sectionPath(section));
  };

  const primary = NAV_ITEMS.filter((i) => i.mobilePrimary);
  const overflow = NAV_ITEMS.filter((i) => !i.mobilePrimary);
  const moreActive = overflow.some((i) => isActive(i.section));

  return (
    <>
      <nav
        className="flex items-center justify-around border-t bg-background md:hidden"
        style={{
          minHeight: '3rem',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
        }}
      >
        {primary.map(({ id, icon: Icon, label, section }) => (
          <NavLink
            key={id}
            to={appRoute(sectionPath(section))}
            className={cn(itemClass, isActive(section) && 'text-foreground')}
            aria-label={label}
          >
            <Icon className="h-4 w-4" />
            <span className="text-[10px] leading-none">{label}</span>
          </NavLink>
        ))}
        {/* Search opens the command palette instead of navigating */}
        <button
          type="button"
          className={itemClass}
          onClick={() => commandPaletteOpen$.set(true)}
          aria-label="Search"
        >
          <Search className="h-4 w-4" />
          <span className="text-[10px] leading-none">Search</span>
        </button>
        <button
          type="button"
          className={cn(itemClass, moreActive && 'text-foreground')}
          onClick={() => setMoreOpen(true)}
          aria-label="More"
        >
          <MoreHorizontal className="h-4 w-4" />
          <span className="text-[10px] leading-none">More</span>
        </button>
      </nav>
      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent
          side="bottom"
          className="max-h-[85dvh] overflow-y-auto pb-[env(safe-area-inset-bottom,0px)]"
        >
          <SheetHeader>
            <SheetTitle>More</SheetTitle>
          </SheetHeader>
          <div className="mt-2 flex flex-col">
            {overflow.map(({ id, icon: Icon, label, section }) => (
              <NavLink
                key={id}
                to={appRoute(sectionPath(section))}
                onClick={() => setMoreOpen(false)}
                className={cn(
                  'flex min-h-11 items-center gap-3 rounded-md px-2 text-sm text-muted-foreground',
                  isActive(section) && 'bg-secondary text-foreground'
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
};
