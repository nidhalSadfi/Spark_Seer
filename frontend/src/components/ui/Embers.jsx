import { useMemo } from 'react'

/** Decorative rising embers. Pure CSS; disabled automatically under prefers-reduced-motion. */
export default function Embers({ count = 26, className = '' }) {
  const embers = useMemo(() => {
    // deterministic pseudo-random so the layout is stable between renders
    let s = 7
    const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647)
    return Array.from({ length: count }, (_, i) => ({
      id: i,
      left: rnd() * 100,
      size: 2 + rnd() * 3,
      dur: 5 + rnd() * 6,
      delay: -rnd() * 10,
      dx: (rnd() - 0.5) * 50,
      hot: rnd() > 0.65,
    }))
  }, [count])

  return (
    <div aria-hidden className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`}>
      {embers.map((e) => (
        <span
          key={e.id}
          className="absolute bottom-0 rounded-full motion-safe:animate-ember"
          style={{
            left: `${e.left}%`,
            width: e.size,
            height: e.size,
            background: e.hot ? '#fde68a' : '#f97316',
            boxShadow: `0 0 ${e.size * 3}px ${e.hot ? '#fbbf24' : '#f97316'}`,
            animationDelay: `${e.delay}s`,
            '--ember-dur': `${e.dur}s`,
            '--ember-dx': `${e.dx}px`,
            opacity: 0,
          }}
        />
      ))}
    </div>
  )
}
