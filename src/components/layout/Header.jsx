import { Menu } from 'lucide-react'
import { Badge } from '../ui/primitives'
import { RegionSelector, SensorSelector, YearRangePicker } from '../common/FilterControls'

export default function Header({ onMenu }) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur-md">
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3 px-4 py-3 sm:px-6">
        <button
          onClick={onMenu}
          aria-label="Open navigation"
          className="grid size-9 place-items-center self-end rounded-lg border border-line text-muted hover:text-ink lg:hidden"
        >
          <Menu className="size-5" />
        </button>
        <RegionSelector />
        <SensorSelector />
        <YearRangePicker />
        <div className="ml-auto self-center">
          <Badge tone="ok">
            <span className="size-1.5 rounded-full bg-ok" aria-hidden /> Sample data mode
          </Badge>
        </div>
      </div>
    </header>
  )
}
