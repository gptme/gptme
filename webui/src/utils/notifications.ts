/**
 * System notification utilities for desktop notifications
 */

type NotificationPermission = 'default' | 'granted' | 'denied';

let notificationPermission: NotificationPermission = 'default';

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
 * Show a system notification
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
  // Check if notifications are supported
  if (!('Notification' in window)) {
    console.log('Notifications not supported in this browser');
    return null;
  }

  // Request permission if needed
  if (notificationPermission === 'default') {
    await requestNotificationPermission();
  }

  // Check if we have permission
  if (notificationPermission !== 'granted') {
    console.log('Notification permission denied');
    return null;
  }

  // Check if we should only show when tab is inactive
  if (options?.requireInactive && isTabActive()) {
    console.log('Tab is active, skipping notification');
    return null;
  }

  try {
    const notification = new Notification(title, {
      body: options?.body,
      icon: options?.icon || '/favicon.png',
      tag: options?.tag,
      badge: '/favicon.png',
      silent: false, // Allow sound from the notification itself
    });

    // Auto-close notification after a few seconds
    setTimeout(() => {
      notification.close();
    }, 5000);

    // Handle click to focus the window/tab
    notification.onclick = () => {
      window.focus();
      notification.close();
    };

    console.log('Notification shown:', title);
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
    requireInactive: true, // Only show when tab is not active
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
    requireInactive: true, // Only show when tab is not active - chime and UI are enough when active
  });
}

/**
 * Get the current notification permission status
 */
export function getNotificationPermission(): NotificationPermission {
  return notificationPermission;
}
