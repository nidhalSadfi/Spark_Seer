import { motion } from 'motion/react'

const HINTS = [
  { id: 'arrows', keys: ['←', '→'], label: 'Switch scene' },
  { id: 'digits', keys: ['1', '–', '5'], label: 'Jump to scene' },
  { id: 'letters', keys: ['A', '–', 'Z'], label: 'Spark embers' },
  { id: 'enter', keys: ['⏎'], label: 'Launch dashboard' },
]

/** Floating keyboard legend; each group lights up while its keys are pressed. */
export default function KeyHints({ pressed }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed bottom-4 left-1/2 z-30 hidden -translate-x-1/2 items-center gap-4 rounded-2xl border border-line bg-bg/80 px-4 py-2 backdrop-blur-md md:flex"
    >
      {HINTS.map((h) => {
        const on = pressed === h.id
        return (
          <motion.div key={h.id} animate={{ scale: on ? 1.08 : 1 }} transition={{ type: 'spring', stiffness: 500, damping: 22 }} className="flex items-center gap-2">
            <span className="flex gap-1">
              {h.keys.map((k, i) =>
                k === '–' ? (
                  <span key={i} className="self-center text-faint">–</span>
                ) : (
                  <motion.kbd
                    key={i}
                    animate={{
                      backgroundColor: on ? 'rgba(249,115,22,0.35)' : 'rgba(30,45,68,1)',
                      borderColor: on ? 'rgba(249,115,22,0.9)' : 'rgba(37,53,76,1)',
                      y: on ? 2 : 0,
                    }}
                    transition={{ duration: 0.12 }}
                    className="min-w-6 rounded-md border px-1.5 py-0.5 text-center font-mono text-xs text-ink shadow-[0_2px_0_0_#25354c]"
                  >
                    {k}
                  </motion.kbd>
                ),
              )}
            </span>
            <span className={`text-xs transition-colors ${on ? 'text-orange-200' : 'text-muted'}`}>{h.label}</span>
          </motion.div>
        )
      })}
    </div>
  )
}
