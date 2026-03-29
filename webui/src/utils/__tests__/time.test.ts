import { getDateGroup, groupByDate } from '../time';

describe('getDateGroup', () => {
  // Use a fixed "now" so tests are deterministic
  const now = new Date('2026-03-29T14:00:00');

  it('returns Today for same-day timestamps', () => {
    expect(getDateGroup(new Date('2026-03-29T00:00:01'), now)).toBe('Today');
    expect(getDateGroup(new Date('2026-03-29T13:59:59'), now)).toBe('Today');
  });

  it('returns Yesterday for previous day', () => {
    expect(getDateGroup(new Date('2026-03-28T10:00:00'), now)).toBe('Yesterday');
    expect(getDateGroup(new Date('2026-03-28T23:59:59'), now)).toBe('Yesterday');
  });

  it('returns This Week for 2-6 days ago', () => {
    expect(getDateGroup(new Date('2026-03-27T12:00:00'), now)).toBe('This Week');
    expect(getDateGroup(new Date('2026-03-24T00:00:00'), now)).toBe('This Week');
    expect(getDateGroup(new Date('2026-03-23T00:00:00'), now)).toBe('This Week');
  });

  it('returns This Month for 7-29 days ago', () => {
    expect(getDateGroup(new Date('2026-03-22T12:00:00'), now)).toBe('This Month');
    expect(getDateGroup(new Date('2026-03-01T00:00:00'), now)).toBe('This Month');
  });

  it('returns Older for 30+ days ago', () => {
    expect(getDateGroup(new Date('2026-02-27T00:00:00'), now)).toBe('Older');
    expect(getDateGroup(new Date('2025-01-01T00:00:00'), now)).toBe('Older');
  });

  it('handles midnight boundary correctly', () => {
    const midnight = new Date('2026-03-29T00:00:00');
    expect(getDateGroup(midnight, now)).toBe('Today');
    // One second before midnight = yesterday
    expect(getDateGroup(new Date('2026-03-28T23:59:59'), now)).toBe('Yesterday');
  });
});

describe('groupByDate', () => {
  const now = new Date('2026-03-29T14:00:00');

  interface Item {
    id: string;
    ts: number;
  }

  const toUnix = (d: string) => new Date(d).getTime() / 1000;

  it('groups items into date categories', () => {
    const items: Item[] = [
      { id: 'a', ts: toUnix('2026-03-29T12:00:00') },
      { id: 'b', ts: toUnix('2026-03-28T10:00:00') },
      { id: 'c', ts: toUnix('2026-03-25T10:00:00') },
      { id: 'd', ts: toUnix('2026-03-10T10:00:00') },
      { id: 'e', ts: toUnix('2026-01-01T10:00:00') },
    ];

    const groups = groupByDate(items, (i) => i.ts, now);
    expect(groups.map((g) => g.group)).toEqual([
      'Today',
      'Yesterday',
      'This Week',
      'This Month',
      'Older',
    ]);
    expect(groups[0].items.map((i) => i.id)).toEqual(['a']);
    expect(groups[1].items.map((i) => i.id)).toEqual(['b']);
    expect(groups[2].items.map((i) => i.id)).toEqual(['c']);
    expect(groups[3].items.map((i) => i.id)).toEqual(['d']);
    expect(groups[4].items.map((i) => i.id)).toEqual(['e']);
  });

  it('returns empty array for empty input', () => {
    expect(groupByDate<Item>([], (i) => i.ts, now)).toEqual([]);
  });

  it('omits empty groups', () => {
    const items: Item[] = [
      { id: 'a', ts: toUnix('2026-03-29T12:00:00') },
      { id: 'b', ts: toUnix('2026-01-01T10:00:00') },
    ];
    const groups = groupByDate(items, (i) => i.ts, now);
    const groupNames = groups.map((g) => g.group);
    expect(groupNames).toEqual(['Today', 'Older']);
    expect(groupNames).not.toContain('Yesterday');
  });

  it('preserves order within groups', () => {
    const items: Item[] = [
      { id: 'first', ts: toUnix('2026-03-29T13:00:00') },
      { id: 'second', ts: toUnix('2026-03-29T10:00:00') },
      { id: 'third', ts: toUnix('2026-03-29T08:00:00') },
    ];
    const groups = groupByDate(items, (i) => i.ts, now);
    expect(groups).toHaveLength(1);
    expect(groups[0].items.map((i) => i.id)).toEqual(['first', 'second', 'third']);
  });

  it('groups are returned in chronological order (most recent first)', () => {
    // Items in reverse order
    const items: Item[] = [
      { id: 'old', ts: toUnix('2025-01-01T10:00:00') },
      { id: 'new', ts: toUnix('2026-03-29T12:00:00') },
    ];
    const groups = groupByDate(items, (i) => i.ts, now);
    expect(groups[0].group).toBe('Today');
    expect(groups[1].group).toBe('Older');
  });

  it('handles multiple items in same group', () => {
    const items: Item[] = [
      { id: 'a', ts: toUnix('2026-03-29T13:00:00') },
      { id: 'b', ts: toUnix('2026-03-29T12:00:00') },
      { id: 'c', ts: toUnix('2026-03-29T11:00:00') },
    ];
    const groups = groupByDate(items, (i) => i.ts, now);
    expect(groups).toHaveLength(1);
    expect(groups[0].group).toBe('Today');
    expect(groups[0].items).toHaveLength(3);
  });
});
