import { mergeUserRuns } from '../messageUtils';

describe('mergeUserRuns', () => {
  const roles = ['user', 'user', 'assistant', 'user', 'system', 'user', 'user', 'user'];
  const roleAt = (index: number) => roles[index];

  it('keeps one row per run of user messages, keyed by its first', () => {
    const { rows, runs } = mergeUserRuns([0, 1, 2, 3, 4, 5, 6, 7], roleAt);
    expect(rows).toEqual([0, 2, 3, 4, 5]);
    expect([...runs]).toEqual([
      [0, [0, 1]],
      [5, [5, 6, 7]],
    ]);
  });

  it('runs over hidden rows: only the visible ones count', () => {
    // 4 (system) hidden: 3 and 5 become adjacent
    const { rows, runs } = mergeUserRuns([2, 3, 5], roleAt);
    expect(rows).toEqual([2, 3]);
    expect(runs.get(3)).toEqual([3, 5]);
  });
});
