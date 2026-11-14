import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import DashboardLayout from './components/layout/DashboardLayout'
import { Skeleton } from './components/ui/primitives'

// Route-level code splitting keeps ECharts out of the initial bundle.
const Overview = lazy(() => import('./pages/Overview'))
const FireMapPage = lazy(() => import('./pages/FireMapPage'))
const CalendarPage = lazy(() => import('./pages/CalendarPage'))
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage'))
const DataExplorer = lazy(() => import('./pages/DataExplorer'))

export default function App() {
  return (
    <Suspense fallback={<Skeleton className="m-6 h-64" />}>
      <Routes>
        <Route element={<DashboardLayout />}>
          <Route index element={<Overview />} />
          <Route path="map" element={<FireMapPage />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="explorer" element={<DataExplorer />} />
          <Route path="*" element={<Overview />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
