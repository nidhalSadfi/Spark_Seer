import { useEffect, useState } from 'react'
import { useIsFetching } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { Menu } from 'lucide-react'
import { DataModeBadge } from '../common/DataSource'
import { AoiChip, RegionSelector, SensorSelector, YearRangePicker } from '../common/FilterControls'

function UtcClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="hidden text-right font-mono text-xs text-muted sm:block" aria-label="Current UTC time">
      <span className="text-faint">UTC </span>
      <span className="tabular-nums text-ink">{now.toISOString().slice(11, 19)}</span>
    </div>
  )
}

/** Thin ember-colored bar that sweeps across the top while any data request is in flight. */
function FetchProgress() {
  // the periodic /health poll shouldn't flash the bar
  const fetching = useIsFetching({ predicate: (q) => q.queryKey[0] !== 'health' }) > 0
  return (
    <AnimatePresence>
      {fetching && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-fire/10"
          aria-hidden
        >
          <div className="h-full w-1/3 bg-gradient-to-r from-transparent via-fire to-fire-low motion-safe:animate-progress" />
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export default function Header({ onMenu }) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur-md">
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3 px-4 py-3 sm:px-6">
        <button
          onClick={onMenu}
          aria-label="Open navigation"
          className="grid size-9 place-items-center self-end rounded-lg border border-line text-muted transition-colors hover:border-fire/40 hover:text-ink lg:hidden"
        >
          <Menu className="size-5" />
        </button>
        <RegionSelector />
        <AoiChip />
        <SensorSelector />
        <YearRangePicker />
        <div className="ml-auto flex items-center gap-4 self-center">
          <UtcClock />
          <DataModeBadge />
        </div>
      </div>
      <FetchProgress />
    </header>
  )
}
