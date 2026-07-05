import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ArtifactsPanel } from '../ArtifactsPanel';
import type { Artifact } from '@/types/artifact';

const mockListArtifacts = jest.fn();
const mockSetters = {
  workspaceNavigate: jest.fn(),
  rightSidebarActiveTab: jest.fn(),
  rightSidebarVisible: jest.fn(),
};

jest.mock('@/utils/artifactsApi', () => ({
  useArtifactsApi: () => ({
    listArtifacts: mockListArtifacts,
  }),
}));

jest.mock('../workspace/FilePreview', () => ({
  FilePreview: ({ file }: { file: { name: string; path: string } }) => (
    <div data-testid="file-preview">
      {file.name}:{file.path}
    </div>
  ),
}));

jest.mock('@/stores/workspaceExplorer', () => ({
  workspaceNavigateTo$: {
    set: (...args: unknown[]) => mockSetters.workspaceNavigate(...args),
  },
}));

jest.mock('@/stores/sidebar', () => ({
  rightSidebarActiveTab$: {
    set: (...args: unknown[]) => mockSetters.rightSidebarActiveTab(...args),
  },
  rightSidebarVisible$: {
    set: (...args: unknown[]) => mockSetters.rightSidebarVisible(...args),
  },
}));

describe('ArtifactsPanel', () => {
  const artifact: Artifact = {
    id: 'art_hero',
    kind: 'image',
    title: 'hero.png',
    source: {
      type: 'attachment',
      path: 'attachments/nested/hero.png',
      url: null,
    },
    created_at: '2026-05-30T12:00:00Z',
    size: 4096,
    mime_type: 'image/png',
    provenance: {
      message_index: 2,
      tool: null,
    },
    preview: {
      type: 'image',
    },
    actions: [],
    diff: null,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('loads artifacts and previews the first attachment-backed artifact', async () => {
    mockListArtifacts.mockResolvedValue([artifact]);

    render(<ArtifactsPanel conversationId="conv-1" />);

    await waitFor(() => {
      expect(screen.getByText('hero.png')).toBeInTheDocument();
    });

    expect(mockListArtifacts).toHaveBeenCalledWith('conv-1', expect.any(AbortSignal));
    await waitFor(() => {
      expect(screen.getByTestId('file-preview')).toHaveTextContent('hero.png:nested/hero.png');
    });
  });

  it('routes the selected artifact to the workspace viewer', async () => {
    mockListArtifacts.mockResolvedValue([artifact]);

    render(<ArtifactsPanel conversationId="conv-2" />);

    await waitFor(() => {
      const btn = screen.getByTitle('Open in workspace viewer');
      expect(btn).toBeInTheDocument();
      expect(btn).not.toBeDisabled();
    });

    fireEvent.click(screen.getByTitle('Open in workspace viewer'));

    expect(mockSetters.workspaceNavigate).toHaveBeenCalledWith({
      path: 'nested',
      root: 'attachments',
    });
    expect(mockSetters.rightSidebarVisible).toHaveBeenCalledWith(true);
    expect(mockSetters.rightSidebarActiveTab).toHaveBeenCalledWith('workspace');
  });

  it('renders the diff for a modified artifact and toggles to preview', async () => {
    const modified: Artifact = {
      ...artifact,
      id: 'art_app',
      kind: 'other',
      title: 'app.py',
      source: { type: 'workspace', path: 'app.py', url: null },
      preview: { type: 'text' },
      provenance: { message_index: 1, tool: 'patch' },
      diff: '-old line\n+new line',
    };
    mockListArtifacts.mockResolvedValue([modified]);

    render(<ArtifactsPanel conversationId="conv-diff" />);

    await waitFor(() => {
      expect(screen.getByText('-old line')).toBeInTheDocument();
    });
    expect(screen.getByText('+new line')).toBeInTheDocument();

    // Toggle to the file preview.
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => {
      expect(screen.getByTestId('file-preview')).toHaveTextContent('app.py:app.py');
    });
  });

  it('shows an error when artifact loading fails', async () => {
    mockListArtifacts.mockRejectedValue(new Error('Artifacts unavailable'));

    render(<ArtifactsPanel conversationId="conv-3" />);

    await waitFor(() => {
      expect(screen.getByText('Artifacts unavailable')).toBeInTheDocument();
    });
  });
});
