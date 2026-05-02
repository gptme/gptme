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
});
