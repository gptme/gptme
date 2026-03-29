import { observable } from '@legendapp/state';

export type SettingsCategory = 'servers' | 'appearance' | 'audio' | 'content' | 'about';

/** Observable to open the settings modal from outside (e.g. server dropdown, command palette). */
export const settingsModal$ = observable({
  open: false,
  category: 'appearance' as SettingsCategory,
});
