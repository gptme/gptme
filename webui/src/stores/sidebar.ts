import { type Agent } from '@/utils/workspaceUtils';
import { observable } from '@legendapp/state';
import type { ImperativePanelHandle } from 'react-resizable-panels';

export const leftSidebarVisible$ = observable(true);
export const leftSidebarCollapsed$ = observable(false);
export const rightSidebarVisible$ = observable(true);
export const rightSidebarCollapsed$ = observable(false);
export const rightSidebarActiveTab$ = observable<string | null>(null);

// Selected workspace for filtering conversations
export const selectedWorkspace$ = observable<string | null>(null);

// Selected agent for filtering conversations
export const selectedAgent$ = observable<Agent | null>(null);

let leftPanelRef: ImperativePanelHandle | null = null;
let rightPanelRef: ImperativePanelHandle | null = null;

export const setLeftPanelRef = (ref: ImperativePanelHandle | null) => {
  leftPanelRef = ref;
};

export const setRightPanelRef = (ref: ImperativePanelHandle | null) => {
  rightPanelRef = ref;
};

export const toggleLeftSidebar = () => {
  if (leftPanelRef) {
    // Desktop: toggle visibility using panel ref, collapsed state managed separately
    if (leftPanelRef.isCollapsed()) {
      leftPanelRef.expand();
    } else {
      leftPanelRef.collapse();
    }
  } else {
    // Mobile: toggle state directly
    leftSidebarVisible$.set(!leftSidebarVisible$.get());
  }
};

export const toggleLeftSidebarCollapsed = () => {
  const currentlyCollapsed = leftSidebarCollapsed$.get();
  leftSidebarCollapsed$.set(!currentlyCollapsed);

  // Directly control the panel
  if (leftPanelRef) {
    if (!currentlyCollapsed) {
      // Collapsing: set to collapsed size
      leftPanelRef.collapse();
    } else {
      // Expanding: set to normal size
      leftPanelRef.resize(20);
    }
  }
};

export const toggleRightSidebar = () => {
  if (rightPanelRef) {
    // Desktop: use panel ref
    if (rightPanelRef.isCollapsed()) {
      rightPanelRef.expand();
    } else {
      rightPanelRef.collapse();
    }
  } else {
    // Mobile: toggle state directly
    rightSidebarVisible$.set(!rightSidebarVisible$.get());
  }
};

export const toggleRightSidebarCollapsed = () => {
  rightSidebarCollapsed$.set(!rightSidebarCollapsed$.get());
};
