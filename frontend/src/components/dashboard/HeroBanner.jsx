import { Link } from 'react-router-dom'
import { motion } from 'motion/react'
import { ArrowRight } from 'lucide-react'
import Embers from '../ui/Embers'
import { Badge } from '../ui/primitives'

// Illustrative blip positions on the scan disc (percent of the disc box).
const BLIPS = [
  { x: 28, y: 36, d: 0 },
  { x: 62, y: 28, d: 0.8 },
  { x: 70, y: 62, d: 1.6 },
  { x: 38, y: 70, d: 2.2 },
  { x: 52, y: 48, d: 1.1 },
]

function ScanDisc() {
  return (
    <div className="relative aspect-square w-full max-w-[260px]" aria-hidden>
      {/* concentric range rings */}
      {[100, 72, 44].map((s) => (
        <div
          key={s}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-fire/20"
          style={{ width: `${s}%`, height: `${s}%` }}
        />
      ))}
      <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-fire/10" />
      <div className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-fire/10" />
      {/* rotating sweep */}
      <div className="absolute inset-0 rounded-full motion-safe:animate-sweep" style={{ background: 'conic-gradient(from 0deg, transparent 0deg, transparent 280deg, rgba(249,115,22,0.35) 360deg)' }} />
      {/* detections */}
      {BLIPS.map((b, i) => (
        <span key={i} className="absolute" style={{ left: `${b.x}%`, top: `${b.y}%` }}>
          <span className="absolute -left-1 -top-1 size-2 rounded-full bg-fire-high shadow-[0_0_10px_#ef4444]" />
          <span
            className="absolute -left-1 -top-1 size-2 rounded-full border border-fire-high motion-safe:animate-blip"
            style={{ animationDelay: `${b.d}s` }}
          />
        </span>
      ))}
    </div>
  )
}

export default function HeroBanner({ regionLabel, sensorLabel, latest }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="relative mb-4 overflow-hidden rounded-2xl border border-line bg-gradient-to-br from-[#1b2538] via-[#14202f] to-[#2a1710] p-6 sm:p-8"
    >
      <div className="bg-survey absolute inset-0" aria-hidden />
      <div className="absolute -right-24 -top-24 size-80 rounded-full bg-fire/15 blur-3xl" aria-hidden />
      <Embers />

      <div className="relative grid items-center gap-6 md:grid-cols-[1fr_auto]">
        <div className="max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="fire">
              <span className="size-1.5 rounded-full bg-fire motion-safe:animate-pulse" aria-hidden />
              {regionLabel}
            </Badge>
            <Badge>{sensorLabel}</Badge>
            {latest && <Badge>Latest observation · {latest}</Badge>}
          </div>
          <h2 className="mt-4 text-3xl font-bold leading-tight tracking-tight sm:text-4xl">
            Seeing fire <span className="text-heat motion-safe:animate-shine">from orbit.</span>
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-muted sm:text-[15px]">
            Spark Seer harmonizes MODIS and VIIRS active-fire detections into one burning-activity calendar, so you can
            see when fire season peaks and which months break from the historical pattern.
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              to="/dashboard/calendar"
              className="group inline-flex h-10 items-center gap-2 rounded-lg bg-fire px-4 text-sm font-medium text-[#1a0d02] transition-colors hover:bg-orange-400"
            >
              Open fire calendar
              <ArrowRight className="size-4 transition-transform group-hover:translate-x-1" aria-hidden />
            </Link>
            <Link
              to="/dashboard/analytics"
              className="inline-flex h-10 items-center rounded-lg border border-line px-4 text-sm text-muted transition-colors hover:border-fire/40 hover:text-ink"
            >
              View analytics
            </Link>
          </div>
        </div>
        <div className="hidden justify-self-end md:block">
          <ScanDisc />
        </div>
      </div>
    </motion.section>
  )
}
