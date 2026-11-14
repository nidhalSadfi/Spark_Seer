export const layers = {
  temperature: { label: 'Temperature', field: 'max_temperature_c', metric: 'Maximum temperature', unit: '°C', breaks: [10, 20, 25, 30, 35], colours: ['#4469a4', '#6da7ac', '#a8c984', '#ecd875', '#ed9b54', '#c85642'] },
  humidity: { label: 'Humidity', field: 'min_humidity_pct', metric: 'Minimum relative humidity', unit: '%', breaks: [20, 40, 60, 80], colours: ['#e3c78f', '#b6c98c', '#7ebca3', '#4a9c9c', '#30718a'] },
  wind: { label: 'Wind', field: 'max_wind_speed_kmh', metric: 'Maximum wind speed', unit: 'km/h', breaks: [10, 20, 30, 50], colours: ['#d1dfce', '#a2c3bc', '#74a5b4', '#687fa5', '#765c8e'] },
  rainfall: { label: 'Rainfall', field: 'rainfall_total_mm', metric: '24-hour rainfall total', unit: 'mm', breaks: [1, 5, 10, 25], colours: ['#e1e8d6', '#b7d9bc', '#75bba9', '#429999', '#326b8c'] },
};

export function colour(value, layer) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '#a3ada8';
  const index = layer.breaks.findIndex(limit => value < limit);
  return layer.colours[index < 0 ? layer.colours.length - 1 : index];
}

export function formatValue(value, unit = '') {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} ${unit}`.trim() : 'Unavailable';
}
