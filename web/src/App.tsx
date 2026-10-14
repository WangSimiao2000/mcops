import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '@/auth/AuthContext';
import { useAuth } from '@/auth/useAuth';
import { RequireAuth } from '@/auth/RequireAuth';
import { AppLayout } from '@/components/AppLayout';
import { ConsoleProvider } from '@/console/ConsoleContext';
import { lazyPage } from '@/lib/lazyPage';
import { LoginPage } from '@/pages/LoginPage';
import { OverviewPage } from '@/pages/OverviewPage';

// Every page except login + overview is split into its own chunk, so the first
// load over the SSH tunnel only fetches the shell (xterm ships with the console page).
const BackupsPage = lazyPage(() => import('@/pages/BackupsPage'), 'BackupsPage');
const BotPage = lazyPage(() => import('@/pages/BotPage'), 'BotPage');
const ConsolePage = lazyPage(() => import('@/pages/ConsolePage'), 'ConsolePage');
const IpDetailPage = lazyPage(() => import('@/pages/IpDetailPage'), 'IpDetailPage');
const OpsPage = lazyPage(() => import('@/pages/OpsPage'), 'OpsPage');
const PlayerDetailPage = lazyPage(() => import('@/pages/PlayerDetailPage'), 'PlayerDetailPage');
const PlayersPage = lazyPage(() => import('@/pages/PlayersPage'), 'PlayersPage');
const SecurityPage = lazyPage(() => import('@/pages/SecurityPage'), 'SecurityPage');
const SettingsPage = lazyPage(() => import('@/pages/SettingsPage'), 'SettingsPage');
const ModsPage = lazyPage(() => import('@/pages/ModsPage'), 'ModsPage');
const LogsPage = lazyPage(() => import('@/pages/LogsPage'), 'LogsPage');
const NetworkPage = lazyPage(() => import('@/pages/NetworkPage'), 'NetworkPage');
const WhitelistPage = lazyPage(() => import('@/pages/WhitelistPage'), 'WhitelistPage');

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 2000,
    },
  },
});

function LoginRoute() {
  const { status } = useAuth();
  if (status === 'authenticated') return <Navigate to="/" replace />;
  return <LoginPage />;
}

function ProtectedShell() {
  return (
    <RequireAuth>
      <ConsoleProvider>
        <AppLayout />
      </ConsoleProvider>
    </RequireAuth>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginRoute />} />
            <Route element={<ProtectedShell />}>
              <Route index element={<OverviewPage />} />
              <Route path="console" element={<ConsolePage />} />
              <Route path="players" element={<PlayersPage />} />
              <Route path="players/:name" element={<PlayerDetailPage />} />
              <Route path="ips/:ip" element={<IpDetailPage />} />
              <Route path="security" element={<SecurityPage />} />
              <Route path="bot" element={<BotPage />} />
              <Route path="whitelist" element={<WhitelistPage />} />
              <Route path="ops" element={<OpsPage />} />
              <Route path="backups" element={<BackupsPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="mods" element={<ModsPage />} />
              <Route path="logs" element={<LogsPage />} />
              <Route path="network" element={<NetworkPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
