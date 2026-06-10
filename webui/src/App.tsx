import { Toaster } from '@/components/ui/toaster';
import { Toaster as Sonner } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ThemeProvider } from 'next-themes';
import { ApiProvider } from './contexts/ApiContext';
import { EmbeddedContextProvider } from './contexts/EmbeddedContext';
import { SettingsProvider } from './contexts/SettingsContext';
import { lazy, Suspense, type FC } from 'react';
import { CommandPalette } from './components/CommandPalette';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ShortcutsDialog } from './components/ShortcutsDialog';
import { SetupWizard } from './components/SetupWizard';
import { appRoutes } from './appRoutes';

// Lazy-loaded route pages — code-split at route boundaries for smaller initial bundle
const Index = lazy(() => import('./pages/Index'));
const Tasks = lazy(() => import('./pages/Tasks'));
const Workspace = lazy(() => import('./pages/Workspace'));
const Agents = lazy(() => import('./pages/Agents'));
const Workspaces = lazy(() => import('./pages/Workspaces'));
const History = lazy(() => import('./pages/History'));
const ExternalSessions = lazy(() => import('./pages/ExternalSessions'));
const Admin = lazy(() => import('./pages/Admin'));
const Health = lazy(() => import('./pages/Health'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const NotFound = lazy(() => import('./pages/NotFound'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Disable automatic background refetching
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      refetchOnReconnect: false,
      // Reduce stale time to ensure updates are visible immediately
      staleTime: 0,
      // Keep cached data longer
      gcTime: 1000 * 60 * 5,
      // Ensure we get updates
      notifyOnChangeProps: 'all',
    },
    mutations: {
      // Ensure mutations trigger immediate updates
      onSuccess: () => {
        queryClient.invalidateQueries();
      },
    },
  },
});

/** Lightweight fallback shown while a lazy-route chunk is loading. */
const RouteLoader: FC = () => (
  <div className="flex h-64 items-center justify-center">
    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
  </div>
);

const App: FC = () => {
  return (
    <EmbeddedContextProvider>
      <SettingsProvider>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <QueryClientProvider client={queryClient}>
            <TooltipProvider>
              <ApiProvider queryClient={queryClient}>
                <BrowserRouter
                  basename={import.meta.env.BASE_URL}
                  future={{
                    v7_startTransition: true,
                    v7_relativeSplatPath: true,
                  }}
                >
                  <ErrorBoundary>
                    <Suspense fallback={<RouteLoader />}>
                      <Routes>
                        <Route path={appRoutes.root} element={<Index />} />
                        <Route path={appRoutes.chat} element={<Index />} />
                        <Route path={appRoutes.chatConversation} element={<Index />} />
                        <Route path={appRoutes.tasks} element={<Tasks />} />
                        <Route path={appRoutes.taskDetails} element={<Tasks />} />
                        <Route path={appRoutes.agents} element={<Agents />} />
                        <Route path={appRoutes.workspaces} element={<Workspaces />} />
                        <Route path={appRoutes.history} element={<History />} />
                        <Route path={appRoutes.externalSessions} element={<ExternalSessions />} />
                        <Route path={appRoutes.admin} element={<Admin />} />
                        <Route path={appRoutes.health} element={<Health />} />
                        <Route path={appRoutes.settings} element={<SettingsPage />} />
                        <Route path={appRoutes.settingsCategory} element={<SettingsPage />} />
                        <Route path={appRoutes.workspace} element={<Workspace />} />
                        <Route path="*" element={<NotFound />} />
                      </Routes>
                    </Suspense>
                  </ErrorBoundary>
                  <SetupWizard />
                  <CommandPalette />
                  <ShortcutsDialog />
                  <Toaster />
                  <Sonner />
                </BrowserRouter>
              </ApiProvider>
            </TooltipProvider>
          </QueryClientProvider>
        </ThemeProvider>
      </SettingsProvider>
    </EmbeddedContextProvider>
  );
};

export default App;
