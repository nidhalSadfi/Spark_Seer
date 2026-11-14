import { useRef } from 'react'
import { motion, useMotionTemplate, useMotionValue, useSpring } from 'motion/react'

/** Card that tilts toward the pointer in 3D and carries an ember glare. */
export default function TiltCard({ children, className = '', max = 9 }) {
  const ref = useRef(null)
  const rx = useSpring(useMotionValue(0), { stiffness: 220, damping: 20 })
  const ry = useSpring(useMotionValue(0), { stiffness: 220, damping: 20 })
  const gx = useMotionValue(50)
  const gy = useMotionValue(50)
  const glare = useMotionTemplate`radial-gradient(240px circle at ${gx}% ${gy}%, rgba(249,115,22,0.20), transparent 70%)`

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect()
    const px = (e.clientX - r.left) / r.width
    const py = (e.clientY - r.top) / r.height
    ry.set((px - 0.5) * 2 * max)
    rx.set(-(py - 0.5) * 2 * max)
    gx.set(px * 100)
    gy.set(py * 100)
  }
  const onLeave = () => {
    rx.set(0)
    ry.set(0)
    gx.set(50)
    gy.set(50)
  }

  return (
    <motion.div
      ref={ref}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      style={{ rotateX: rx, rotateY: ry, transformPerspective: 900 }}
      className={`group relative overflow-hidden rounded-2xl border border-line bg-gradient-to-br from-card to-[#0f1a2b] p-6 transition-colors duration-300 hover:border-fire/40 ${className}`}
    >
      <motion.div aria-hidden className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" style={{ background: glare }} />
      <div className="relative">{children}</div>
    </motion.div>
  )
}
