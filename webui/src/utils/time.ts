export type DateGroup = string;

// Well-known groups that always appear in this order before monthly groups
const RECENT_GROUPS = ['Today', 'Yesterday', 'This Week', 'This Month'] as const;

/**
 * Format a month group label, e.g. "March 2026" or "March" for the current year.
 */
function formatMonthGroup(date: Date, now: Date): string {
  const month = date.toLocaleString('default', { month: 'long' });
  if (date.getFullYear() === now.getFullYear()) {
    return month;
  }
  return `${month} ${date.getFullYear()}`;
}

/**
 * Categorize a date into a display group for conversation list headers.
 * Returns well-known groups for recent dates and month names for older ones.
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
  return formatMonthGroup(date, now);
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
  const groups = new Map<DateGroup, T[]>();
  // Track the order in which month groups appear (for sorting)
  const monthGroupDates = new Map<string, Date>();

  for (const item of items) {
    const date = new Date(getTimestamp(item) * 1000);
    const group = getDateGroup(date, now);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push(item);
    // Track representative date for month groups (for ordering)
    if (!RECENT_GROUPS.includes(group as (typeof RECENT_GROUPS)[number])) {
      if (!monthGroupDates.has(group) || date > monthGroupDates.get(group)!) {
        monthGroupDates.set(group, date);
      }
    }
  }

  // Build ordered result: recent groups first (in fixed order), then month groups (newest first)
  const result: { group: DateGroup; items: T[] }[] = [];

  for (const g of RECENT_GROUPS) {
    if (groups.has(g)) {
      result.push({ group: g, items: groups.get(g)! });
    }
  }

  // Sort month groups by date (most recent first)
  const sortedMonthGroups = [...monthGroupDates.entries()]
    .sort(([, a], [, b]) => b.getTime() - a.getTime())
    .map(([group]) => group);

  for (const g of sortedMonthGroups) {
    result.push({ group: g, items: groups.get(g)! });
  }

  return result;
}

/**
 * Format a Date as an ISO date string (YYYY-MM-DD) in local time.
 */
export function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
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
