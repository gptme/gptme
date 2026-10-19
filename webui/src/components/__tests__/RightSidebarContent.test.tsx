import { render, screen } from '@testing-library/react';
import { RightSidebarContent } from '../RightSidebarContent';

jest.mock('../ConversationSettings', () => ({
  ConversationSettings: ({
    conversationId,
    serverId,
  }: {
    conversationId: string;
    serverId?: string;
  }) => (
    <div data-testid="settings-panel">
      {conversationId}:{serverId ?? 'primary'}
    </div>
  ),
}));

jest.mock('../BrowserPreview', () => ({
  BrowserPreview: () => <div data-testid="browser-panel">browser</div>,
}));

jest.mock('../ComputerPreview', () => ({
  ComputerPreview: () => <div data-testid="computer-panel">computer</div>,
}));

jest.mock('../ArtifactsPanel', () => ({
  ArtifactsPanel: ({ conversationId }: { conversationId: string }) => (
    <div data-testid="artifacts-panel">{conversationId}</div>
  ),
}));

jest.mock('../workspace/WorkspaceExplorer', () => ({
  WorkspaceExplorer: ({ conversationId }: { conversationId: string }) => (
    <div data-testid="workspace-panel">{conversationId}</div>
  ),
}));

describe('RightSidebarContent', () => {
  it('renders the artifacts panel through the sidebar registry', () => {
    render(<RightSidebarContent conversationId="conv-art" activeTab="artifacts" />);

    expect(screen.getByTestId('artifacts-panel')).toHaveTextContent('conv-art');
  });

  it('renders the computer panel', () => {
    render(<RightSidebarContent conversationId="conv-computer" activeTab="computer" />);

    expect(screen.getByTestId('computer-panel')).toBeInTheDocument();
  });

  it('forwards the owning server to the settings panel', () => {
    render(
      <RightSidebarContent
        conversationId="conv-settings"
        activeTab="settings"
        serverId="server-2"
      />
    );

    expect(screen.getByTestId('settings-panel')).toHaveTextContent('conv-settings:server-2');
  });
});
