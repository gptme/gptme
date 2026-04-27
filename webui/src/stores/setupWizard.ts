import { observable } from '@legendapp/state';

export type SetupWizardStep = 'welcome' | 'mode' | 'local' | 'cloud' | 'provider' | 'complete';

/** Observable to reopen the setup wizard to a specific step from outside the component tree. */
export const setupWizard$ = observable({
  open: false,
  step: 'welcome' as SetupWizardStep,
});
