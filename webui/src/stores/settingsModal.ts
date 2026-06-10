import { observable } from '@legendapp/state';

export const SETTINGS_CATEGORIES = [
  'servers',
  'appearance',
  'audio',
  'content',
  'developer',
  'about',
] as const;

export type SettingsCategory = (typeof SETTINGS_CATEGORIES)[number];

/** Observable to open the settings modal from outside (e.g. server dropdown, command palette). */
export const settingsModal$ = observable({
  open: false,
  category: 'appearance' as SettingsCategory,
});
