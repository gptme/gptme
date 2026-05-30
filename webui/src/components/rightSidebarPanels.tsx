import type { ReactNode } from 'react';
import { Globe, FolderOpen, Monitor, Package, SlidersHorizontal } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { RightSidebarPanelId } from '@/types/sidebar';
import { ArtifactsPanel } from './ArtifactsPanel';
import { BrowserPreview } from './BrowserPreview';
import { ConversationSettings } from './ConversationSettings';
import { WorkspaceExplorer } from './workspace/WorkspaceExplorer';

const VNC_URL = 'http://localhost:6080/vnc.html';

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
    id: 'browser',
    label: 'Browser',
    icon: Globe,
    render: () => <BrowserPreview />,
  },
  {
    id: 'computer',
    label: 'Computer',
    icon: Monitor,
    render: () => (
      <iframe
        src={VNC_URL}
        className="h-full w-full rounded-md border-0"
        allow="clipboard-read; clipboard-write"
        title="VNC Viewer"
      />
    ),
  },
];

export function getRightSidebarPanel(
  panelId: RightSidebarPanelId
): RightSidebarPanelDefinition | undefined {
  return rightSidebarPanels.find((panel) => panel.id === panelId);
}
