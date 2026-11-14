import { Suspense, useState } from 'react'
import { useLocation, useOutlet } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { X } from 'lucide-react'
import Sidebar from './Sidebar'
import Header from './Header'
import { Skeleton } from '../ui/primitives'

function PageFallback() {
  return (
    <div className="flex flex-col gap-4" aria-busy>
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

export default function DashboardLayout() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()
  const outlet = useOutlet()

  return (
    <div className="relative min-h-screen lg:grid lg:grid-cols-[250px_1fr]">
      {/* Atmosphere: survey grid + ember glow, fixed behind all content */}
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-0 overflow-hidden">
        <div className="bg-survey absolute inset-0" />
        <div className="absolute -right-40 -top-40 size-[560px] rounded-full bg-fire/[0.07] blur-3xl" />
        <div className="absolute -bottom-52 left-1/3 size-[480px] rounded-full bg-fire-high/[0.05] blur-3xl" />
      </div>

      {/* Desktop sidebar */}
      <aside className="sticky top-0 z-10 hidden h-screen border-r border-line lg:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setOpen(false)}
            />
            <motion.aside
              className="fixed inset-y-0 left-0 z-40 w-64 border-r border-line lg:hidden"
              initial={{ x: -270 }}
              animate={{ x: 0 }}
              exit={{ x: -270 }}
              transition={{ type: 'spring', stiffness: 380, damping: 36 }}
            >
              <button
                onClick={() => setOpen(false)}
                aria-label="Close navigation"
                className="absolute right-3 top-3 z-10 text-muted hover:text-ink"
              >
                <X className="size-5" />
              </button>
              <Sidebar onNavigate={() => setOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="relative z-10 min-w-0">
        <Header onMenu={() => setOpen(true)} />
        <AnimatePresence mode="wait" initial={false}>
          <motion.main
            key={pathname}
            initial={{ opacity: 0, y: 14, filter: 'blur(4px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -8, filter: 'blur(3px)' }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6"
          >
            <Suspense fallback={<PageFallback />}>{outlet}</Suspense>
          </motion.main>
        </AnimatePresence>
      </div>
    </div>
  )
}
