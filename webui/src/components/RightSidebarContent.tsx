import type { FC } from 'react';
import { ConversationSettings } from './ConversationSettings';
import { BrowserPreview } from './BrowserPreview';
import { WorkspaceExplorer } from './workspace/WorkspaceExplorer';

interface Props {
  conversationId: string;
  activeTab: string;
}

const VNC_URL = 'http://localhost:6080/vnc.html';

export const RightSidebarContent: FC<Props> = ({ conversationId, activeTab }) => {
  const renderContent = () => {
    switch (activeTab) {
      case 'settings':
        return <ConversationSettings conversationId={conversationId} />;
      case 'workspace':
        return <WorkspaceExplorer conversationId={conversationId} />;
      case 'computer':
        return (
          <iframe
            src={VNC_URL}
            className="h-full w-full rounded-md border-0"
            allow="clipboard-read; clipboard-write"
            title="VNC Viewer"
          />
        );
      case 'browser':
        return <BrowserPreview />;
      default:
        return null;
    }
  };

  return <div className="h-full border-l bg-background">{renderContent()}</div>;
};
