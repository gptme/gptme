/**
 * Client-side types for the conversation panel registry (#830 Phase 3b).
 *
 * The server validates each descriptor's ``src`` and ``sandbox`` tokens before
 * sending; the webui trusts these values and passes them straight to
 * ``SandboxedIframePanel``.
 */

import type { IframeSandboxToken } from '@/types/panel';

export interface IframePanelEntry {
  id: string;
  kind: 'iframe';
  title: string;
  src: string;
  sandbox: IframeSandboxToken[];
  allow?: string | null;
  resize?: 'auto' | 'fixed' | null;
  bootstrap?: Record<string, unknown> | null;
  icon?: string | null;
  message_index?: number | null;
}

export interface PanelListResponse {
  panels: IframePanelEntry[];
}
