import '@testing-library/jest-dom';
import { render } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { WorkspacesView } from '../WorkspacesView';
import type { ConversationSummary } from '@/types/conversation';

const mockNavigate = jest.fn();
const mockExtractWorkspaces = jest.fn();

jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

jest.mock('@/stores/sidebar', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    selectedWorkspace$: observable(null),
    selectedAgent$: observable(null),
    rightSidebarVisible$: observable(false),
    rightSidebarActiveTab$: observable(null),
  };
});

jest.mock('@/utils/routes', () => ({
  appRoute: (path: string) => path,
}));

jest.mock('@/utils/workspaceUtils', () => {
  const actual = jest.requireActual('@/utils/workspaceUtils');
  return {
    ...actual,
    extractWorkspacesFromConversations: (...args: unknown[]) => mockExtractWorkspaces(...args),
  };
});

const renderWorkspacesView = (conversations: ConversationSummary[] = []) =>
  render(
    <BrowserRouter>
      <WorkspacesView conversations={conversations} />
    </BrowserRouter>
  );

describe('WorkspacesView', () => {
  beforeEach(() => {
    mockExtractWorkspaces.mockClear();
    mockExtractWorkspaces.mockReturnValue([]);
  });

  it('renders empty state when no workspaces exist', () => {
    const { getByText } = renderWorkspacesView();
    expect(getByText('No workspaces yet')).toBeInTheDocument();
  });

  describe('memoization', () => {
    it('does not re-extract workspaces on re-render with stable conversations reference', () => {
      const conversations: ConversationSummary[] = [];

      const { rerender } = render(
        <BrowserRouter>
          <WorkspacesView conversations={conversations} />
        </BrowserRouter>
      );
      expect(mockExtractWorkspaces).toHaveBeenCalledTimes(1);

      // Re-render with the same reference — useMemo must skip re-extraction
      rerender(
        <BrowserRouter>
          <WorkspacesView conversations={conversations} />
        </BrowserRouter>
      );
      expect(mockExtractWorkspaces).toHaveBeenCalledTimes(1);
    });

    it('re-extracts workspaces when conversations reference changes', () => {
      const { rerender } = render(
        <BrowserRouter>
          <WorkspacesView conversations={[]} />
        </BrowserRouter>
      );
      expect(mockExtractWorkspaces).toHaveBeenCalledTimes(1);

      // New array reference → useMemo must recompute
      rerender(
        <BrowserRouter>
          <WorkspacesView conversations={[]} />
        </BrowserRouter>
      );
      expect(mockExtractWorkspaces).toHaveBeenCalledTimes(2);
    });
  });
});
