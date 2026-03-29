import type { Message } from '@/types/conversation';

export interface ForkInfo {
  /** Branch names available at this fork point, sorted by timestamp of next message */
  branchNames: string[];
  /** Index of current branch in branchNames */
  currentIndex: number;
}

/**
 * Compute fork points across branches.
 * A fork point is a message index where the NEXT message differs across branches.
 * Returns a Map from message index to ForkInfo.
 *
 * Algorithm (ported from old Vue UI):
 * For each message at index i on the current branch:
 *   1. Start with branches = [currentBranch]
 *   2. For each other branch: if message at i+1 has a different timestamp, add that branch
 *   3. Sort branches by timestamp of their i+1 message
 *   4. Only include in result if branches.length > 1
 */
export function computeForkPoints(
  currentBranch: string,
  branches: Record<string, Message[]>
): Map<number, ForkInfo> {
  const result = new Map<number, ForkInfo>();

  const currentLog = branches[currentBranch];
  if (!currentLog) return result;

  const branchNames = Object.keys(branches);
  if (branchNames.length <= 1) return result;

  for (let i = 0; i < currentLog.length; i++) {
    const forkBranches: string[] = [currentBranch];
    const nextMsgCurrent = currentLog[i + 1];

    for (const branch of branchNames) {
      if (branch === currentBranch) continue;
      const branchLog = branches[branch];
      if (!branchLog) continue;

      const nextMsgBranch = branchLog[i + 1];

      // Fork if: current has a next but branch doesn't (or vice versa),
      // or both have a next but with different timestamps
      if (nextMsgCurrent && nextMsgBranch) {
        if (nextMsgCurrent.timestamp !== nextMsgBranch.timestamp) {
          forkBranches.push(branch);
        }
      } else if (nextMsgCurrent || nextMsgBranch) {
        // One branch is longer than the other at this point
        forkBranches.push(branch);
      }
    }

    if (forkBranches.length > 1) {
      // Sort by timestamp of next message (earlier first)
      forkBranches.sort((a, b) => {
        const aNext = branches[a]?.[i + 1];
        const bNext = branches[b]?.[i + 1];
        if (!aNext) return 1;
        if (!bNext) return -1;
        return (
          new Date(aNext.timestamp || '').getTime() - new Date(bNext.timestamp || '').getTime()
        );
      });

      result.set(i, {
        branchNames: forkBranches,
        currentIndex: forkBranches.indexOf(currentBranch),
      });
    }
  }

  return result;
}
