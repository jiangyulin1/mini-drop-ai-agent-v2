import { Suspense, lazy } from "react";
import { Spin } from "antd";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import OperationsOverview from "./pages/OperationsOverview";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const AuditLogs = lazy(() => import("./pages/AuditLogs"));
const TaskResult = lazy(() => import("./pages/TaskResult"));
const AIDiagnosis = lazy(() => import("./pages/AIDiagnosis"));
const AgentDetail = lazy(() => import("./pages/AgentDetail"));
const Settings = lazy(() => import("./pages/Settings"));
const AgentsOverview = lazy(() => import("./pages/AgentsOverview"));
const RuntimeConsole = lazy(() => import("./pages/RuntimeConsole"));

const Lazy = ({ children }) => (
  <Suspense fallback={<Spin size="large" style={{ display: "block", margin: "40px auto" }} />}>
    {children}
  </Suspense>
);

export function LegacyCaseRedirect() {
  const location = useLocation();
  return (
    <Navigate
      to={{ pathname: "/cases", search: location.search, hash: location.hash }}
      replace
    />
  );
}

export default function Router() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<OperationsOverview />} />
          <Route path="/tasks" element={<Lazy><Dashboard /></Lazy>} />
          <Route path="/audit" element={<Lazy><AuditLogs /></Lazy>} />
          <Route
            path="/task/:taskId"
            element={<Lazy><TaskResult /></Lazy>}
          />
          <Route
            path="/cases"
            element={<Lazy><AIDiagnosis /></Lazy>}
          />
          {/* Legacy aliases: redirect so layout, sidebar highlighting and
              browser history all resolve to a single canonical URL. */}
          <Route path="/ai-diagnosis" element={<LegacyCaseRedirect />} />
          <Route
            path="/ai-cases"
            element={<LegacyCaseRedirect />}
          />
          <Route
            path="/diagnoses"
            element={<Navigate to="/cases" replace />}
          />
          <Route
            path="/agent/:agentId"
            element={<Lazy><AgentDetail /></Lazy>}
          />
          <Route path="/agents" element={<Lazy><AgentsOverview /></Lazy>} />
          <Route path="/runtime" element={<Lazy><RuntimeConsole /></Lazy>} />
          <Route path="/about" element={<Navigate to="/" replace />} />
          <Route
            path="/settings"
            element={<Lazy><Settings /></Lazy>}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
