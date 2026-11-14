import { defineConfig } from 'vite';

export default defineConfig({
  plugins: process.env.EMBERGUARD_CDN_LEAFLET ? [{
    name: 'leaflet-cdn',
    resolveId(id) {
      if (id === 'leaflet') return { id: 'https://esm.sh/leaflet@1.9.4', external: true };
      if (id === 'leaflet/dist/leaflet.css') return '\0leaflet-css';
    },
    load(id) {
      if (id === '\0leaflet-css') return "const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'; document.head.appendChild(link);";
    },
  }] : [],
  server: { proxy: { '/weather': 'http://127.0.0.1:8000', '/fires': 'http://127.0.0.1:8000' } },
});
