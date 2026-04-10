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

  // For each other branch, find the first divergence index.
  // Compare against the current branch AND group identical branches at the
  // same fork point as other branches that DO diverge there, so the count
  // stays consistent regardless of which branch you're viewing from.
  const divergenceMap = new Map<number, string[]>(); // forkIndex -> branch names

  // First pass: find where each branch diverges from current
  const branchForkIndex = new Map<string, number | null>(); // branch -> forkIndex or null (identical)
  for (const branch of branchNames) {
    if (branch === currentBranch) continue;
    const otherLog = branches[branch];
    if (!otherLog) continue;

    const maxLen = Math.max(currentLog.length, otherLog.length);
    let found = false;
    for (let i = 0; i < maxLen; i++) {
      const currentMsg = currentLog[i];
      const otherMsg = otherLog[i];

      if (!currentMsg || !otherMsg || currentMsg.timestamp !== otherMsg.timestamp) {
        const forkIndex = Math.max(0, i - 1);
        branchForkIndex.set(branch, forkIndex);
        found = true;
        break;
      }
    }
    if (!found) {
      // Branch is identical to current — find where it diverges from ANY other branch
      branchForkIndex.set(branch, null);
    }
  }

  // Second pass: identical branches get assigned the most common fork index
  // from non-identical branches (they were created at the same point)
  let fallbackForkIndex: number | null = null;
  for (const [, idx] of branchForkIndex) {
    if (idx !== null) {
      fallbackForkIndex = idx;
      break;
    }
  }

  for (const [branch, idx] of branchForkIndex) {
    const forkIndex = idx ?? fallbackForkIndex;
    if (forkIndex === null) continue; // all branches identical, skip
    if (!divergenceMap.has(forkIndex)) {
      divergenceMap.set(forkIndex, []);
    }
    divergenceMap.get(forkIndex)!.push(branch);
  }

  // Build ForkInfo for each fork point
  const result = new Map<number, ForkInfo>();
  for (const [forkIndex, otherBranches] of divergenceMap) {
    const allBranches = [currentBranch, ...otherBranches];

    // Sort by timestamp of the diverging message, with branch name as tiebreaker
    // for deterministic order regardless of which branch is currently viewed
    allBranches.sort((a, b) => {
      const aMsg = branches[a]?.[forkIndex + 1];
      const bMsg = branches[b]?.[forkIndex + 1];
      if (!aMsg && !bMsg) return a.localeCompare(b);
      if (!aMsg) return 1;
      if (!bMsg) return -1;
      const timeDiff =
        new Date(aMsg.timestamp || '').getTime() - new Date(bMsg.timestamp || '').getTime();
      return timeDiff !== 0 ? timeDiff : a.localeCompare(b);
    });

    result.set(forkIndex, {
      branchNames: allBranches,
      currentIndex: allBranches.indexOf(currentBranch),
    });
  }

  return result;
}
