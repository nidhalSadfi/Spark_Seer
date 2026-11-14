import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from 'motion/react'
import { Terrain } from '@lucasmarkes/hairline/react'
import { Brand } from '../components/layout/Sidebar'
import EmberField from '../components/landing/EmberField'
import HeroTitle from '../components/landing/HeroTitle'
import MagneticButton from '../components/landing/MagneticButton'
import FigureShowcase, { SCENES } from '../components/landing/FigureShowcase'
import KeyHints from '../components/landing/KeyHints'
import { ChallengeCard, TeamSection } from '../components/landing/TeamChallenge'
import { FactsStrip, FeatureGrid, Reveal, SectionHeading } from '../components/landing/LandingParts'
import { PROJECT } from '../config/project'

const KEY_ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
const ROW_OFFSET = [0, 0.3, 0.8]

/** Map a letter key to a position along the bottom of the viewport, like a keyboard layout. */
function keyPosition(k) {
  for (let r = 0; r < KEY_ROWS.length; r++) {
    const i = KEY_ROWS[r].indexOf(k)
    if (i >= 0) {
      return {
        x: ((i + 0.5 + ROW_OFFSET[r]) / 10.8) * window.innerWidth,
        y: window.innerHeight * (0.97 - r * 0.02),
      }
    }
  }
  return { x: window.innerWidth / 2, y: window.innerHeight }
}

const HERO_THEME = {
  '--hairline-plate': '#0b1322',
  '--hairline-hi': '#fde68a',
  '--hairline-edge': '#fb923c',
  '--hairline-mid': '#5d6f89',
  '--hairline-lo': '#25354c',
  '--hairline-stroke': 1,
}

export default function Landing() {
  const navigate = useNavigate()
  const embers = useRef(null)
  const spot = useRef(null)
  const timers = useRef([])
  const [scene, setScene] = useState(0)
  const [pressed, setPressed] = useState(null)
  const [lastKey, setLastKey] = useState(null)
  const [leaving, setLeaving] = useState(null)
  const [scrolled, setScrolled] = useState(false)

  // pointer-driven parallax for the hero figure
  const px = useMotionValue(0)
  const py = useMotionValue(0)
  const rotY = useSpring(useTransform(px, [-0.5, 0.5], [-9, 9]), { stiffness: 120, damping: 18 })
  const rotX = useSpring(useTransform(py, [-0.5, 0.5], [7, -7]), { stiffness: 120, damping: 18 })

  const later = (fn, ms) => {
    const t = setTimeout(fn, ms)
    timers.current.push(t)
  }
  useEffect(() => {
    const list = timers.current
    return () => list.forEach(clearTimeout)
  }, [])

  const flash = useCallback((id) => {
    setPressed(id)
    later(() => setPressed((p) => (p === id ? null : p)), 220)
  }, [])

  const enter = useCallback(
    (x = window.innerWidth / 2, y = window.innerHeight / 2) => {
      if (leaving) return
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        navigate('/dashboard')
        return
      }
      setLeaving({ x, y })
      embers.current?.burst(x, y, 90, 1.5)
      later(() => navigate('/dashboard'), 900)
    },
    [leaving, navigate],
  )

  // Pointer: spotlight + parallax
  useEffect(() => {
    let raf = 0
    const onMove = (e) => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        spot.current?.style.setProperty('--mx', `${e.clientX}px`)
        spot.current?.style.setProperty('--my', `${e.clientY}px`)
        px.set(e.clientX / window.innerWidth - 0.5)
        py.set(e.clientY / window.innerHeight - 0.5)
      })
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('pointermove', onMove)
    }
  }, [px, py])

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Keyboard: scenes, sparks, launch
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return
      const el = e.target
      const interactive = el.closest?.('button, a, input, select, textarea')
      const inFigure = el.closest?.('[role="group"]') // Hairline's Riffle owns the arrow keys when focused

      if (e.key === 'Enter' && !interactive) {
        flash('enter')
        enter()
        return
      }
      if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && !inFigure) {
        flash('arrows')
        const d = e.key === 'ArrowRight' ? 1 : -1
        setScene((s) => (s + d + SCENES.length) % SCENES.length)
        return
      }
      if (/^[1-5]$/.test(e.key)) {
        flash('digits')
        setScene(Number(e.key) - 1)
        return
      }
      if (/^[a-z]$/i.test(e.key) && !interactive) {
        flash('letters')
        const { x, y } = keyPosition(e.key.toLowerCase())
        embers.current?.burst(x, y, 22, 1)
        setLastKey({ key: e.key, n: Date.now() })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enter, flash])

  const launch = (e) => enter(e?.clientX || undefined, e?.clientY || undefined)

  return (
    <div id="top" className="relative min-h-screen overflow-x-clip bg-bg text-ink">
      {/* atmosphere */}
      <div aria-hidden className="bg-survey pointer-events-none fixed inset-0 z-0" />
      <div
        ref={spot}
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{ background: 'radial-gradient(560px circle at var(--mx, 70%) var(--my, 25%), rgba(249,115,22,0.11), transparent 62%)' }}
      />
      <EmberField ref={embers} />

      {/* nav */}
      <header
        className={`fixed inset-x-0 top-0 z-40 transition-all duration-300 ${
          scrolled ? 'border-b border-line bg-bg/80 backdrop-blur-md' : 'border-b border-transparent'
        }`}
      >
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-8">
          <a href="#top" aria-label="Spark Seer home">
            <Brand />
          </a>
          <nav aria-label="Sections" className="hidden items-center gap-7 text-sm text-muted md:flex">
            <a href="#explore" className="transition-colors hover:text-ink">How it works</a>
            <a href="#inside" className="transition-colors hover:text-ink">Inside the dashboard</a>
            <a href="#team" className="transition-colors hover:text-ink">Challenge &amp; team</a>
          </nav>
          <motion.button
            type="button"
            onClick={launch}
            whileHover={{ y: -1 }}
            whileTap={{ scale: 0.95 }}
            className="h-9 rounded-lg border border-fire/50 bg-fire/10 px-4 text-sm font-medium text-orange-200 transition-colors hover:bg-fire/20"
          >
            Launch dashboard
          </motion.button>
        </div>
      </header>

      <main className="relative z-10">
        {/* hero */}
        <section className="mx-auto grid min-h-screen max-w-7xl items-center gap-10 px-5 pb-20 pt-28 sm:px-8 lg:grid-cols-[1.05fr_1fr]">
          <div>
            <motion.p
              initial={{ opacity: 0, x: -16 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.5 }}
              className="mb-5 inline-flex items-center gap-2 rounded-full border border-fire/40 bg-fire/10 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.18em] text-orange-200"
            >
              <span className="size-1.5 rounded-full bg-fire motion-safe:animate-pulse" aria-hidden />
              {PROJECT.challenge.event}
            </motion.p>
            <HeroTitle lastKey={lastKey} />
            <motion.p
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.7 }}
              className="mt-6 max-w-lg text-lg leading-relaxed text-muted"
            >
              Harmonizing <strong className="font-semibold text-ink">MODIS</strong> and{' '}
              <strong className="font-semibold text-ink">VIIRS</strong> active-fire records into one burning-activity
              calendar — so you can see when fire season peaks and which months break from the pattern.
            </motion.p>
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.9 }}
              className="mt-8 flex flex-wrap items-center gap-x-8"
            >
              <MagneticButton onClick={launch}>Launch dashboard</MagneticButton>
              <a href="#explore" className="group inline-flex items-center gap-2 text-sm text-muted transition-colors hover:text-ink">
                See how it works
                <span className="transition-transform duration-300 group-hover:translate-y-1" aria-hidden>↓</span>
              </a>
            </motion.div>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 1.4 }}
              className="mt-2 font-mono text-[11px] uppercase tracking-[0.16em] text-faint"
            >
              Move your pointer · press any key
            </motion.p>
          </div>

          <motion.div
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.8, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
            style={{ rotateX: rotX, rotateY: rotY, transformPerspective: 1100 }}
            className="relative"
          >
            <div className="absolute -inset-6 rounded-[2rem] bg-fire/10 blur-3xl" aria-hidden />
            <div className="relative overflow-hidden rounded-3xl border border-line p-3 shadow-[0_0_90px_-30px_rgba(249,115,22,0.6)]" style={{ ...HERO_THEME, background: '#0b1322' }}>
              <span aria-hidden className="absolute left-3 top-3 z-10 size-3 border-l border-t border-fire/60" />
              <span aria-hidden className="absolute bottom-3 right-3 z-10 size-3 border-b border-r border-fire/60" />
              <Terrain theme="dark" intensity={0.9} play label="Pillars rising around the pointer, like hotspots" />
            </div>
          </motion.div>
        </section>

        {/* facts */}
        <section className="mx-auto max-w-6xl px-5 sm:px-8">
          <FactsStrip />
        </section>

        {/* interactive showcase */}
        <section id="explore" className="mx-auto max-w-7xl scroll-mt-20 px-5 py-28 sm:px-8">
          <SectionHeading kicker="How it works" title="From orbit to insight in five steps">
            Play with each figure — they respond to your pointer, and to your keyboard.
          </SectionHeading>
          <Reveal>
            <FigureShowcase scene={scene} onScene={setScene} />
          </Reveal>
        </section>

        {/* dashboard preview */}
        <section id="inside" className="mx-auto max-w-7xl scroll-mt-20 px-5 pb-28 sm:px-8">
          <SectionHeading kicker="Inside the dashboard" title="Everything you need to read the fire record">
            Jump straight into any view.
          </SectionHeading>
          <FeatureGrid />
        </section>

        {/* challenge & team */}
        <section id="team" className="mx-auto max-w-6xl scroll-mt-20 px-5 pb-28 sm:px-8">
          <Reveal className="mx-auto mb-16 max-w-3xl">
            <ChallengeCard />
          </Reveal>
          <TeamSection />
        </section>

        {/* closing CTA */}
        <section className="relative mx-auto max-w-4xl px-5 pb-40 text-center sm:px-8">
          <Reveal>
            <h2 className="text-3xl font-bold tracking-tight sm:text-5xl">
              Ready to <span className="text-heat">see the fire season?</span>
            </h2>
            <p className="mx-auto mt-4 max-w-lg text-muted">
              The dashboard currently runs on illustrative sample data while the harmonized dataset is being connected.
            </p>
            <div className="mt-8">
              <MagneticButton onClick={launch}>Launch dashboard</MagneticButton>
            </div>
          </Reveal>
        </section>

        <footer className="border-t border-line pb-24 pt-6 text-center font-mono text-[11px] uppercase tracking-[0.16em] text-faint">
          {PROJECT.name} · {PROJECT.challenge.event}
        </footer>
      </main>

      <KeyHints pressed={pressed} />

      {/* ignition transition into the dashboard */}
      <AnimatePresence>
        {leaving && (
          <>
            <motion.div
              aria-hidden
              className="pointer-events-none fixed z-[60] rounded-full"
              style={{
                left: leaving.x - 40,
                top: leaving.y - 40,
                width: 80,
                height: 80,
                background: 'radial-gradient(circle, #fde68a 0%, #f97316 45%, #7f1d1d 100%)',
              }}
              initial={{ scale: 0 }}
              animate={{ scale: 60 }}
              transition={{ duration: 0.65, ease: [0.5, 0, 0.75, 0] }}
            />
            <motion.div
              aria-hidden
              className="pointer-events-none fixed inset-0 z-[61] bg-bg"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, delay: 0.55 }}
            />
          </>
        )}
      </AnimatePresence>
    </div>
  )
}
