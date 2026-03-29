export type DateGroup = 'Today' | 'Yesterday' | 'This Week' | 'This Month' | 'Older';

/**
 * Categorize a date into a display group for conversation list headers.
 */
export function getDateGroup(date: Date, now: Date = new Date()): DateGroup {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);
  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfWeek.getDate() - 6); // "This Week" covers days 2-6 ago (Today/Yesterday handled first)
  const startOfMonth = new Date(startOfToday);
  startOfMonth.setDate(startOfMonth.getDate() - 29); // "This Month" covers days 7-29 ago (This Week handled first)

  if (date >= startOfToday) return 'Today';
  if (date >= startOfYesterday) return 'Yesterday';
  if (date >= startOfWeek) return 'This Week';
  if (date >= startOfMonth) return 'This Month';
  return 'Older';
}

/**
 * Group items by date period, preserving order within groups.
 * Returns groups in chronological order (most recent first).
 */
export function groupByDate<T>(
  items: T[],
  getTimestamp: (item: T) => number,
  now: Date = new Date()
): { group: DateGroup; items: T[] }[] {
  const groupOrder: DateGroup[] = ['Today', 'Yesterday', 'This Week', 'This Month', 'Older'];
  const groups = new Map<DateGroup, T[]>();

  for (const item of items) {
    const date = new Date(getTimestamp(item) * 1000);
    const group = getDateGroup(date, now);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push(item);
  }

  return groupOrder.filter((g) => groups.has(g)).map((g) => ({ group: g, items: groups.get(g)! }));
}

export function getRelativeTimeString(date: Date): string {
  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffInSeconds < 60) {
    return 'just now';
  }

  const diffInMinutes = Math.floor(diffInSeconds / 60);
  if (diffInMinutes < 60) {
    return `${diffInMinutes}min ago`;
  }

  const diffInHours = Math.floor(diffInMinutes / 60);
  if (diffInHours < 24) {
    return `${diffInHours}h ago`;
  }

  const diffInDays = Math.floor(diffInHours / 24);
  if (diffInDays === 1) {
    return 'yesterday';
  }
  if (diffInDays < 7) {
    return `${diffInDays}d ago`;
  }

  const diffInWeeks = Math.floor(diffInDays / 7);
  if (diffInWeeks < 4) {
    return `${diffInWeeks}w ago`;
  }

  return date.toLocaleDateString();
}
