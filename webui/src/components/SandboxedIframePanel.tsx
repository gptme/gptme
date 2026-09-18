/**
 * Renders a plugin-owned iframe panel under the strict #830 Phase 3 contract:
 * src is validated against the allowlist, the sandbox attribute is filtered to
 * the permitted token set, and all host <-> iframe traffic flows through the
 * origin-gated postMessage protocol.
 *
 * Bootstrap flow:
 *   1. Render <iframe sandbox=... src=...> but hold the bootstrap payload.
 *   2. Iframe loads and posts `gptme:ready`.
 *   3. Host validates the sender and replies with `gptme:bootstrap`.
 *
 * Because the sandbox drops `allow-same-origin` for any scripted panel (see
 * `resolveSandbox`), the frame normally has an opaque origin: it speaks as
 * `"null"` and must be addressed with `targetOrigin: "*"`. Identity is pinned by
 * `event.source === contentWindow`, not by origin.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FC } from 'react';
import type { GptmeIframeMessage, IframePanelDescriptor } from '@/types/panel';
import { isGptmeIframeMessage } from '@/types/panel';
import {
  iframeSrcOrigin,
  isAllowedIframeSrc,
  resolvePanelSrc,
  resolveSandbox,
  sandboxHasOpaqueOrigin,
  urlOrigin,
} from '@/utils/iframePanelPolicy';

interface Props {
  descriptor: IframePanelDescriptor;
  conversationId: string;
  /** Instance API base URL. Server-relative descriptor srcs resolve against it. */
  apiBaseUrl?: string;
}

export const SandboxedIframePanel: FC<Props> = ({ descriptor, conversationId, apiBaseUrl }) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const bootstrappedRef = useRef(false);
  // The src whose document the once-per-document guard is currently armed for.
  const bootstrappedSrcRef = useRef<string | null>(null);
  const [autoHeight, setAutoHeight] = useState<number | null>(null);

  // Server-relative srcs (e.g. "/preview/5173/") belong to the instance server,
  // which may be a different origin than the page rendering this panel.
  const src = resolvePanelSrc(descriptor.src, apiBaseUrl);
  const apiOrigin = urlOrigin(apiBaseUrl);
  const allowed = isAllowedIframeSrc(src, apiOrigin);
  const expectedOrigin = allowed ? iframeSrcOrigin(src, apiOrigin ?? undefined) : null;
  // A sandbox without `allow-same-origin` gives the frame an opaque origin: its
  // messages arrive as `event.origin === "null"` and no concrete `targetOrigin`
  // can address it. `resolveSandbox` drops `allow-same-origin` whenever
  // `allow-scripts` is requested, so every scripted panel is opaque, and
  // `event.source` is the only identity gate available.
  const opaqueOrigin = sandboxHasOpaqueOrigin(descriptor.sandbox);

  // Bootstrap is scoped to the *loaded document*, not to the effect lifecycle or
  // to the src string. A document that finishes loading in this frame (initial
  // load, reload, or an in-frame navigation) gets exactly one bootstrap, so a
  // legitimate reload can complete the handshake again, while duplicate
  // `gptme:ready` messages from the same document are still ignored.
  const handleLoad = useCallback(() => {
    bootstrappedRef.current = false;
  }, []);

  useEffect(() => {
    // A src change loads a new document, so re-arm the once-per-document guard
    // synchronously. `handleLoad` covers the same case, but a cached or
    // same-origin document can post `gptme:ready` before its `load` event
    // fires; without this reset that `ready` would be dropped and the panel
    // would never receive its bootstrap payload. Other effect re-runs (e.g. a
    // `conversationId` change) must NOT reset the guard: a duplicate `ready`
    // from the already-loaded document has to stay ignored.
    if (bootstrappedSrcRef.current !== src) {
      bootstrappedSrcRef.current = src;
      bootstrappedRef.current = false;
    }
    if (!allowed) return;

    const post = (message: GptmeIframeMessage) => {
      const target = iframeRef.current?.contentWindow;
      if (!target) return;
      const targetOrigin = opaqueOrigin ? '*' : expectedOrigin;
      if (!targetOrigin) return;
      target.postMessage(message, targetOrigin);
    };

    const handleMessage = (event: MessageEvent) => {
      // Identity gate: only this panel's own frame may drive the protocol.
      if (event.source !== iframeRef.current?.contentWindow) return;
      // Origin gate. An opaque-origin frame can only ever speak as "null", and
      // the source check above is what authenticates it. Everything else must
      // match the declared src origin; fail closed when it is unresolvable.
      if (opaqueOrigin) {
        if (event.origin !== 'null') return;
      } else if (!expectedOrigin || event.origin !== expectedOrigin) {
        return;
      }
      if (!isGptmeIframeMessage(event.data)) return;

      switch (event.data.type) {
        case 'gptme:ready':
          // Bootstrap-once-per-document guard: duplicate `gptme:ready` messages
          // from the same loaded document must not re-send the payload. The
          // guard is re-armed by `handleLoad` when a new document finishes
          // loading, so a legitimate reload recovers while a duplicate ready
          // (e.g. a page posting it twice) does not.
          if (!bootstrappedRef.current) {
            bootstrappedRef.current = true;
            post({
              type: 'gptme:bootstrap',
              // Spread descriptor.bootstrap first so the prop-supplied
              // conversationId always wins over any key in the bootstrap blob.
              payload: { ...(descriptor.bootstrap ?? {}), conversation_id: conversationId },
            });
          }
          break;
        case 'gptme:resize': {
          if (descriptor.resize !== 'auto') break;
          const height = (event.data.payload as { height?: unknown } | undefined)?.height;
          const MAX_IFRAME_HEIGHT = 16_000;
          if (typeof height === 'number' && Number.isFinite(height) && height > 0) {
            setAutoHeight(Math.min(height, MAX_IFRAME_HEIGHT));
          }
          break;
        }
        // Unrecognised gptme:* messages are silently ignored for forward compat.
        default:
          break;
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [
    // `src` is required here: a src change re-resolves `expectedOrigin` and
    // `opaqueOrigin`, and the listener must capture the new values or the new
    // document is validated against the previous src's origin and never gets
    // bootstrapped. (`handleLoad` re-arms the once-per-document guard.)
    src,
    allowed,
    expectedOrigin,
    opaqueOrigin,
    conversationId,
    descriptor.bootstrap,
    descriptor.resize,
  ]);

  if (!allowed) {
    return (
      <div
        role="alert"
        className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground"
      >
        <p className="font-medium text-foreground">Panel blocked</p>
        <p>
          The panel source <code className="rounded bg-muted px-1">{src}</code> is not an allowed
          iframe origin. Only localhost tool servers, server-relative paths, and the connected
          server are permitted.
        </p>
      </div>
    );
  }

  return (
    <iframe
      ref={iframeRef}
      src={src}
      title={descriptor.title}
      onLoad={handleLoad}
      sandbox={resolveSandbox(descriptor.sandbox)}
      allow={descriptor.allow ?? ''}
      className="w-full rounded-md border-0"
      style={
        descriptor.resize === 'auto' && autoHeight ? { height: autoHeight } : { height: '100%' }
      }
    />
  );
};
