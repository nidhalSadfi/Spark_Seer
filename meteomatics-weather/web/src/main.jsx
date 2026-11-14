import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import './style.css';
import { colour, formatValue, layers } from './weather';

async function getJSON(path, signal) {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function WeatherMap({ data, layer, selected, onSelect, fires }) {
  const container = useRef(null);
  const map = useRef(null);
  const cells = useRef(null);
  const detections = useRef(null);
  useEffect(() => {
    map.current = L.map(container.current).setView([36.3, 9.7], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors', maxZoom: 18,
    }).addTo(map.current);
    return () => { map.current.remove(); };
  }, []);
  useEffect(() => {
    if (cells.current) cells.current.remove();
    if (!data) return;
    cells.current = L.geoJSON(data, {
      style: feature => ({ color: feature.properties.grid_id === selected ? '#10281f' : '#ffffff',
        weight: feature.properties.grid_id === selected ? 3 : 1,
        fillColor: colour(feature.properties[layer.field], layer), fillOpacity: .65 }),
      onEachFeature: (feature, polygon) => {
        const text = document.createElement('div');
        text.textContent = `${feature.properties.grid_id} · ${layer.metric}: ${formatValue(feature.properties[layer.field], layer.unit)}`;
        polygon.bindTooltip(text).on('click', () => onSelect(feature.properties.grid_id));
      },
    }).addTo(map.current);
  }, [data, layer, selected, onSelect]);
  useEffect(() => {
    if (detections.current) detections.current.remove();
    if (!fires?.features) return;
    detections.current = L.geoJSON(fires, {
      pointToLayer: (feature, position) => L.circleMarker(position, {
        radius: 5, color: '#7c291b', weight: 1, fillColor: '#f29149', fillOpacity: .9,
      }),
      onEachFeature: (feature, marker) => {
        const text = document.createElement('div');
        text.textContent = `NASA FIRMS · ${feature.properties.date} · ${feature.properties.grid_id}`;
        marker.bindTooltip(text).on('click', () => onSelect(feature.properties.grid_id));
      },
    }).addTo(map.current);
  }, [fires, onSelect]);
  return <div ref={container} className="map" aria-label="Northern Tunisia interactive weather map" />;
}

function App() {
  const [layerName, setLayerName] = useState('temperature');
  const [windowIndex, setWindow] = useState(0);
  const [data, setData] = useState(null);
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  const [cell, setCell] = useState(null);
  const [cellError, setCellError] = useState('');
  const [history, setHistory] = useState(null);
  const [fireError, setFireError] = useState('');
  const [day, setDay] = useState('');
  const [reload, setReload] = useState(0);
  const layer = layers[layerName];
  useEffect(() => {
    const controller = new AbortController();
    getJSON('/weather/grid-meta', controller.signal).then(setMeta).catch(() => {});
    return () => controller.abort();
  }, [reload]);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    getJSON(`/weather/map?window=${windowIndex}`, controller.signal).then(setData)
      .catch(failure => { if (failure.name !== 'AbortError') setError(failure.message); });
    return () => controller.abort();
  }, [windowIndex, reload]);
  useEffect(() => {
    setCell(null);
    setCellError('');
    if (!selected) return;
    const controller = new AbortController();
    getJSON(`/weather/cell/${encodeURIComponent(selected)}`, controller.signal).then(result => {
      if (!Number.isFinite(result.lat) || !Number.isFinite(result.lon)
        || !Array.isArray(result.hourly) || !Array.isArray(result.daily)
        || result.daily.length !== 3 || !Array.isArray(result.warnings)) {
        throw new Error('Cell forecast is incomplete; refresh the weather cache.');
      }
      setCell(result);
    })
      .catch(failure => { if (failure.name !== 'AbortError') setCellError(failure.message); });
    return () => controller.abort();
  }, [selected, reload]);
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams();
    if (selected) query.set('grid_id', selected);
    if (day) query.set('day', day);
    setHistory(null);
    setFireError('');
    getJSON(`/fires/calendar?${query}`, controller.signal).then(setHistory)
      .catch(failure => { if (failure.name !== 'AbortError') setFireError(failure.message); });
    return () => controller.abort();
  }, [selected, day, reload]);
  const summary = cell?.daily[windowIndex];
  return <>
    <header><div className="brand"><span className="brand-icon">E</span><div><b>EmberGuard <span>AI</span></b><small>ENVIRONMENTAL INTELLIGENCE</small></div></div><span className="region">NORTHERN TUNISIA <span className="dot" /> WEATHER CONTEXT</span></header>
    <main>
      <div className="intro"><div><p className="eyebrow">THE NEXT 72 HOURS</p><h1>Weather, in perspective.</h1><p>Explore point forecasts and historical fire observations across northern Tunisia.</p></div><button onClick={() => setReload(value => value + 1)}>↻ Reload cached data</button></div>
      <div className="workspace"><section className="map-panel">
        <div className="toolbar"><div className="tabs" aria-label="Weather layer">{Object.entries(layers).map(([key, item]) => <button key={key} aria-pressed={layerName === key} onClick={() => setLayerName(key)}>{item.label}</button>)}</div><select aria-label="Forecast window" value={windowIndex} onChange={event => setWindow(Number(event.target.value))}><option value={0}>0–24h</option><option value={1}>24–48h</option><option value={2}>48–72h</option></select></div>
        {error && <div className="notice" role="alert">Weather unavailable: {error}. The grid remains visible; no forecast values have been substituted.</div>}
        {!data && !error && <div className="notice" role="status">Loading forecast…</div>}
        {data?.stale && <div className="notice" role="alert">Stale forecast cache — retrieved {data.retrieved_at_utc}. Check validity before use.</div>}
        <WeatherMap data={data || meta?.grid} layer={layer} selected={selected} onSelect={setSelected} fires={history} />
        <div className="legend"><b>{layer.metric} · {layer.unit}</b><div className="swatches">{layer.colours.map((shade, index) => <span key={shade}><i style={{background: shade}} />{index === 0 ? `< ${layer.breaks[0]}` : index === layer.colours.length - 1 ? `≥ ${layer.breaks[index - 1]}` : `${layer.breaks[index - 1]}–<${layer.breaks[index]}`}</span>)}<span><i style={{background: '#a3ada8'}}/>Unavailable</span></div></div>
        <div className="source">METEOMATICS · {meta?.cell_count ?? '—'} CELLS · 0.5° GRID<br/>{data ? `Retrieved ${data.retrieved_at_utc} · cache age ${data.cache_age_hours ?? 'unknown'} h` : 'Awaiting forecast cache'}{data && <><br/>Valid samples: {data.valid_from_utc} → {data.valid_until_utc}</>}</div>
      </section><aside>
        <section className="card"><p className="eyebrow">POINT FORECAST</p><h2>{selected || 'Explore a cell'}</h2>{!selected && <p>Click a geographical cell to inspect its exact centre forecast and linked fire history.</p>}{cellError && <p role="alert">{cellError}</p>}{cell && <><p className="coordinates">{cell.lat.toFixed(3)}°N, {cell.lon.toFixed(3)}°E · UTC</p><p>{summary.first_valid_time_utc} → {summary.last_valid_time_utc}</p><dl>{Object.values(layers).map(item => <div key={item.field}><dt>{item.metric}</dt><dd>{formatValue(summary[item.field], item.unit)}</dd></div>)}<div><dt>Maximum gust</dt><dd>{formatValue(summary.max_wind_gust_kmh, 'km/h')}</dd></div><div><dt>Rainfall coverage</dt><dd>{summary.rainfall_hours_available}/24 hours</dd></div></dl><p>Extrema use available samples; check hourly gaps below.</p>{cell.warnings.map(warning => <p className="warning" key={warning}>{warning}</p>)}<details><summary>View exact hourly values</summary><div className="hourly"><table><thead><tr><th>UTC time</th><th>°C</th><th>RH %</th><th>Wind km/h</th><th>Gust km/h</th><th>Rain mm</th></tr></thead><tbody>{cell.hourly.slice(windowIndex * 24, (windowIndex + 1) * 24).map(row => <tr key={row.valid_time_utc}><td>{row.valid_time_utc}</td>{['temperature_c', 'relative_humidity_pct', 'wind_speed_kmh', 'wind_gust_kmh', 'precip_1h_mm'].map(field => <td key={field}>{formatValue(row[field])}</td>)}</tr>)}</tbody></table></div></details></>}{selected && <button className="text-button" onClick={() => setSelected('')}>Clear selection</button>}</section>
        <section className="card"><p className="eyebrow">NASA FIRMS · HISTORY</p><h2>Historical fire calendar</h2><p>{selected ? `Observations linked to ${selected}.` : 'Observations across the shared weather grid.'} Historical dates do not change the forecast period.</p><label>Observation date<input type="date" value={day} onChange={event => setDay(event.target.value)}/></label>{day && <button className="text-button" onClick={() => setDay('')}>Show all imported dates</button>}{fireError && <p role="alert">{fireError}</p>}{history && !history.available && <p className="empty">Historical data unavailable. Connect your NASA FIRMS CSV to populate this calendar.</p>}{history?.available && <><div className="calendar">{history.calendar.map(entry => <button key={entry.date} aria-pressed={day === entry.date} onClick={() => setDay(entry.date)}>{entry.date}<b>{entry.count} detections</b></button>)}</div>{!history.calendar.length && <p>No imported detections match this cell.</p>}<p>{history.features.length} of {history.matching_detections} imported detections shown.{history.truncated && ' Marker limit reached; select a date or cell to narrow the results.'} Counts are observations, not unique fires. Missing dates do not prove zero fires.</p></>}</section>
      </aside></div>
      <footer><b>Weather context, not an official fire-danger index or fire-ignition prediction.</b><p>Point forecasts at cell centres; map colours are not area averages. Default bounding-box cells include offshore and border areas. Three rolling 24-hour windows, not calendar days. Rainfall covers the hour preceding each validity time. Retrieval time is not a model issue time.</p></footer>
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
