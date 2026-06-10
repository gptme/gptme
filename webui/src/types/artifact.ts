export type ArtifactKind =
  | 'image'
  | 'audio'
  | 'video'
  | 'html'
  | 'markdown'
  | 'pdf'
  | 'diff'
  | 'dataset'
  | 'webapp'
  | 'binary'
  | 'other';

export type ArtifactPreviewType = 'image' | 'audio' | 'video' | 'text' | 'pdf' | 'none';

export interface ArtifactSource {
  type: 'attachment' | 'workspace' | 'external' | 'inline';
  path: string | null;
  url: string | null;
}

export interface ArtifactProvenance {
  message_index: number | null;
  tool: string | null;
}

export interface ArtifactPreview {
  type: ArtifactPreviewType;
}

export interface ArtifactAction {
  type: string;
  panel: string | null;
  artifact_id: string | null;
}

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  title: string;
  source: ArtifactSource;
  created_at: string;
  size: number | null;
  mime_type: string | null;
  provenance: ArtifactProvenance;
  preview: ArtifactPreview;
  actions: ArtifactAction[];
  /** Unified diff of the change, for files modified by the conversation. */
  diff: string | null;
}

export interface ArtifactListResponse {
  artifacts: Artifact[];
}
