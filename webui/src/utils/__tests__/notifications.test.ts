import { jest } from '@jest/globals';

const mockInvokeTauri =
  jest.fn<(command: string, args?: Record<string, unknown>) => Promise<unknown>>();
const mockIsTauriEnvironment = jest.fn<() => boolean>(() => true);

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: mockIsTauriEnvironment,
  invokeTauri: mockInvokeTauri,
}));

import { showNotification } from '@/utils/notifications';

const originalHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');

function setDocumentHidden(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    value: hidden,
  });
}

afterEach(() => {
  jest.restoreAllMocks();
  if (originalHiddenDescriptor) {
    Object.defineProperty(document, 'hidden', originalHiddenDescriptor);
  } else {
    Reflect.deleteProperty(document, 'hidden');
  }
});

describe('showNotification — Tauri native path', () => {
  beforeEach(() => {
    mockInvokeTauri.mockReset();
    mockIsTauriEnvironment.mockReturnValue(true);
    // is_permission_granted resolves true; notify resolves undefined.
    mockInvokeTauri.mockResolvedValue(true);
  });

  it('sends plugin:notification|notify with an `options` payload key', async () => {
    await showNotification('Tool confirmation', { body: 'Run shell?' });

    const notifyCall = mockInvokeTauri.mock.calls.find(
      (call) => call[0] === 'plugin:notification|notify'
    );
    expect(notifyCall).toBeDefined();
    // The Rust command is `notify(_app, notification: State<..>, options:
    // NotificationData)` — the payload key is `options` (the plugin's own
    // init-iife.js shim invokes it the same way). A `notification` wrapper
    // fails deserialization and silently degrades to the browser fallback.
    expect(notifyCall?.[1]).toEqual({
      options: { title: 'Tool confirmation', body: 'Run shell?' },
    });
  });

  it('forwards icon, which the plugin does support', async () => {
    await showNotification('Done', { body: 'Finished', icon: '/icon.png' });

    const notifyCall = mockInvokeTauri.mock.calls.find(
      (call) => call[0] === 'plugin:notification|notify'
    );
    expect(notifyCall?.[1]).toEqual({
      options: { title: 'Done', body: 'Finished', icon: '/icon.png' },
    });
  });

  it('notifies when the Tauri window is visible but unfocused', async () => {
    setDocumentHidden(false);
    jest.spyOn(document, 'hasFocus').mockReturnValue(false);

    await showNotification('Tool confirmation', { requireInactive: true });

    expect(mockInvokeTauri).toHaveBeenCalledWith('plugin:notification|notify', {
      options: { title: 'Tool confirmation' },
    });
  });

  it('skips notifications while the window is visible and focused', async () => {
    setDocumentHidden(false);
    jest.spyOn(document, 'hasFocus').mockReturnValue(true);
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined);

    await showNotification('Tool confirmation', { requireInactive: true });

    expect(mockInvokeTauri).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith('Tab is active, skipping notification', {
      documentHidden: false,
      documentHasFocus: true,
    });
  });
});

describe('showNotification — native grant does not leak into the browser path', () => {
  it('still requests browser permission when a later native call fails', async () => {
    const requestPermission = jest.fn(async () => 'denied' as NotificationPermission);
    const NotificationCtor = jest.fn();
    Object.assign(NotificationCtor, { permission: 'default', requestPermission });
    Object.defineProperty(window, 'Notification', {
      value: NotificationCtor,
      configurable: true,
      writable: true,
    });
    mockIsTauriEnvironment.mockReturnValue(true);

    // 1st call: native path succeeds.
    mockInvokeTauri.mockReset();
    mockInvokeTauri.mockResolvedValue(true);
    await showNotification('First');
    expect(NotificationCtor).not.toHaveBeenCalled();

    // 2nd call: native path throws → falls back to the browser API, which must
    // ask for its own permission rather than assume the native grant covers it.
    mockInvokeTauri.mockReset();
    mockInvokeTauri.mockRejectedValue(new Error('plugin unavailable'));
    await showNotification('Second');

    expect(requestPermission).toHaveBeenCalled();
    expect(NotificationCtor).not.toHaveBeenCalled();
  });
});
