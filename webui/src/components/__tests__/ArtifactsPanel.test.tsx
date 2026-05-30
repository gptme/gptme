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

    expect(mockListArtifacts).toHaveBeenCalledWith('conv-1');
    expect(screen.getByTestId('file-preview')).toHaveTextContent('hero.png:nested/hero.png');
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

  it('shows an error when artifact loading fails', async () => {
    mockListArtifacts.mockRejectedValue(new Error('Artifacts unavailable'));

    render(<ArtifactsPanel conversationId="conv-3" />);

    await waitFor(() => {
      expect(screen.getByText('Artifacts unavailable')).toBeInTheDocument();
    });
  });
});
