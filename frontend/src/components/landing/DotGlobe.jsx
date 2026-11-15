import { useEffect, useRef, useState } from 'react'
import { LAND_DOTS } from './globeLand'

/**
 * Wireframe dot-matrix globe (canvas 2D, orthographic projection).
 *  - land drawn as a dot matrix (Natural Earth coastlines, see globeLand.js)
 *  - lat/long wireframe with node points at the grid intersections; the far side shows through faintly
 *  - fire hotspots: glowing, pulsing points. The four large rings are the EmberLedger coverage areas
 *    (Tunisia / northern Algeria); the other clusters are ILLUSTRATIVE, not real detections.
 *  - drag to rotate (with inertia), click a hotspot to fly to it, hover for its name
 *  - keyboard: focus the globe, arrows rotate, Enter / Space jumps between clusters, Esc leaves it
 */

const RAD = Math.PI / 180
const TAU = Math.PI * 2
const clamp = (v, a, b) => Math.min(b, Math.max(a, v))
const lerp = (a, b, t) => a + (b - a) * t
const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2)

// ── hotspot clusters ─────────────────────────────────────────────
const CLUSTERS = [
  // EmberLedger coverage (backend areas)
  { name: 'Bizerte', lat: 37.1, lon: 9.65, core: true },
  { name: 'Northwest Tunisia', lat: 36.6, lon: 8.75, core: true },
  { name: 'Northeast Algeria', lat: 36.7, lon: 7.8, core: true },
  { name: 'Kabylie', lat: 36.65, lon: 4.45, core: true },
  // other fire-prone regions (illustrative)
  { name: 'Amazon Basin', lat: -8, lon: -60, n: 14, spread: 7 },
  { name: 'Central Africa', lat: -6, lon: 24, n: 16, spread: 8 },
  { name: 'West Africa savanna', lat: 10, lon: -2, n: 10, spread: 6 },
  { name: 'Northern Australia', lat: -15, lon: 133, n: 10, spread: 7 },
  { name: 'Western United States', lat: 40, lon: -118, n: 9, spread: 5 },
  { name: 'Boreal Canada', lat: 56, lon: -108, n: 8, spread: 6 },
  { name: 'Siberia', lat: 58, lon: 105, n: 12, spread: 8 },
  { name: 'Indonesia', lat: -2, lon: 112, n: 9, spread: 5 },
  { name: 'Iberia', lat: 40, lon: -6, n: 6, spread: 3 },
  { name: 'Greece & Turkey', lat: 38.5, lon: 27, n: 6, spread: 3 },
  { name: 'Southern Africa', lat: -20, lon: 28, n: 8, spread: 5 },
]

function buildFires() {
  let s = 20260
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647)
  const gauss = () => (rnd() + rnd() + rnd() - 1.5) / 0.75
  const fires = []
  for (const c of CLUSTERS) {
    fires.push({ lat: c.lat, lon: c.lon, size: c.core ? 1.25 : 1.4, phase: rnd(), name: c.name, core: !!c.core })
    for (let i = 0; i < (c.n ?? 0); i++) {
      fires.push({ lat: clamp(c.lat + gauss() * c.spread, -80, 80), lon: c.lon + (gauss() * c.spread) / Math.max(0.35, Math.cos(c.lat * RAD)), size: 0.45 + rnd() * 0.55, phase: rnd(), name: null, core: false })
    }
  }
  return fires
}

/** Precompute cos(lat)·sin(lon), cos(lat)·cos(lon), sin(lat) for a flat [lat, lon, ...] list in degrees. */
function basis(list, scale = 1) {
  const n = list.length / 2
  const A = new Float32Array(n)
  const B = new Float32Array(n)
  const S = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const phi = list[2 * i] * scale * RAD
    const lam = list[2 * i + 1] * scale * RAD
    A[i] = Math.cos(phi) * Math.sin(lam)
    B[i] = Math.cos(phi) * Math.cos(lam)
    S[i] = Math.sin(phi)
  }
  return { n, A, B, S }
}

const LAND = basis(LAND_DOTS, 0.1) // stored in tenths of a degree
const FIRES = buildFires()
const FIRE_BASIS = basis(FIRES.flatMap((f) => [f.lat, f.lon]))
const MAIN_IDX = FIRES.map((f, i) => (f.name ? i : -1)).filter((i) => i >= 0)

// bright → dim styles for land dots, bucketed by depth
const LAND_BUCKETS = 6
const landStyle = (k) => `rgba(${Math.round(lerp(70, 190, k))},${Math.round(lerp(92, 208, k))},${Math.round(lerp(120, 232, k))},${lerp(0.28, 0.95, k).toFixed(2)})`

function makeGlowSprite() {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  grad.addColorStop(0, 'rgba(255,214,150,1)')
  grad.addColorStop(0.25, 'rgba(249,115,22,0.75)')
  grad.addColorStop(0.6, 'rgba(239,68,68,0.22)')
  grad.addColorStop(1, 'rgba(239,68,68,0)')
  g.fillStyle = grad
  g.fillRect(0, 0, 64, 64)
  return c
}

export default function DotGlobe({ className = '' }) {
  const wrapRef = useRef(null)
  const canvasRef = useRef(null)
  const tipRef = useRef(null)
  const api = useRef({})
  const [tip, setTip] = useState(null) // { name, core } of the hovered / selected cluster

  useEffect(() => {
    const wrap = wrapRef.current
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const glow = makeGlowSprite()

    const st = {
      lon0: 12, // longitude facing the viewer
      tilt: 24, // latitude facing the viewer
      vLon: 0,
      vTilt: 0,
      size: 0,
      dpr: 1,
      dragging: false,
      moved: 0,
      last: null,
      pointer: null, // CSS px relative to canvas
      hover: -1,
      selected: -1,
      fly: null,
      idle: 0, // seconds since the user last touched it
      visible: true,
      dirty: true,
    }
    let raf = 0
    let prev = performance.now()
    let tipKey = ''

    const setActive = () => {
      const i = st.selected >= 0 ? st.selected : st.hover
      const key = i >= 0 ? String(i) : ''
      if (key !== tipKey) {
        tipKey = key
        setTip(i >= 0 ? { name: FIRES[i].name, core: FIRES[i].core } : null)
      }
    }

    const resize = () => {
      const w = wrap.clientWidth
      st.dpr = Math.min(window.devicePixelRatio || 1, 2)
      st.size = w
      canvas.width = Math.round(w * st.dpr)
      canvas.height = Math.round(w * st.dpr)
      canvas.style.width = `${w}px`
      canvas.style.height = `${w}px`
      st.dirty = true
    }

    const flyTo = (lat, lon) => {
      const dLon = ((lon - st.lon0 + 540) % 360) - 180
      st.fly = { fromLon: st.lon0, toLon: st.lon0 + dLon, fromTilt: st.tilt, toTilt: clamp(lat, -55, 55), t: 0, dur: 0.9 }
      st.vLon = st.vTilt = 0
    }
    const select = (i) => {
      st.selected = i
      if (i >= 0) flyTo(FIRES[i].lat, FIRES[i].lon)
      setActive()
      st.dirty = true
    }

    // ── projection helpers (rotation about the vertical axis, then tilt) ──
    let c0 = 1
    let s0 = 0
    let cT = 1
    let sT = 0
    const setView = () => {
      c0 = Math.cos(st.lon0 * RAD)
      s0 = Math.sin(st.lon0 * RAD)
      cT = Math.cos(st.tilt * RAD)
      sT = Math.sin(st.tilt * RAD)
    }
    // x,y in [-1,1] screen space (y up), z>0 faces the viewer
    const proj = (A, B, S, i, out) => {
      const x = A[i] * c0 - B[i] * s0
      const w = B[i] * c0 + A[i] * s0
      out.x = x
      out.y = cT * S[i] - sT * w
      out.z = sT * S[i] + cT * w
    }
    const P = { x: 0, y: 0, z: 0 }

    // lat/lon in degrees → x,y,z (same projection as proj, without the precomputed tables)
    const projLL = (lat, lon, out) => {
      const phi = lat * RAD
      const lam = lon * RAD
      const cphi = Math.cos(phi)
      const sphi = Math.sin(phi)
      const A = cphi * Math.sin(lam)
      const B = cphi * Math.cos(lam)
      const w = B * c0 + A * s0
      out.x = A * c0 - B * s0
      out.y = cT * sphi - sT * w
      out.z = sT * sphi + cT * w
    }

    const drawGrid = (cx, cy, R) => {
      const front = new Path2D()
      const back = new Path2D()
      const sample = (latOf, lonOf, count) => {
        let prevPt = null
        for (let k = 0; k <= count; k++) {
          projLL(latOf(k / count), lonOf(k / count), P)
          const px = cx + P.x * R
          const py = cy - P.y * R
          const isFront = P.z >= 0
          if (prevPt && prevPt.front === isFront) {
            const path = isFront ? front : back
            path.moveTo(prevPt.x, prevPt.y)
            path.lineTo(px, py)
          }
          prevPt = { x: px, y: py, front: isFront }
        }
      }
      for (let lat = -60; lat <= 60; lat += 30) sample(() => lat, (t) => t * 360 - 180, 120)
      for (let lon = -180; lon < 180; lon += 30) sample((t) => -90 + t * 180, () => lon, 60)
      ctx.lineWidth = Math.max(1, R * 0.0035)
      ctx.strokeStyle = 'rgba(93,111,137,0.07)'
      ctx.stroke(back)
      ctx.strokeStyle = 'rgba(111,135,168,0.30)'
      ctx.stroke(front)

      // node points where the wireframe lines cross
      for (let lat = -60; lat <= 60; lat += 30) {
        for (let lon = -180; lon < 180; lon += 30) {
          projLL(lat, lon, P)
          const isFront = P.z >= 0
          ctx.fillStyle = isFront ? 'rgba(253,186,116,0.85)' : 'rgba(253,186,116,0.12)'
          ctx.beginPath()
          ctx.arc(cx + P.x * R, cy - P.y * R, R * (isFront ? 0.0085 : 0.006), 0, TAU)
          ctx.fill()
        }
      }
    }

    const draw = (t) => {
      const W = canvas.width
      const R = W * 0.43
      const cx = W / 2
      const cy = W / 2
      setView()
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, W, W)

      // sphere body + rim
      const body = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.1, cx, cy, R)
      body.addColorStop(0, '#16233a')
      body.addColorStop(1, '#0a111f')
      ctx.fillStyle = body
      ctx.beginPath()
      ctx.arc(cx, cy, R, 0, TAU)
      ctx.fill()
      // glow hugging the rim only (a gradient from r0 > 0 would tint the whole interior with its first stop)
      const rim = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.16)
      rim.addColorStop(0, 'rgba(249,115,22,0)')
      rim.addColorStop(0.8, 'rgba(249,115,22,0)')
      rim.addColorStop(0.86, 'rgba(249,115,22,0.26)')
      rim.addColorStop(1, 'rgba(249,115,22,0)')
      ctx.fillStyle = rim
      ctx.beginPath()
      ctx.arc(cx, cy, R * 1.16, 0, TAU)
      ctx.fill()
      ctx.strokeStyle = 'rgba(111,135,168,0.45)'
      ctx.lineWidth = Math.max(1, R * 0.004)
      ctx.beginPath()
      ctx.arc(cx, cy, R, 0, TAU)
      ctx.stroke()

      drawGrid(cx, cy, R)

      // land dot matrix, bucketed by depth so each bucket is one path
      const buckets = Array.from({ length: LAND_BUCKETS }, () => new Path2D())
      const back = new Path2D()
      const baseR = R * 0.0062
      for (let i = 0; i < LAND.n; i++) {
        proj(LAND.A, LAND.B, LAND.S, i, P)
        const px = cx + P.x * R
        const py = cy - P.y * R
        if (P.z < 0) {
          back.moveTo(px + baseR * 0.7, py)
          back.arc(px, py, baseR * 0.7, 0, TAU)
        } else {
          const b = Math.min(LAND_BUCKETS - 1, Math.floor(P.z * LAND_BUCKETS))
          const r = baseR * (0.6 + 0.7 * P.z)
          buckets[b].moveTo(px + r, py)
          buckets[b].arc(px, py, r, 0, TAU)
        }
      }
      ctx.fillStyle = 'rgba(93,111,137,0.13)'
      ctx.fill(back)
      buckets.forEach((path, b) => {
        ctx.fillStyle = landStyle((b + 0.5) / LAND_BUCKETS)
        ctx.fill(path)
      })

      // fire hotspots
      ctx.globalCompositeOperation = 'lighter'
      const active = st.selected >= 0 ? st.selected : st.hover
      let tipPos = null
      for (let i = 0; i < FIRES.length; i++) {
        proj(FIRE_BASIS.A, FIRE_BASIS.B, FIRE_BASIS.S, i, P)
        if (P.z <= 0.02) continue
        const f = FIRES[i]
        const px = cx + P.x * R
        const py = cy - P.y * R
        const vis = Math.min(1, P.z * 2.2)
        const pulse = reduced ? 0.5 : (t * 0.55 + f.phase) % 1
        const gs = R * 0.05 * f.size * (i === active ? 1.5 : 1) * (0.85 + 0.15 * Math.sin(t * 3 + f.phase * 6))
        ctx.globalAlpha = vis
        ctx.drawImage(glow, px - gs, py - gs, gs * 2, gs * 2)
        // expanding ring
        ctx.globalAlpha = (1 - pulse) * 0.55 * vis
        ctx.strokeStyle = f.core ? '#fde68a' : '#f97316'
        ctx.lineWidth = Math.max(1, R * 0.004)
        ctx.beginPath()
        ctx.arc(px, py, R * 0.012 * f.size + pulse * R * 0.05 * f.size, 0, TAU)
        ctx.stroke()
        // bright core
        ctx.globalAlpha = vis
        ctx.fillStyle = '#fff1d0'
        ctx.beginPath()
        ctx.arc(px, py, R * 0.0075 * f.size, 0, TAU)
        ctx.fill()
        // coverage-area marker ring
        if (f.core) {
          ctx.globalAlpha = 0.9 * vis
          ctx.strokeStyle = '#fde68a'
          ctx.setLineDash([R * 0.012, R * 0.012])
          ctx.lineWidth = Math.max(1, R * 0.004)
          ctx.beginPath()
          ctx.arc(px, py, R * 0.034, 0, TAU)
          ctx.stroke()
          ctx.setLineDash([])
        }
        if (i === active) tipPos = { x: px / st.dpr, y: py / st.dpr }
      }
      ctx.globalAlpha = 1
      ctx.globalCompositeOperation = 'source-over'

      // tooltip follows the active hotspot (or hides when it rotates out of view)
      const tipEl = tipRef.current
      if (tipEl) {
        if (tipPos) {
          const w = tipEl.offsetWidth
          const flip = tipPos.x + 14 + w > st.size - 6 // keep the label inside the panel
          tipEl.style.opacity = '1'
          tipEl.style.transform = `translate(${flip ? tipPos.x - 14 - w : tipPos.x + 14}px, ${Math.max(4, tipPos.y - 14)}px)`
        } else tipEl.style.opacity = '0'
      }
    }

    const frame = (now) => {
      const dt = Math.min(0.05, (now - prev) / 1000)
      prev = now

      let moving = false
      if (st.fly) {
        st.fly.t += dt / st.fly.dur
        const k = ease(Math.min(1, st.fly.t))
        st.lon0 = lerp(st.fly.fromLon, st.fly.toLon, k)
        st.tilt = lerp(st.fly.fromTilt, st.fly.toTilt, k)
        if (st.fly.t >= 1) st.fly = null
        moving = true
      } else if (!st.dragging) {
        // inertia, then gentle auto-rotation once the user lets go
        st.idle += dt
        if (Math.abs(st.vLon) > 0.02 || Math.abs(st.vTilt) > 0.02) {
          st.lon0 += st.vLon * dt
          st.tilt = clamp(st.tilt + st.vTilt * dt, -60, 60)
          const damp = Math.exp(-3.2 * dt)
          st.vLon *= damp
          st.vTilt *= damp
          moving = true
        } else if (!reduced && st.selected < 0 && st.idle > 1.2) {
          const slow = st.hover >= 0 ? 0.15 : 1
          st.lon0 += 6 * dt * slow
          st.tilt = lerp(st.tilt, 24, Math.min(1, dt * 0.4))
          moving = true
        }
      } else moving = true

      if (st.visible && !document.hidden && (!reduced || st.dirty || moving)) {
        draw(now / 1000)
        st.dirty = false
      }
      raf = requestAnimationFrame(frame)
    }

    // ── pointer input ──
    const local = (e) => {
      const r = canvas.getBoundingClientRect()
      return { x: e.clientX - r.left, y: e.clientY - r.top }
    }
    const pick = (p) => {
      const R = st.size * 0.43
      const cx = st.size / 2
      setView()
      let best = -1
      let bestD = Infinity
      for (const i of MAIN_IDX) {
        proj(FIRE_BASIS.A, FIRE_BASIS.B, FIRE_BASIS.S, i, P)
        if (P.z < 0.12) continue
        const d = Math.hypot(cx + P.x * R - p.x, cx - P.y * R - p.y)
        if (d < R * 0.06 && d < bestD) {
          best = i
          bestD = d
        }
      }
      return best
    }
    const onDown = (e) => {
      st.dragging = true
      st.moved = 0
      st.last = { x: e.clientX, y: e.clientY, t: performance.now() }
      st.fly = null
      st.vLon = st.vTilt = 0
      canvas.setPointerCapture?.(e.pointerId)
    }
    const onMove = (e) => {
      const p = local(e)
      st.pointer = p
      if (st.dragging && st.last) {
        const dx = e.clientX - st.last.x
        const dy = e.clientY - st.last.y
        const dt = Math.max(0.008, (performance.now() - st.last.t) / 1000)
        const k = 90 / (st.size * 0.43) // degrees per px: a radius drag turns ~90°
        st.lon0 -= dx * k
        st.tilt = clamp(st.tilt + dy * k, -60, 60)
        st.vLon = (-dx * k) / dt
        st.vTilt = (dy * k) / dt
        st.moved += Math.abs(dx) + Math.abs(dy)
        st.last = { x: e.clientX, y: e.clientY, t: performance.now() }
        st.idle = 0
      } else {
        const h = pick(p)
        if (h !== st.hover) {
          st.hover = h
          canvas.style.cursor = h >= 0 ? 'pointer' : 'grab'
          setActive()
        }
      }
      st.dirty = true
    }
    const onUp = (e) => {
      if (!st.dragging) return
      st.dragging = false
      canvas.releasePointerCapture?.(e.pointerId)
      st.idle = 0
      if (st.moved < 5) {
        // a click: select the hotspot under the pointer, or clear the selection
        const h = pick(local(e))
        select(h === st.selected ? -1 : h)
      } else {
        // flick: cap the coasting speed
        st.vLon = clamp(st.vLon, -140, 140)
        st.vTilt = clamp(st.vTilt, -90, 90)
        if (st.selected >= 0) {
          st.selected = -1
          setActive()
        }
      }
    }
    const onLeave = () => {
      st.pointer = null
      if (st.hover !== -1) {
        st.hover = -1
        setActive()
      }
      canvas.style.cursor = 'grab'
    }

    // keyboard (the wrapper is a focusable group)
    api.current.onKeyDown = (e) => {
      const turn = (dLon, dTilt) => {
        e.preventDefault()
        st.fly = null
        st.lon0 += dLon
        st.tilt = clamp(st.tilt + dTilt, -60, 60)
        st.idle = 0
        st.dirty = true
      }
      if (e.key === 'ArrowLeft') turn(-12, 0)
      else if (e.key === 'ArrowRight') turn(12, 0)
      else if (e.key === 'ArrowUp') turn(0, 8)
      else if (e.key === 'ArrowDown') turn(0, -8)
      else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        const cur = MAIN_IDX.indexOf(st.selected)
        select(MAIN_IDX[(cur + 1) % MAIN_IDX.length])
      } else if (e.key === 'Escape') {
        select(-1)
        e.currentTarget.blur()
      }
    }

    const io = new IntersectionObserver(([en]) => {
      st.visible = en.isIntersecting
      st.dirty = true
    })
    io.observe(wrap)
    const ro = new ResizeObserver(resize)
    ro.observe(wrap)
    canvas.style.cursor = 'grab'
    canvas.addEventListener('pointerdown', onDown)
    canvas.addEventListener('pointermove', onMove)
    canvas.addEventListener('pointerup', onUp)
    canvas.addEventListener('pointercancel', onUp)
    canvas.addEventListener('pointerleave', onLeave)
    resize()
    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      io.disconnect()
      ro.disconnect()
      canvas.removeEventListener('pointerdown', onDown)
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerup', onUp)
      canvas.removeEventListener('pointercancel', onUp)
      canvas.removeEventListener('pointerleave', onLeave)
    }
  }, [])

  return (
    <div
      ref={wrapRef}
      role="group"
      tabIndex={0}
      aria-label="Interactive globe of fire hotspots. Drag to rotate, or use the arrow keys. Enter jumps between hotspot clusters, Escape leaves the globe."
      onKeyDown={(e) => api.current.onKeyDown?.(e)}
      // Mouse clicks must not focus the globe (it would then swallow the page's arrow-key shortcuts);
      // keyboard users still reach it with Tab.
      onMouseDown={(e) => e.preventDefault()}
      className={`relative aspect-square w-full select-none rounded-3xl outline-offset-2 ${className}`}
    >
      <canvas ref={canvasRef} className="block touch-pan-y" />

      <div
        ref={tipRef}
        aria-hidden
        className="pointer-events-none absolute left-0 top-0 z-10 max-w-[200px] rounded-lg border border-line bg-[#0d1524]/95 px-2.5 py-1.5 text-xs opacity-0 shadow-lg backdrop-blur-sm transition-opacity duration-150"
      >
        {tip && (
          <>
            <p className="font-semibold text-ink">{tip.name}</p>
            <p className="text-[11px] text-muted">{tip.core ? 'Study area with a 25-year record' : 'Illustrative hotspot cluster'}</p>
          </>
        )}
      </div>

      <p className="sr-only" aria-live="polite">
        {tip ? `${tip.name}. ${tip.core ? 'Study area with a 25-year record.' : 'Illustrative hotspot cluster.'}` : ''}
      </p>

      <div className="pointer-events-none absolute inset-x-4 bottom-3 flex items-end justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.14em] text-faint">
        <span className="flex flex-col gap-1">
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-fire shadow-[0_0_8px_#f97316]" aria-hidden /> Fire hotspot (illustrative)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full border border-dashed border-fire-low" aria-hidden /> Study areas
          </span>
        </span>
        <span className="text-right">Drag · click a hotspot</span>
      </div>
    </div>
  )
}
