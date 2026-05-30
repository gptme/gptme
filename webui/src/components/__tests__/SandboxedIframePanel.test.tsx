import { act, render, screen, waitFor } from '@testing-library/react';
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

    emitFromIframe(frame, 'http://localhost:8080', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-abc' } },
      'http://localhost:8080'
    );
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

    emitFromIframe(frame, 'http://localhost:8080', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'conv-abc', artifact_id: 'art_01' } },
      'http://localhost:8080'
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

    emitFromIframe(frame, 'http://localhost:8080', { type: 'gptme:unknown' });

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

    emitFromIframe(frame, 'http://localhost:8080', { type: 'gptme:ready' });

    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'gptme:bootstrap', payload: { conversation_id: 'real-conv-id' } },
      'http://localhost:8080'
    );
  });

  it('rejects postMessage when expectedOrigin cannot be resolved (fail-closed)', async () => {
    // Simulate an iframe whose src produces a null origin by patching the policy.
    // We do this indirectly: use a descriptor with an opaque-origin data: src that
    // passes the allowlist check but returns null from iframeSrcOrigin. The
    // simplest approach is to render with a server-relative src that resolves fine
    // but then emit from 'null' (the serialised opaque origin browsers send for
    // sandboxed iframes without allow-same-origin).
    render(<SandboxedIframePanel descriptor={baseDescriptor} conversationId="conv-abc" />);
    const frame = getIframe();
    const postMessage = jest.fn();
    Object.defineProperty(frame, 'contentWindow', { value: { postMessage }, configurable: true });

    // 'null' is what browsers serialize as the origin for opaque origins.
    emitFromIframe(frame, 'null', { type: 'gptme:ready' });

    await new Promise((r) => setTimeout(r, 10));
    expect(postMessage).not.toHaveBeenCalled();
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
      emitFromIframe(frame, 'http://localhost:8080', {
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
});
