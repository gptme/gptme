export interface FileType {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number;
  modified: string;
  mime_type: string | null;
}

export interface TextPreview {
  type: 'text';
  content: string;
}

export interface BinaryPreview {
  type: 'binary';
  metadata: FileType;
}

export interface ImagePreview {
  type: 'image';
  content: string; // Blob URL
}

export interface Model3DPreview {
  type: 'model3d';
  content: string; // Blob URL for model-viewer src
  mime_type: string;
}

export type FilePreview = TextPreview | BinaryPreview | ImagePreview | Model3DPreview;
