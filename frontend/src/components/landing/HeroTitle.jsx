import { useEffect, useState } from 'react'
import { motion } from 'motion/react'

const HEAT = ['#fde68a', '#fcd34d', '#fbbf24', '#f59e0b', '#f97316', '#fb7a2c', '#f05a24', '#ef4444', '#dc2626']
const LINES = ['SPARK', 'SEER']

/**
 * Wordmark whose letters spring away from the pointer and jump when you press
 * the matching key. `lastKey` is { key, n } — `n` changes on every key press.
 */
export default function HeroTitle({ lastKey }) {
  // `expired` remembers which key press (by n) has finished its jump
  const [expired, setExpired] = useState(0)

  useEffect(() => {
    if (!lastKey?.n) return undefined
    const t = setTimeout(() => setExpired(lastKey.n), 520)
    return () => clearTimeout(t)
  }, [lastKey])

  const pulsing = new Set()
  if (lastKey?.key && lastKey.n !== expired) {
    const ch = lastKey.key.toUpperCase()
    let k = 0
    LINES.forEach((line) =>
      [...line].forEach((c) => {
        if (c === ch) pulsing.add(k)
        k++
      }),
    )
  }

  let idx = -1
  return (
    <h1 aria-label="Spark Seer" className="font-black leading-[0.9] tracking-tight">
      {LINES.map((line, li) => (
        <span key={line} aria-hidden className="block text-[clamp(3.4rem,11vw,7.5rem)]">
          {[...line].map((c) => {
            idx++
            const i = idx
            const color = HEAT[i % HEAT.length]
            const hit = pulsing.has(i)
            return (
              <motion.span
                key={`${li}-${i}`}
                className="inline-block cursor-default select-none"
                style={{ color, textShadow: `0 0 36px ${color}55` }}
                initial={{ opacity: 0, y: 48, rotateX: -70 }}
                animate={
                  hit
                    ? { opacity: 1, rotateX: 0, y: [0, -22, 0], scale: [1, 1.25, 1] }
                    : { opacity: 1, rotateX: 0, y: 0, scale: 1 }
                }
                transition={
                  hit
                    ? { duration: 0.5, ease: 'easeOut' }
                    : { type: 'spring', stiffness: 180, damping: 16, delay: 0.1 + i * 0.05 }
                }
                whileHover={{ y: -12, scale: 1.14, transition: { type: 'spring', stiffness: 420, damping: 12 } }}
              >
                {c}
              </motion.span>
            )
          })}
        </span>
      ))}
    </h1>
  )
}
