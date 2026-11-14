import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'

const MAX_PARTICLES = 280
const POINTER_RADIUS = 170

const lerp = (a, b, t) => a + (b - a) * t
// ember colour over a particle's life: pale yellow → orange → red
const STOPS = [
  [253, 230, 138],
  [249, 115, 22],
  [239, 68, 68],
]
function emberColor(t) {
  const seg = t < 0.5 ? 0 : 1
  const k = t < 0.5 ? t / 0.5 : (t - 0.5) / 0.5
  const a = STOPS[seg]
  const b = STOPS[seg + 1]
  return [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)]
}

/**
 * Full-viewport ember particle field.
 *  - ambient embers rise from the bottom
 *  - the pointer swirls and pulls nearby embers
 *  - clicking bursts embers; the parent can call ref.burst(x, y, count, power)
 * Renders nothing (and runs no loop) under prefers-reduced-motion.
 */
const EmberField = forwardRef(function EmberField({ density = 60 }, ref) {
  const canvasRef = useRef(null)
  const burstFn = useRef(() => {})
  useImperativeHandle(ref, () => ({ burst: (...args) => burstFn.current(...args) }), [])

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    const parts = []
    const pointer = { x: -9999, y: -9999, active: false }
    let w = 0
    let h = 0
    let raf = 0
    let acc = 0
    let last = performance.now()

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = window.innerWidth
      h = window.innerHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const spawn = (x, y, vx, vy, life, size) => {
      if (parts.length >= MAX_PARTICLES) parts.shift()
      parts.push({ x, y, vx, vy, life, size, age: 0 })
    }

    burstFn.current = (x, y, count = 32, power = 1) => {
      for (let i = 0; i < count; i++) {
        const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.5
        const speed = (70 + Math.random() * 230) * power
        spawn(x, y, Math.cos(angle) * speed, Math.sin(angle) * speed, 1.1 + Math.random() * 1.6, 1.4 + Math.random() * 2.6)
      }
    }

    const onMove = (e) => {
      pointer.x = e.clientX
      pointer.y = e.clientY
      pointer.active = true
    }
    const onLeave = () => {
      pointer.active = false
    }
    const onDown = (e) => burstFn.current(e.clientX, e.clientY, 26, 0.9)

    const frame = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now

      acc += dt * density
      while (acc >= 1) {
        acc -= 1
        spawn(Math.random() * w, h + 8, (Math.random() - 0.5) * 30, -(60 + Math.random() * 110), 5 + Math.random() * 6, 0.9 + Math.random() * 2)
      }

      ctx.clearRect(0, 0, w, h)
      ctx.globalCompositeOperation = 'lighter'
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i]
        p.age += dt
        const t = p.age / p.life
        if (t >= 1) {
          parts.splice(i, 1)
          continue
        }
        if (pointer.active) {
          const dx = p.x - pointer.x
          const dy = p.y - pointer.y
          const d = Math.hypot(dx, dy)
          if (d < POINTER_RADIUS && d > 1) {
            const f = 1 - d / POINTER_RADIUS
            // swirl around the pointer, with a gentle pull inward
            p.vx += (-dy / d) * f * 260 * dt - (dx / d) * f * 70 * dt
            p.vy += (dx / d) * f * 260 * dt - (dy / d) * f * 70 * dt
          }
        }
        p.vy -= 10 * dt // buoyancy
        const drag = Math.max(0, 1 - 0.9 * dt)
        p.vx *= drag
        p.vy *= drag
        p.x += p.vx * dt
        p.y += p.vy * dt

        const [r, g, b] = emberColor(t)
        const fadeIn = Math.min(1, t / 0.08)
        const alpha = (1 - t) * fadeIn
        ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},${alpha * 0.1})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.size * 3, 0, 6.2832)
        ctx.fill()
        ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},${alpha})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.size, 0, 6.2832)
        ctx.fill()
      }
      raf = requestAnimationFrame(frame)
    }

    resize()
    window.addEventListener('resize', resize)
    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('pointerdown', onDown, { passive: true })
    document.documentElement.addEventListener('pointerleave', onLeave)
    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerdown', onDown)
      document.documentElement.removeEventListener('pointerleave', onLeave)
      burstFn.current = () => {}
    }
  }, [density])

  return <canvas ref={canvasRef} aria-hidden className="pointer-events-none fixed inset-0 z-0 size-full" />
})

export default EmberField
