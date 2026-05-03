import {
  getEmbeddedParentOrigin,
  isEmbeddedContextEventAllowed,
  parseEmbeddedContextMessage,
} from '@/lib/embeddedContext';

describe('EmbeddedContext helpers', () => {
  it('derives the parent origin from document.referrer', () => {
    expect(getEmbeddedParentOrigin('https://gptme.ai/chat')).toBe('https://gptme.ai');
    expect(getEmbeddedParentOrigin('')).toBeNull();
    expect(getEmbeddedParentOrigin('not-a-url')).toBeNull();
  });

  it('parses valid embedded context messages', () => {
    expect(
      parseEmbeddedContextMessage({
        type: 'gptme-host:embedded-context',
        payload: {
          menuItems: [
            {
              kind: 'link',
              id: 'dashboard',
              label: 'Dashboard',
              href: '/account',
              section: 'General',
            },
            {
              kind: 'action',
              id: 'sign-out',
              label: 'Sign out',
              action: 'sign_out',
              destructive: true,
            },
          ],
        },
      })
    ).toEqual([
      { kind: 'link', id: 'dashboard', label: 'Dashboard', href: '/account', section: 'General' },
      { kind: 'action', id: 'sign-out', label: 'Sign out', action: 'sign_out', destructive: true },
    ]);
  });

  it('rejects malformed embedded context messages', () => {
    expect(
      parseEmbeddedContextMessage({
        type: 'gptme-host:embedded-context',
        payload: {
          menuItems: [{ kind: 'link', id: 'dashboard', label: 'Dashboard' }],
        },
      })
    ).toBeNull();

    expect(
      parseEmbeddedContextMessage({
        type: 'wrong-type',
        payload: {
          menuItems: [],
        },
      })
    ).toBeNull();
  });

  it('rejects unsafe href protocols in link menu items', () => {
    const makeMsg = (href: string) => ({
      type: 'gptme-host:embedded-context',
      payload: { menuItems: [{ kind: 'link', id: 'x', label: 'X', href }] },
    });

    // Safe: relative paths and http/https
    expect(parseEmbeddedContextMessage(makeMsg('/account'))).not.toBeNull();
    expect(parseEmbeddedContextMessage(makeMsg('./relative'))).not.toBeNull();
    expect(parseEmbeddedContextMessage(makeMsg('#anchor'))).not.toBeNull();
    expect(parseEmbeddedContextMessage(makeMsg('https://example.com'))).not.toBeNull();
    expect(parseEmbeddedContextMessage(makeMsg('http://localhost:3000'))).not.toBeNull();

    // Unsafe: javascript: and other non-http protocols
    expect(parseEmbeddedContextMessage(makeMsg('javascript:alert(1)'))).toBeNull();
    expect(
      parseEmbeddedContextMessage(makeMsg('data:text/html,<script>alert(1)</script>'))
    ).toBeNull();
    expect(parseEmbeddedContextMessage(makeMsg('vbscript:msgbox(1)'))).toBeNull();
  });

  it('only accepts messages from the parent origin when known', () => {
    expect(
      isEmbeddedContextEventAllowed('https://gptme.ai', 'https://gptme.ai', 'http://localhost:5173')
    ).toBe(true);
    expect(
      isEmbeddedContextEventAllowed(
        'https://evil.example',
        'https://gptme.ai',
        'http://localhost:5173'
      )
    ).toBe(false);
  });

  it('falls back to same-origin messages when no parent origin is known', () => {
    expect(
      isEmbeddedContextEventAllowed('http://localhost:5173', null, 'http://localhost:5173')
    ).toBe(true);
    expect(isEmbeddedContextEventAllowed('https://gptme.ai', null, 'http://localhost:5173')).toBe(
      false
    );
  });

  it('can accept a first validated bootstrap message from an unknown parent origin', () => {
    expect(
      isEmbeddedContextEventAllowed('https://gptme.ai', null, 'http://localhost:5173', {
        allowUnknownParentOrigin: true,
      })
    ).toBe(true);
  });
});
