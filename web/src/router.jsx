import { Suspense, lazy } from "react";
import { Spin } from "antd";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import OperationsOverview from "./pages/OperationsOverview";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const AuditLogs = lazy(() => import("./pages/AuditLogs"));
const TaskResult = lazy(() => import("./pages/TaskResult"));
const DiagnosisHistory = lazy(() => import("./pages/DiagnosisHistory"));
const AIDiagnosis = lazy(() => import("./pages/AIDiagnosis"));
const AgentDetail = lazy(() => import("./pages/AgentDetail"));
const Settings = lazy(() => import("./pages/Settings"));
const AgentsOverview = lazy(() => import("./pages/AgentsOverview"));
const RuntimeConsole = lazy(() => import("./pages/RuntimeConsole"));
const AboutAgent = lazy(() => import("./pages/AboutAgent"));

const Lazy = ({ children }) => (
  <Suspense fallback={<Spin size="large" style={{ display: "block", margin: "40px auto" }} />}>
    {children}
  </Suspense>
);

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
          <Route path="/ai-diagnosis" element={<Lazy><AIDiagnosis /></Lazy>} />
          <Route
            path="/ai-cases"
            element={<Lazy><AIDiagnosis /></Lazy>}
          />
          <Route
            path="/diagnoses"
            element={<Lazy><DiagnosisHistory /></Lazy>}
          />
          <Route
            path="/agent/:agentId"
            element={<Lazy><AgentDetail /></Lazy>}
          />
          <Route path="/agents" element={<Lazy><AgentsOverview /></Lazy>} />
          <Route path="/runtime" element={<Lazy><RuntimeConsole /></Lazy>} />
          <Route path="/about" element={<Lazy><AboutAgent /></Lazy>} />
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
