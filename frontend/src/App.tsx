import { BrowserRouter, Route, Routes } from 'react-router'
import { AppShell } from './components/AppShell'
import { PageHeader } from './components/PageHeader'
import { WorkspaceGate } from './components/WorkspaceGate'
import { DashboardPage } from './pages/DashboardPage'
import { OnboardingPage } from './pages/OnboardingPage'
import { OpportunityDetailPage } from './pages/OpportunityDetailPage'
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { SettingsPage } from './pages/SettingsPage'
import './App.css'

function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <section className="settings-panel">
        <p className="settings-empty">
          That address does not match any page. Use the navigation to pick one
          of the three destinations.
        </p>
      </section>
    </>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="settings" element={<SettingsPage />} />
        <Route path="onboarding" element={<OnboardingPage />} />
        <Route element={<WorkspaceGate />}>
          <Route index element={<DashboardPage />} />
          <Route path="opportunities" element={<OpportunitiesPage />} />
          <Route path="opportunities/:opportunityId" element={<OpportunityDetailPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}
