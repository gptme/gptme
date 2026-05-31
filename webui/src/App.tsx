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
import { ShortcutsDialog } from './components/ShortcutsDialog';
import { SetupWizard } from './components/SetupWizard';

// Lazy-loaded route pages — code-split at route boundaries for smaller initial bundle
const Index = lazy(() => import('./pages/Index'));
const Tasks = lazy(() => import('./pages/Tasks'));
const Workspace = lazy(() => import('./pages/Workspace'));
const Agents = lazy(() => import('./pages/Agents'));
const Workspaces = lazy(() => import('./pages/Workspaces'));
const History = lazy(() => import('./pages/History'));
const ExternalSessions = lazy(() => import('./pages/ExternalSessions'));
const Admin = lazy(() => import('./pages/Admin'));
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
                  <Suspense fallback={<RouteLoader />}>
                    <Routes>
                      <Route path="/" element={<Index />} />
                      <Route path="/chat" element={<Index />} />
                      <Route path="/chat/:id" element={<Index />} />
                      <Route path="/tasks" element={<Tasks />} />
                      <Route path="/tasks/:id" element={<Tasks />} />
                      <Route path="/agents" element={<Agents />} />
                      <Route path="/workspaces" element={<Workspaces />} />
                      <Route path="/history" element={<History />} />
                      <Route path="/external-sessions" element={<ExternalSessions />} />
                      <Route path="/admin" element={<Admin />} />
                      <Route path="/workspace/:id" element={<Workspace />} />
                      <Route path="*" element={<NotFound />} />
                    </Routes>
                  </Suspense>
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
