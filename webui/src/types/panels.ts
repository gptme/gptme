/**
 * Client-side types for the conversation panel registry (#830 Phase 3b).
 *
 * The server validates each descriptor's ``src`` and ``sandbox`` tokens before
 * sending; the webui trusts these values and passes them straight to
 * ``SandboxedIframePanel``.
 */

import type { IframeSandboxToken } from '@/types/panel';

/** Lifecycle status for a live_app panel. */
export type LiveAppStatus = 'running' | 'stopped' | 'error' | 'loading' | 'unavailable';

export interface LiveAppPanelEntry {
  id: string;
  kind: 'live_app';
  title: string;
  /** The URL of the running app. Validated against the src allowlist. */
  url: string;
  /** Current lifecycle status of the app. */
  status: LiveAppStatus;
  /** Human-readable status line shown in the panel header. */
  status_message?: string | null;
  /** Sandbox tokens for the iframe that hosts the app. */
  sandbox: IframeSandboxToken[];
  icon?: string | null;
  message_index?: number | null;
}

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

/** Union of all panel entry kinds the webui can render. */
export type PanelEntry = IframePanelEntry | LiveAppPanelEntry;

export interface PanelListResponse {
  panels: PanelEntry[];
}
