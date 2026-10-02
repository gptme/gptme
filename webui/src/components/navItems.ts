import {
  MessageSquare,
  Kanban,
  History,
  Bot,
  FolderOpen,
  Layers,
  Shield,
  BadgeCheck,
} from 'lucide-react';
import type { ComponentType } from 'react';

export type NavSection =
  | 'chat'
  | 'tasks'
  | 'history'
  | 'agents'
  | 'workspaces'
  | 'skills'
  | 'external-sessions'
  | 'admin';

export interface NavItem {
  id: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  section: NavSection;
  /** Shown directly in the mobile bottom bar; the rest go in its "More" sheet. */
  mobilePrimary?: boolean;
}

/** Canonical navigation list shared by the desktop sidebar and the mobile bottom nav. */
export const NAV_ITEMS: NavItem[] = [
  { id: 'chat', icon: MessageSquare, label: 'Chat', section: 'chat', mobilePrimary: true },
  { id: 'agents', icon: Bot, label: 'Agents', section: 'agents' },
  { id: 'workspaces', icon: FolderOpen, label: 'Workspaces', section: 'workspaces' },
  { id: 'tasks', icon: Kanban, label: 'Tasks', section: 'tasks', mobilePrimary: true },
  { id: 'history', icon: History, label: 'History', section: 'history', mobilePrimary: true },
  { id: 'skills', icon: BadgeCheck, label: 'Skills', section: 'skills' },
  { id: 'external-sessions', icon: Layers, label: 'External', section: 'external-sessions' },
  { id: 'admin', icon: Shield, label: 'Admin', section: 'admin' },
];
