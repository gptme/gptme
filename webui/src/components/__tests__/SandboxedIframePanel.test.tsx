import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SandboxedIframePanel } from '../SandboxedIframePanel';
import type { IframePanelDescriptor, IframeSandboxToken } from '@/types/panel';

const baseDescriptor: IframePanelDescriptor = {
  id: 'webapp-preview',
  kind: 'iframe',
  title: 'Webapp Preview',
  src: 'http://localhost:8080',
  sandbox: ['allow-scripts'],
};

function getIframe(): HTMLIFrameElement {
  const frame = screen.getByTitle('Webapp Preview');
  return frame as HTMLIFrameElement;
}

function emitFromIframe(frame: HTMLIFrameElement, origin: string, data: unknown) {
  window.dispatchEvent(new MessageEvent('message', { data, origin, source: frame.contentWindow }));
}

describe('SandboxedIframePanel', () => {
  it('renders a sandboxed iframe with the filtered sandbox attribute', () => {
    render(
      <SandboxedIframePanel
        descriptor={{
          ...baseDescriptor,
          // 'allow-popups' is never permitted; cast simulates a tool requesting it.
          sandbox: ['allow-scripts', 'allow-popups' as IframeSandboxToken],
        }}
        conversationId="conv1"
      />
    );
    const frame = getIframe();
    expect(frame.getAttribute('src')).toBe('http://localhost:8080');
    // allow-popups is never permitted and must be dropped.
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
  });

  it('sends gptme:bootstrap with the conversation id after gptme:ready', async () => {
    render(<SandboxedIframePanel descriptor={baseDescriptor} conversationId="conv-abc" />);
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', {
      value: { postMessage },
      configurable: true,
    });

    // `allow-scripts` without `allow-same-origin` is an opaque origin, which
    // browsers serialize as "null" — not the src's origin.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-abc' } },
      '*'
    );
  });

  it('replies to the concrete src origin when the sandbox keeps the frame origin', async () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, sandbox: ['allow-same-origin'] }}
        conversationId="conv-abc"
      />
    );
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'http://localhost:8080', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-abc' } },
      'http://localhost:8080'
    );
  });

  it('rejects a "null" origin from a frame that is not sandboxed (fail-closed)', async () => {
    // `allow-same-origin` is what keeps the frame's real origin; a frame that
    // keeps its origin cannot legitimately claim to be opaque.
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, sandbox: ['allow-same-origin'] }}
        conversationId="conv-abc"
      />
    );
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // A frame with a real origin cannot legitimately claim to be opaque.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await new Promise((r) => setTimeout(r, 10));
    expect(postMessage).not.toHaveBeenCalled();
  });

  it('merges descriptor bootstrap fields into the bootstrap payload', async () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, bootstrap: { artifact_id: 'art_01' } }}
        conversationId="conv-abc"
      />
    );
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-abc', artifact_id: 'art_01' } },
      '*'
    );
  });

  it('ignores messages from a foreign origin', async () => {
    render(<SandboxedIframePanel descriptor={baseDescriptor} conversationId="conv-abc" />);
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'https://evil.example.com', { type: 'gptme:ready' });

    await new Promise((r) => setTimeout(r, 10));
    expect(postMessage).not.toHaveBeenCalled();
  });

  it('ignores unrecognised gptme message types', async () => {
    render(<SandboxedIframePanel descriptor={baseDescriptor} conversationId="conv-abc" />);
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'null', { type: 'gptme:unknown' });

    await new Promise((r) => setTimeout(r, 10));
    expect(postMessage).not.toHaveBeenCalled();
  });

  it('prop conversationId wins over a conversation_id key in the bootstrap blob', async () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, bootstrap: { conversation_id: 'override-attempt' } }}
        conversationId="real-conv-id"
      />
    );
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'real-conv-id' } },
      '*'
    );
  });

  it('caps resize height at 16 000 px', async () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, resize: 'auto' }}
        conversationId="conv-abc"
      />
    );
    const frame = getIframe();
    Object.defineProperty(frame, 'contentWindow', {
      value: { postMessage: jest.fn() },
      configurable: true,
    });

    await act(async () => {
      emitFromIframe(frame, 'null', {
        type: 'gptme:resize',
        payload: { height: 1e15 },
      });
    });

    const style = frame.getAttribute('style') ?? '';
    // height should be capped, not set to 1e15
    expect(style).toMatch(/16000/);
  });

  it('renders a blocked placeholder for a disallowed src', () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, src: 'https://evil.example.com' }}
        conversationId="conv1"
      />
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/Panel blocked/)).toBeInTheDocument();
    expect(screen.queryByTitle('Webapp Preview')).not.toBeInTheDocument();
  });

  it('resolves a server-relative src against the instance API base url', () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, src: '/preview/5173/', title: 'Live App' }}
        conversationId="conv1"
        apiBaseUrl="https://fleet.gptme.ai/api/v1/instances/abc"
      />
    );
    const frame = screen.getByTitle('Live App') as HTMLIFrameElement;
    // The SPA origin is not the pod; the resolved src must point at the instance.
    expect(frame.getAttribute('src')).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc/preview/5173/'
    );
  });

  it('leaves a server-relative src alone when no API base url is given', () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, src: '/preview/5173/', title: 'Live App' }}
        conversationId="conv1"
      />
    );
    // Local dev serves the SPA and the API from the same origin.
    expect(screen.getByTitle('Live App').getAttribute('src')).toBe('/preview/5173/');
  });

  it('accepts opaque-origin messages from the panel frame and replies to it', async () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, src: '/preview/5173/', title: 'Live App' }}
        conversationId="conv1"
        apiBaseUrl="https://fleet.gptme.ai/api/v1/instances/abc"
      />
    );
    const frame = screen.getByTitle('Live App') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // The sandbox is `allow-scripts`, so the frame is opaque and speaks as
    // "null". The reply targets "*" because a concrete origin can never match
    // an opaque frame — but it is still bound to this exact contentWindow.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv1' } },
      '*'
    );
  });

  it('replies to the resolved API origin when the frame keeps its origin', async () => {
    render(
      <SandboxedIframePanel
        descriptor={{
          ...baseDescriptor,
          src: '/preview/5173/',
          sandbox: ['allow-same-origin'],
          title: 'Live App',
        }}
        conversationId="conv1"
        apiBaseUrl="https://fleet.gptme.ai/api/v1/instances/abc"
      />
    );
    const frame = screen.getByTitle('Live App') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'https://fleet.gptme.ai', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv1' } },
      'https://fleet.gptme.ai'
    );
  });

  it('does not re-bootstrap after gptme:ready fires a second time (bootstrap-once-per-document)', async () => {
    // A document that posts `gptme:ready` twice (without reloading) must not
    // receive the bootstrap payload twice.
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, title: 'Bootstrap Once Test' }}
        conversationId="conv-once"
      />
    );
    const frame = screen.getByTitle('Bootstrap Once Test') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // First ready — bootstrap fires.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));

    // Second ready (navigation) — bootstrap must NOT fire again.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledTimes(1);
  });

  it('bootstrap guard persists after a conversationId re-render (dep change without src change)', async () => {
    // If the effect resets the guard on every re-run, a dep change such as a
    // conversationId update would allow a navigated attacker document in the
    // same opaque-origin frame to receive a second bootstrap payload.
    const { rerender } = render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, title: 'Guard Persist Test' }}
        conversationId="conv-guard-1"
      />
    );
    const frame = screen.getByTitle('Guard Persist Test') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // First ready — bootstrap fires once.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));

    // Re-render with a new conversationId (same src — no frame navigation).
    act(() => {
      rerender(
        <SandboxedIframePanel
          descriptor={{ ...baseDescriptor, title: 'Guard Persist Test' }}
          conversationId="conv-guard-2"
        />
      );
    });

    // Second ready in the same frame after re-render — guard must still hold.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledTimes(1);
  });

  it('re-bootstraps after the frame reloads (new document, same src)', async () => {
    // A reload replaces the document in the same iframe. The new document needs
    // the bootstrap payload too, so the guard must re-arm on the load event —
    // otherwise every reloaded panel loses its conversation_id permanently.
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, title: 'Reload Test' }}
        conversationId="conv-reload"
      />
    );
    const frame = screen.getByTitle('Reload Test') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));

    // Reload: a fresh document finishes loading in the same frame.
    act(() => {
      fireEvent.load(frame);
    });
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(2));
    expect(postMessage).toHaveBeenNthCalledWith(
      2,
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-reload' } },
      '*'
    );
  });

  it('re-bootstraps when the src changes to one with a different origin policy', async () => {
    // The message listener must be rebuilt when `src` changes, otherwise the
    // new document is validated against the previous src's origin/opacity and
    // never receives the bootstrap payload.
    const { rerender } = render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, title: 'Src Change Test' }}
        conversationId="conv-src"
      />
    );
    let frame = screen.getByTitle('Src Change Test') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // First document: opaque origin (allow-scripts without allow-same-origin).
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));

    // New src keeps the frame's real origin, so the listener must now accept
    // the concrete origin instead of the stale opaque "null".
    rerender(
      <SandboxedIframePanel
        descriptor={{
          ...baseDescriptor,
          title: 'Src Change Test',
          src: 'http://localhost:9090',
          sandbox: ['allow-same-origin'],
        }}
        conversationId="conv-src"
      />
    );
    frame = screen.getByTitle('Src Change Test') as HTMLIFrameElement;
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });
    act(() => {
      fireEvent.load(frame);
    });

    emitFromIframe(frame, 'http://localhost:9090', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(2));
    expect(postMessage).toHaveBeenNthCalledWith(
      2,
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-src' } },
      'http://localhost:9090'
    );
  });

  it('bootstraps a new document that posts ready before its load event fires', async () => {
    // A cached/same-origin document can post `gptme:ready` before `load`.
    // The guard must be re-armed on the src change itself, not only on load.
    const { rerender } = render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, title: 'Race Test' }}
        conversationId="conv-race"
      />
    );
    let frame = screen.getByTitle('Race Test') as HTMLIFrameElement;
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));

    rerender(
      <SandboxedIframePanel
        descriptor={{
          ...baseDescriptor,
          title: 'Race Test',
          src: 'http://localhost:9090',
          sandbox: ['allow-same-origin'],
        }}
        conversationId="conv-race"
      />
    );
    frame = screen.getByTitle('Race Test') as HTMLIFrameElement;
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // Deliberately no `load` event — the new document speaks first.
    emitFromIframe(frame, 'http://localhost:9090', { type: 'gptme:ready' });
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(2));
  });

  it('treats an empty sandbox as opaque (present-but-empty attribute)', async () => {
    // `sandbox: []` renders as `sandbox=""`, which still sandboxes the frame;
    // it therefore speaks as "null" and is answered with targetOrigin "*".
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, sandbox: [], title: 'Empty Sandbox Test' }}
        conversationId="conv-empty"
      />
    );
    const frame = screen.getByTitle('Empty Sandbox Test') as HTMLIFrameElement;
    expect(frame.getAttribute('sandbox')).toBe('');
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-empty' } },
      '*'
    );
  });

  it('still blocks a foreign origin when an API base url is set', () => {
    render(
      <SandboxedIframePanel
        descriptor={{ ...baseDescriptor, src: 'https://evil.example.com' }}
        conversationId="conv1"
        apiBaseUrl="https://fleet.gptme.ai"
      />
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByTitle('Webapp Preview')).not.toBeInTheDocument();
  });
});
