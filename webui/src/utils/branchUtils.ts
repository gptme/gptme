import type { Message } from '@/types/conversation';

export interface ForkInfo {
  /** Branch names available at this fork point, sorted by timestamp of diverging message */
  branchNames: string[];
  /** Index of current branch in branchNames */
  currentIndex: number;
}

/**
 * Compute fork points across branches.
 *
 * For each other branch, find the FIRST message index where it diverges from the
 * current branch (different timestamp or different length). The fork indicator is
 * placed at the last COMMON message (index - 1), so the user sees it on the message
 * just before the conversation splits.
 *
 * Each branch only appears at its first divergence point — not at every subsequent
 * message (which would all differ because they're on separate branches).
 */
export function computeForkPoints(
  currentBranch: string,
  branches: Record<string, Message[]>
): Map<number, ForkInfo> {
  const currentLog = branches[currentBranch];
  if (!currentLog) return new Map();

  const branchNames = Object.keys(branches);
  if (branchNames.length <= 1) return new Map();

  // For each other branch, find the first divergence index
  const divergenceMap = new Map<number, string[]>(); // forkIndex -> branch names

  for (const branch of branchNames) {
    if (branch === currentBranch) continue;
    const otherLog = branches[branch];
    if (!otherLog) continue;

    const maxLen = Math.max(currentLog.length, otherLog.length);
    for (let i = 0; i < maxLen; i++) {
      const currentMsg = currentLog[i];
      const otherMsg = otherLog[i];

      // Divergence: one is missing, or timestamps differ
      if (!currentMsg || !otherMsg || currentMsg.timestamp !== otherMsg.timestamp) {
        // Fork point is the last common message (i-1), clamped to 0
        const forkIndex = Math.max(0, i - 1);
        if (!divergenceMap.has(forkIndex)) {
          divergenceMap.set(forkIndex, []);
        }
        divergenceMap.get(forkIndex)!.push(branch);
        break;
      }
    }
  }

  // Build ForkInfo for each fork point
  const result = new Map<number, ForkInfo>();
  for (const [forkIndex, otherBranches] of divergenceMap) {
    const allBranches = [currentBranch, ...otherBranches];

    // Sort by timestamp of the diverging message (the one after the fork)
    allBranches.sort((a, b) => {
      const aMsg = branches[a]?.[forkIndex + 1];
      const bMsg = branches[b]?.[forkIndex + 1];
      if (!aMsg) return 1;
      if (!bMsg) return -1;
      return (
        new Date(aMsg.timestamp || '').getTime() - new Date(bMsg.timestamp || '').getTime()
      );
    });

    result.set(forkIndex, {
      branchNames: allBranches,
      currentIndex: allBranches.indexOf(currentBranch),
    });
  }

  return result;
}
