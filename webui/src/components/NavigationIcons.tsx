import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { FC } from 'react';

interface NavItem {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

interface Props {
  navItems: NavItem[];
  activeTab: string;
  onTabSelect: (tabId: string) => void;
  className?: string;
}

export const NavigationIcons: FC<Props> = ({ navItems, activeTab, onTabSelect, className }) => {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      {navItems.map((item) => {
        const Icon = item.icon;
        const isActive = activeTab === item.id;

        return (
          <Tooltip key={item.id}>
            <TooltipTrigger asChild>
              <Button
                variant={isActive ? 'secondary' : 'ghost'}
                size="icon"
                onClick={() => onTabSelect(item.id)}
                className={cn('h-10 w-10', isActive && 'bg-secondary')}
                aria-label={item.label}
              >
                <Icon className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">{item.label}</TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
};
