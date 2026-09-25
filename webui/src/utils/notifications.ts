/**
 * System notification utilities for desktop notifications.
 *
 * When running inside Tauri, the browser Notification API may be unreliable in
 * the embedded WebView (it can no-op silently depending on platform and OS
 * permission state).  We therefore prefer the native Tauri notification plugin
 * (`tauri-plugin-notification`) whenever `window.__TAURI_INTERNALS__` is
 * present, falling back to the standard browser API for plain-web usage.
 */

import { isTauriEnvironment, invokeTauri } from './tauri';

type NotificationPermission = 'default' | 'granted' | 'denied';

let notificationPermission: NotificationPermission = 'default';

// ---------------------------------------------------------------------------
// Tauri-native notification helpers (no extra npm package — uses invokeTauri)
// ---------------------------------------------------------------------------

async function tauriIsPermissionGranted(): Promise<boolean> {
  try {
    return await invokeTauri<boolean>('plugin:notification|is_permission_granted');
  } catch {
    return false;
  }
}

async function tauriRequestPermission(): Promise<NotificationPermission> {
  try {
    return await invokeTauri<NotificationPermission>('plugin:notification|request_permission');
  } catch {
    return 'denied';
  }
}

/**
 * Show a notification via the Tauri native plugin.
 * Returns true if the notification was sent, false on any error/denial.
 */
async function showTauriNotification(
  title: string,
  body?: string,
  options?: { icon?: string; tag?: string; silent?: boolean }
): Promise<boolean> {
  try {
    const granted = await tauriIsPermissionGranted();
    if (!granted) {
      const perm = await tauriRequestPermission();
      if (perm !== 'granted') {
        console.log('Tauri notification permission denied');
        return false;
      }
    }
    // tauri-plugin-notification v2 Options supports icon/silent; it has no
    // `tag` field, so browser-API tag dedup has no native equivalent — calls
    // relying on tag dedup may stack instead of replace on desktop.
    await invokeTauri('plugin:notification|notify', {
      notification: {
        title,
        body,
        ...(options?.icon ? { icon: options.icon } : {}),
        ...(options?.silent !== undefined ? { silent: options.silent } : {}),
      },
    });
    // Record the native grant only after the notification actually went out,
    // so a failed invoke leaves the module state 'default' and the browser-API
    // fallback below runs its own permission request. Denial is deliberately
    // NOT recorded: the browser-API fallback still gets its own chance.
    notificationPermission = 'granted';
    console.log('Tauri native notification shown:', title);
    return true;
  } catch (error) {
    console.error('Tauri native notification failed, will fall back:', error);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Browser Notification API helpers
// ---------------------------------------------------------------------------

/**
 * Request notification permission from the browser
 */
export async function requestNotificationPermission(): Promise<NotificationPermission> {
  if (!('Notification' in window)) {
    console.warn('This browser does not support notifications');
    return 'denied';
  }

  if (Notification.permission !== 'default') {
    notificationPermission = Notification.permission as NotificationPermission;
    return notificationPermission;
  }

  try {
    const permission = await Notification.requestPermission();
    notificationPermission = permission as NotificationPermission;
    console.log('Notification permission:', permission);
    return permission as NotificationPermission;
  } catch (error) {
    console.error('Failed to request notification permission:', error);
    notificationPermission = 'denied';
    return 'denied';
  }
}

/**
 * Check if notifications are supported and permitted
 */
export function isNotificationSupported(): boolean {
  return 'Notification' in window && notificationPermission === 'granted';
}

/**
 * Check if the current tab is active/visible
 */
export function isTabActive(): boolean {
  return !document.hidden;
}

/**
 * Show a system notification.
 *
 * In Tauri: attempts the native plugin first; falls back to browser API on
 * failure so the web-only path is unchanged.
 * In browser: uses the standard Notification API directly.
 */
export async function showNotification(
  title: string,
  options?: {
    body?: string;
    icon?: string;
    tag?: string;
    requireInactive?: boolean;
  }
): Promise<Notification | null> {
  // Only show when window is not in focus (when requireInactive is set)
  if (options?.requireInactive && isTabActive()) {
    console.log('Tab is active, skipping notification');
    return null;
  }

  // Try Tauri native notifications first — avoids WebView permission quirks
  if (isTauriEnvironment()) {
    const sent = await showTauriNotification(title, options?.body, options);
    if (sent) return null; // Native notification sent; no browser Notification object
    // Fall through to browser API on failure
  }

  // Browser Notification API path
  if (!('Notification' in window)) {
    console.log('Notifications not supported in this browser');
    return null;
  }

  if (notificationPermission === 'default') {
    await requestNotificationPermission();
  }

  if (notificationPermission !== 'granted') {
    console.log('Notification permission denied');
    return null;
  }

  try {
    const notification = new Notification(title, {
      body: options?.body,
      icon: options?.icon || '/favicon.png',
      tag: options?.tag,
      badge: '/favicon.png',
      silent: false,
    });

    setTimeout(() => {
      notification.close();
    }, 5000);

    notification.onclick = () => {
      window.focus();
      notification.close();
    };

    console.log('Browser notification shown:', title);
    return notification;
  } catch (error) {
    console.error('Failed to show notification:', error);
    return null;
  }
}

/**
 * Show notification when agent completes generation
 */
export async function notifyGenerationComplete(conversationName?: string): Promise<void> {
  const title = 'gptme - Response Complete';
  const body = conversationName
    ? `Agent finished responding in "${conversationName}"`
    : 'Agent finished responding';

  await showNotification(title, {
    body,
    tag: 'generation-complete',
    requireInactive: true,
  });
}

/**
 * Show notification when tool confirmation is needed
 */
export async function notifyToolConfirmation(
  toolName?: string,
  conversationName?: string
): Promise<void> {
  const title = 'gptme - Confirmation Required';
  const body = toolName
    ? `Tool "${toolName}" needs confirmation${conversationName ? ` in "${conversationName}"` : ''}`
    : `Tool confirmation required${conversationName ? ` in "${conversationName}"` : ''}`;

  await showNotification(title, {
    body,
    tag: 'tool-confirmation',
    requireInactive: true,
  });
}

/**
 * Get the current notification permission status
 */
export function getNotificationPermission(): NotificationPermission {
  return notificationPermission;
}
