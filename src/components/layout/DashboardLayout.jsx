import { useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { X } from 'lucide-react'
import Sidebar from './Sidebar'
import Header from './Header'

export default function DashboardLayout() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen border-r border-line lg:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              className="fixed inset-0 z-30 bg-black/60 lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setOpen(false)}
            />
            <motion.aside
              className="fixed inset-y-0 left-0 z-40 w-64 border-r border-line lg:hidden"
              initial={{ x: -260 }}
              animate={{ x: 0 }}
              exit={{ x: -260 }}
              transition={{ duration: 0.2 }}
            >
              <button
                onClick={() => setOpen(false)}
                aria-label="Close navigation"
                className="absolute right-3 top-3 text-muted hover:text-ink"
              >
                <X className="size-5" />
              </button>
              <Sidebar onNavigate={() => setOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="min-w-0">
        <Header onMenu={() => setOpen(true)} />
        <motion.main
          key={pathname}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25, ease: 'easeOut' }}
          className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6"
        >
          <Outlet />
        </motion.main>
      </div>
    </div>
  )
}
