import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import { I18nextProvider } from 'react-i18next';
import { ThemeProvider } from './contexts/ThemeContext';
import { AuthProvider, ProtectedRoute } from './components/auth';
import { TaskMasterProvider } from './contexts/TaskMasterContext';
import { TasksSettingsProvider } from './contexts/TasksSettingsContext';
import { WebSocketProvider } from './contexts/WebSocketContext';
import { PluginsProvider } from './contexts/PluginsContext';
import { NodeProvider } from './contexts/NodeContext';
import AppContent from './components/app/AppContent';
import { getRouterBasename } from './utils/api';
import i18n from './i18n/config.js';

export default function App() {
  return (
    <I18nextProvider i18n={i18n}>
      <ThemeProvider>
        <AuthProvider>
          <NodeProvider>
            <WebSocketProvider>
              <PluginsProvider>
                <TasksSettingsProvider>
                  <TaskMasterProvider>
                  <ProtectedRoute>
                    <Router basename={window.__ROUTER_BASENAME__ || getRouterBasename()}>
                      <Routes>
                        <Route path="/" element={<AppContent />} />
                        <Route path="/index.html" element={<AppContent />} />
                        <Route path="/session" element={<AppContent />} />
                        <Route path="/session/:sessionKey" element={<AppContent />} />
                      </Routes>
                    </Router>
                  </ProtectedRoute>
                  </TaskMasterProvider>
                </TasksSettingsProvider>
              </PluginsProvider>
            </WebSocketProvider>
          </NodeProvider>
        </AuthProvider>
      </ThemeProvider>
    </I18nextProvider>
  );
}
