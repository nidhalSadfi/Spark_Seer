import { useRef } from 'react'
import { motion, useMotionValue, useSpring } from 'motion/react'
import { ArrowRight } from 'lucide-react'

/** CTA that leans toward the pointer before you even touch it. */
export default function MagneticButton({ children, onClick, kbd = '⏎' }) {
  const wrap = useRef(null)
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const sx = useSpring(x, { stiffness: 220, damping: 16 })
  const sy = useSpring(y, { stiffness: 220, damping: 16 })

  const onMove = (e) => {
    const r = wrap.current.getBoundingClientRect()
    x.set((e.clientX - (r.left + r.width / 2)) * 0.3)
    y.set((e.clientY - (r.top + r.height / 2)) * 0.45)
  }
  const onLeave = () => {
    x.set(0)
    y.set(0)
  }

  return (
    <div ref={wrap} onPointerMove={onMove} onPointerLeave={onLeave} className="-m-6 inline-block p-6">
      <motion.button
        type="button"
        onClick={onClick}
        style={{ x: sx, y: sy }}
        whileTap={{ scale: 0.95 }}
        className="group relative inline-flex h-14 items-center gap-3 overflow-hidden rounded-xl bg-gradient-to-r from-fire-mid via-fire to-fire-high bg-[length:200%_100%] px-7 text-base font-semibold text-[#1a0d02] shadow-[0_0_40px_-8px_rgba(249,115,22,0.8)] motion-safe:animate-shine"
      >
        <span className="relative">{children}</span>
        <ArrowRight className="relative size-5 transition-transform duration-300 group-hover:translate-x-1.5" aria-hidden />
        {kbd && (
          <kbd className="relative hidden rounded-md border border-black/25 bg-black/15 px-1.5 py-0.5 font-mono text-xs sm:inline">
            {kbd}
          </kbd>
        )}
      </motion.button>
    </div>
  )
}
