/**
 * Typed iframe panel descriptors and the postMessage envelope for the
 * constrained iframe extension surface (#830 Phase 3).
 *
 * Plugin-owned custom UI never runs inside the core webui bundle. Instead a
 * tool declares an iframe panel descriptor and the UI runs in a sandboxed
 * iframe at runtime, communicating with the host only through the typed
 * postMessage protocol defined here.
 */

/** Sandbox tokens a descriptor is permitted to request. */
export type IframeSandboxToken =
  | 'allow-scripts'
  | 'allow-same-origin'
  | 'allow-forms'
  | 'allow-downloads';

export interface IframePanelDescriptor {
  /** Unique panel id within the conversation. */
  id: string;
  /** Discriminator for the panel registry. */
  kind: 'iframe';
  /** Tab label in the sidebar. */
  title: string;
  /** Lucide icon name (default: "layout"). */
  icon?: string;
  /** URL loaded in the iframe. Validated against the src allowlist. */
  src: string;
  /** Subset of the sandbox token allowlist the tool requests. */
  sandbox: IframeSandboxToken[];
  /** Feature-Policy string (`""` = deny all). */
  allow?: string;
  /** Auto-resize from iframe height messages, or keep a fixed height. */
  resize?: 'auto' | 'fixed';
  /** Opaque JSON forwarded to the iframe in the bootstrap message. */
  bootstrap?: Record<string, unknown>;
}

/** Typed envelope for all host <-> iframe postMessage traffic. */
export interface GptmeIframeMessage {
  type: `gptme:${string}`;
  payload?: unknown;
}

export function isGptmeIframeMessage(value: unknown): value is GptmeIframeMessage {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { type?: unknown }).type === 'string' &&
    (value as { type: string }).type.startsWith('gptme:')
  );
}
