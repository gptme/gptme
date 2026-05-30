/**
 * Renders a plugin-owned iframe panel under the strict #830 Phase 3 contract:
 * src is validated against the allowlist, the sandbox attribute is filtered to
 * the permitted token set, and all host <-> iframe traffic flows through the
 * origin-gated postMessage protocol.
 *
 * Bootstrap flow:
 *   1. Render <iframe sandbox=... src=...> but hold the bootstrap payload.
 *   2. Iframe loads and posts `gptme:ready`.
 *   3. Host validates the origin and replies with `gptme:bootstrap`.
 */
import { useEffect, useRef, useState } from 'react';
import type { FC } from 'react';
import type { GptmeIframeMessage, IframePanelDescriptor } from '@/types/panel';
import { isGptmeIframeMessage } from '@/types/panel';
import { iframeSrcOrigin, isAllowedIframeSrc, resolveSandbox } from '@/utils/iframePanelPolicy';

interface Props {
  descriptor: IframePanelDescriptor;
  conversationId: string;
}

export const SandboxedIframePanel: FC<Props> = ({ descriptor, conversationId }) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [autoHeight, setAutoHeight] = useState<number | null>(null);

  const allowed = isAllowedIframeSrc(descriptor.src);
  const expectedOrigin = allowed ? iframeSrcOrigin(descriptor.src) : null;

  useEffect(() => {
    if (!allowed) return;

    const post = (message: GptmeIframeMessage) => {
      const target = iframeRef.current?.contentWindow;
      if (!target || !expectedOrigin) return;
      target.postMessage(message, expectedOrigin);
    };

    const handleMessage = (event: MessageEvent) => {
      // Strict origin gate: only accept messages from the declared src origin.
      // Fail closed: if expectedOrigin is null (origin unresolvable), reject all.
      if (!expectedOrigin || event.origin !== expectedOrigin) return;
      if (event.source !== iframeRef.current?.contentWindow) return;
      if (!isGptmeIframeMessage(event.data)) return;

      switch (event.data.type) {
        case 'gptme:ready':
          post({
            type: 'gptme:bootstrap',
            // Spread descriptor.bootstrap first so the prop-supplied
            // conversationId always wins over any key in the bootstrap blob.
            payload: { ...(descriptor.bootstrap ?? {}), conversation_id: conversationId },
          });
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
  }, [allowed, expectedOrigin, conversationId, descriptor.bootstrap, descriptor.resize]);

  if (!allowed) {
    return (
      <div
        role="alert"
        className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground"
      >
        <p className="font-medium text-foreground">Panel blocked</p>
        <p>
          The panel source <code className="rounded bg-muted px-1">{descriptor.src}</code> is not an
          allowed iframe origin. Only localhost tool servers and server-relative paths are
          permitted.
        </p>
      </div>
    );
  }

  return (
    <iframe
      ref={iframeRef}
      src={descriptor.src}
      title={descriptor.title}
      sandbox={resolveSandbox(descriptor.sandbox)}
      allow={descriptor.allow ?? ''}
      className="w-full rounded-md border-0"
      style={
        descriptor.resize === 'auto' && autoHeight ? { height: autoHeight } : { height: '100%' }
      }
    />
  );
};
