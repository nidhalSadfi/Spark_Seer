# Spark Seer

Satellite fire-intelligence dashboard (NASA Space Apps 2026) that harmonizes MODIS and VIIRS active-fire records into an activity calendar with historical anomaly analysis.

Stack: React + Vite, Tailwind CSS v4, Motion, ECharts, TanStack Query/Table, NumberFlow, Sonner, Lucide.

```
npm install
npm run dev
```

## Status
- All data is **illustrative mock data** generated in `src/services/fireApi.js`. Replace the bodies of those functions with real API calls; the rest of the UI only depends on their return shapes.
- **Map is intentionally blank** (`src/components/map/FireMap.jsx`), owned by another team member. Integration contract is documented at the top of that file: read filters via `useFilters()`, call `setAoi(...)` when an AOI is drawn.

## Structure
`components/{layout,dashboard,map,calendar,analytics,common,ui}`, `pages/`, `services/`, `hooks/`, `context/`, `utils/`
