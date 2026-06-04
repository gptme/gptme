export const appRoutes = {
  root: '/',
  chat: '/chat',
  chatConversation: '/chat/:id',
  tasks: '/tasks',
  taskDetails: '/tasks/:id',
  agents: '/agents',
  workspaces: '/workspaces',
  history: '/history',
  externalSessions: '/external-sessions',
  admin: '/admin',
  health: '/health',
  workspace: '/workspace/:id',
} as const;

export const cloudflareSpaRedirectRules = [
  `${appRoutes.chat} / 200`,
  `${appRoutes.chat}/* / 200`,
  `${appRoutes.tasks} / 200`,
  `${appRoutes.tasks}/* / 200`,
  `${appRoutes.agents} / 200`,
  `${appRoutes.workspaces} / 200`,
  `${appRoutes.history} / 200`,
  `${appRoutes.externalSessions} / 200`,
  `${appRoutes.admin} / 200`,
  `${appRoutes.health} / 200`,
  '/workspace/* / 200',
] as const;
