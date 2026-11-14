# Spark Seer

Satellite fire-intelligence dashboard (NASA Space Apps 2026) that harmonizes MODIS and VIIRS active-fire records into an activity calendar with historical anomaly analysis.

Stack: React + Vite, Tailwind CSS v4, Motion, ECharts, Leaflet, TanStack Query/Table, NumberFlow, Sonner, Lucide.

```
npm install
npm run dev
```

## Status
- All data is **illustrative mock data** generated in `src/services/fireApi.js`. Replace the bodies of those functions with real API calls; the rest of the UI only depends on their return shapes.
- **Live fire map** (`src/components/map/FireMap.jsx`): Leaflet on the Esri Dark Gray basemap (no API key), fire clusters sized by FRP over a glow layer of hotspot detections, 24 h playback slider, layer toggles, legend and rectangle AOI drawing. Data comes from `getActiveFires()` in `fireApi.js`. It reads filters via `useFilters()` and calls `setAoi(...)` when an area is drawn. The map page adds the selected-fire panel (`FireDetails.jsx`) and the satellite-pass / fire-weather chart (`FirePassChart.jsx`).

## Structure
`components/{layout,dashboard,map,calendar,analytics,common,ui}`, `pages/`, `services/`, `hooks/`, `context/`, `utils/`

## Landing page
`/` is an interactive landing page (Hairline figures, ember particles, keyboard controls); the dashboard lives under `/dashboard`.
Edit the challenge title/summary and the team name and members in `src/config/project.js` — empty fields show placeholders.
