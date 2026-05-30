import { render, screen } from '@testing-library/react';
import { RightSidebarContent } from '../RightSidebarContent';

jest.mock('../ConversationSettings', () => ({
  ConversationSettings: ({ conversationId }: { conversationId: string }) => (
    <div data-testid="settings-panel">{conversationId}</div>
  ),
}));

jest.mock('../BrowserPreview', () => ({
  BrowserPreview: () => <div data-testid="browser-panel">browser</div>,
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

  it('renders the computer panel iframe', () => {
    render(<RightSidebarContent conversationId="conv-vnc" activeTab="computer" />);

    expect(screen.getByTitle('VNC Viewer')).toBeInTheDocument();
  });
});
