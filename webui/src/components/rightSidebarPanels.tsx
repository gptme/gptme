import type { ReactNode } from 'react';
import {
  Cpu,
  GitBranch,
  Globe,
  FolderOpen,
  LayoutDashboard,
  Monitor,
  Package,
  SlidersHorizontal,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { RightSidebarPanelId } from '@/types/sidebar';
import { ArtifactsPanel } from './ArtifactsPanel';
import { BranchMapPanel } from './BranchMapPanel';
import { BrowserPreview } from './BrowserPreview';
import { ComputerPreview } from './ComputerPreview';
import { ConversationSettings } from './ConversationSettings';
import { FunctionBrowserPanel } from './FunctionBrowserPanel';
import { PanelsPanel } from './PanelsPanel';
import { WorkspaceExplorer } from './workspace/WorkspaceExplorer';

interface RightSidebarPanelRenderProps {
  conversationId: string;
}

export interface RightSidebarPanelDefinition {
  id: RightSidebarPanelId;
  label: string;
  icon: LucideIcon;
  render: (props: RightSidebarPanelRenderProps) => ReactNode;
}

export const rightSidebarPanels: RightSidebarPanelDefinition[] = [
  {
    id: 'settings',
    label: 'Chat Settings',
    icon: SlidersHorizontal,
    render: ({ conversationId }) => <ConversationSettings conversationId={conversationId} />,
  },
  {
    id: 'workspace',
    label: 'Workspace',
    icon: FolderOpen,
    render: ({ conversationId }) => <WorkspaceExplorer conversationId={conversationId} />,
  },
  {
    id: 'artifacts',
    label: 'Artifacts',
    icon: Package,
    render: ({ conversationId }) => <ArtifactsPanel conversationId={conversationId} />,
  },
  {
    id: 'branches',
    label: 'Branches',
    icon: GitBranch,
    render: ({ conversationId }) => <BranchMapPanel conversationId={conversationId} />,
  },
  {
    id: 'panels',
    label: 'Panels',
    icon: LayoutDashboard,
    render: ({ conversationId }) => <PanelsPanel conversationId={conversationId} />,
  },
  {
    id: 'functions',
    label: 'Functions',
    icon: Cpu,
    render: () => <FunctionBrowserPanel />,
  },
  {
    id: 'browser',
    label: 'Browser',
    icon: Globe,
    render: () => <BrowserPreview />,
  },
  {
    id: 'computer',
    label: 'Computer',
    icon: Monitor,
    render: () => <ComputerPreview />,
  },
];

export function getRightSidebarPanel(
  panelId: RightSidebarPanelId
): RightSidebarPanelDefinition | undefined {
  return rightSidebarPanels.find((panel) => panel.id === panelId);
}
