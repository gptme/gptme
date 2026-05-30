import { render, screen, waitFor } from '@testing-library/react';
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
