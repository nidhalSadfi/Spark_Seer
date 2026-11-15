import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Dish, Phosphor, Plot, Riffle, Settle } from '@lucasmarkes/hairline/react'

export const SCENES = [
  {
    id: 'orbit',
    label: 'Observe',
    Figure: Dish,
    heading: 'Satellites look down',
    body: 'MODIS and VIIRS watch the planet from polar orbit, passing over every region several times a day.',
    hint: 'Move the pointer to aim the dish.',
  },
  {
    id: 'detect',
    label: 'Detect',
    Figure: Phosphor,
    heading: 'Every detection leaves a trace',
    body: 'Each thermal anomaly becomes a hotspot record: a position, a time, a confidence and a fire radiative power.',
    hint: 'Sweep across the matrix to paint detections.',
  },
  {
    id: 'harmonize',
    label: 'Harmonize',
    Figure: Settle,
    heading: 'Two sensors, one record',
    body: 'Different resolutions and overpass times make raw counts incomparable. Harmonization lines them up into one consistent history.',
    hint: 'Bring the pointer close to the hub.',
  },
  {
    id: 'compare',
    label: 'Compare',
    Figure: Plot,
    heading: 'Each month against its median',
    body: 'Anomaly = (current − historical median) ÷ historical median. It shows which months break from the usual season.',
    hint: 'Brush across the bars.',
  },
  {
    id: 'explore',
    label: 'Explore',
    Figure: Riffle,
    heading: 'Walk the calendar',
    body: 'The fire activity calendar turns years of detections into a pattern you can read at a glance.',
    hint: 'Hover a card, or Tab to the tray and use ← → to walk it (Esc to leave).',
  },
]

// Hairline's palette, mapped onto Spark Seer's. `plate` must equal the stage background.
const STAGE_THEME = {
  '--hairline-plate': '#0c1423',
  '--hairline-hi': '#fde68a',
  '--hairline-edge': '#fb923c',
  '--hairline-mid': '#5d6f89',
  '--hairline-lo': '#26364d',
  '--hairline-stroke': 1,
}

export default function FigureShowcase({ scene, onScene }) {
  const [caption, setCaption] = useState('')
  const active = SCENES[scene]
  const { Figure } = active

  return (
    <div className="grid items-center gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div>
        <div role="tablist" aria-label="How Spark Seer works" className="flex flex-wrap gap-2">
          {SCENES.map((s, i) => {
            const on = i === scene
            return (
              <button
                key={s.id}
                role="tab"
                aria-selected={on}
                onClick={() => onScene(i)}
                className={`relative rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  on ? 'border-fire/50 text-orange-200' : 'border-line text-muted hover:border-faint hover:text-ink'
                }`}
              >
                {on && (
                  <motion.span
                    layoutId="scene-pill"
                    className="absolute inset-0 rounded-lg bg-fire/15"
                    transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                  />
                )}
                <span className="relative">
                  <span className="mr-1.5 font-mono text-[11px] text-fire/80">{i + 1}</span>
                  {s.label}
                </span>
              </button>
            )
          })}
        </div>

        <div className="mt-8 min-h-[190px]">
          <AnimatePresence mode="wait">
            <motion.div
              key={active.id}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.22 }}
            >
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-fire">
                <span className="mr-2 text-faint">//</span>0{scene + 1} · {active.label}
              </p>
              <h3 className="mt-2 text-3xl font-bold tracking-tight">{active.heading}</h3>
              <p className="mt-3 max-w-md text-[15px] leading-relaxed text-muted">{active.body}</p>
              <p className="mt-4 inline-flex items-center gap-2 rounded-full border border-line bg-card/60 px-3 py-1 text-xs text-muted">
                <span className="size-1.5 rounded-full bg-fire motion-safe:animate-pulse" aria-hidden />
                {active.hint}
              </p>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      <div className="relative" onMouseDownCapture={(e) => e.preventDefault() /* clicks must not focus a figure and lock the key shortcuts */}>
        <div
          className="relative overflow-hidden rounded-3xl border border-line p-3 shadow-[0_0_80px_-30px_rgba(249,115,22,0.5)]"
          style={{ ...STAGE_THEME, background: '#0c1423' }}
        >
          <span aria-hidden className="absolute left-3 top-3 z-10 size-3 border-l border-t border-fire/60" />
          <span aria-hidden className="absolute bottom-3 right-3 z-10 size-3 border-b border-r border-fire/60" />
          <AnimatePresence mode="wait">
            <motion.div
              key={active.id}
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 1.02 }}
              transition={{ duration: 0.25 }}
            >
              <Figure theme="dark" intensity={0.75} play onRead={setCaption} />
            </motion.div>
          </AnimatePresence>
          <p className="pointer-events-none absolute bottom-3 left-4 font-mono text-[11px] uppercase tracking-[0.18em] text-faint">
            {caption && <span className="text-fire/90">{caption}</span>}
          </p>
        </div>
      </div>
    </div>
  )
}
