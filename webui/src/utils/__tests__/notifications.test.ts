import { jest } from '@jest/globals';

const mockInvokeTauri = jest.fn<(command: string, args?: Record<string, unknown>) => Promise<unknown>>();
const mockIsTauriEnvironment = jest.fn<() => boolean>(() => true);

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: mockIsTauriEnvironment,
  invokeTauri: mockInvokeTauri,
}));

import { showNotification } from '@/utils/notifications';

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

  it('forwards icon and silent, which the plugin does support', async () => {
    await showNotification('Done', { body: 'Finished', icon: '/icon.png' });

    const notifyCall = mockInvokeTauri.mock.calls.find(
      (call) => call[0] === 'plugin:notification|notify'
    );
    expect(notifyCall?.[1]).toEqual({
      options: { title: 'Done', body: 'Finished', icon: '/icon.png' },
    });
  });
});
