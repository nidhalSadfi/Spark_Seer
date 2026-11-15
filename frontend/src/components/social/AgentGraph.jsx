import { useMemo, useRef, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import './agentGraph.css'
import { FLOW_LINKS, FLOW_NODES, linkState } from '../../utils/agentFlow'

/**
 * The incident agents as a node graph, drawn in Hairline's style (see agentGraph.css): isometric
 * tiles for the agents, sources and steps, dashed links that turn solid as data flows. It shows the
 * state of one run (utils/agentFlow.js `flowFromEvents`): a tile rises while its agent works, its LED
 * says working / done / failed / skipped, and a few words under it say what it produced.
 *
 * Pointer and keyboard like a Hairline figure: the tile under the pointer rises, its links turn solid
 * and its neighbours rise a little; Tab or the arrow keys walk the tiles; Enter picks one (and
 * `onSelect` filters the activity log to it); Esc lets go.
 */

// Floor grid → screen: U runs along the flow, V across it (a skewed isometric floor).
const U = [84, 13]
const V = [-34, 64]
const HALF = 0.2 // half of a tile's footprint, in grid units
const HEIGHT = { llm: 15, code: 9, source: 7 }
const CHIP = 6 // the step on top of a model agent's tile

const r1 = (n) => Math.round(n * 10) / 10
const at = (u, v) => [u * U[0] + v * V[0], u * U[1] + v * V[1]]
const poly = (pts) => `M${pts.map((p) => `${r1(p[0])} ${r1(p[1])}`).join('L')}Z`

// a tile's corners around its centre: back, right, front, left
const CORNERS = [
  [-HALF, -HALF],
  [HALF, -HALF],
  [HALF, HALF],
  [-HALF, HALF],
].map(([du, dv]) => [du * U[0] + dv * V[0], du * U[1] + dv * V[1]])

/** A block of the given scale between two heights: its top face, two visible side faces and outline. */
function block(scale, z0, z1) {
  const c = CORNERS.map(([x, y]) => [x * scale, y * scale])
  const top = c.map(([x, y]) => [x, y - z1])
  const bottom = c.map(([x, y]) => [x, y - z0])
  return {
    top: poly(top),
    left: poly([top[3], top[2], bottom[2], bottom[3]]),
    right: poly([top[2], top[1], bottom[1], bottom[2]]),
  }
}
const SHAPES = Object.fromEntries(
  Object.entries(HEIGHT).map(([kind, h]) => [kind, { tile: block(1, 0, h), chip: kind === 'llm' ? block(0.5, h, h + CHIP) : null }]),
)
const FOOTPRINT = poly(CORNERS)

// where every node and link sits, and the viewBox around them (computed once: the graph is fixed)
const NODES = FLOW_NODES.map((n) => ({ ...n, c: at(n.u, n.v) })).sort((a, b) => a.c[1] - b.c[1]) // back to front
const BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n]))
const LINKS = FLOW_LINKS.map(([from, to]) => ({ from, to, path: `M${BY_ID[from].c.map(r1).join(' ')}L${BY_ID[to].c.map(r1).join(' ')}` }))
const BOUNDS = (() => {
  const xs = NODES.map((n) => n.c[0])
  const ys = NODES.map((n) => n.c[1])
  const x0 = Math.min(...xs) - 44
  const y0 = Math.min(...ys) - 52
  return { x0, y0, w: Math.max(...xs) + 44 - x0, h: Math.max(...ys) + 58 - y0 }
})()
const VIEWBOX = `${r1(BOUNDS.x0)} ${r1(BOUNDS.y0)} ${r1(BOUNDS.w)} ${r1(BOUNDS.h)}`

const WORD = { idle: 'waiting', running: 'working', ok: 'done', error: 'failed', skipped: 'skipped', warn: 'warning' }
const KIND_WORD = { llm: 'model agent', code: 'step', source: 'source' }

export default function AgentGraph({ flow, selected = null, onSelect }) {
  const reduceMotion = useReducedMotion()
  const [hover, setHover] = useState(null)
  const tiles = useRef({})

  const focusId = hover ?? selected
  const near = useMemo(() => new Set(hover ? FLOW_LINKS.filter((l) => l.includes(hover)).flat() : []), [hover])
  const info = focusId ? flow.nodes[focusId] : null
  const meta = focusId ? BY_ID[focusId] : null

  const walk = (id, step) => {
    const i = FLOW_NODES.findIndex((n) => n.id === id)
    const next = FLOW_NODES[(i + step + FLOW_NODES.length) % FLOW_NODES.length]
    tiles.current[next.id]?.focus()
  }
  const onKey = (e, id) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') walk(id, 1)
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') walk(id, -1)
    else if (e.key === 'Enter' || e.key === ' ') onSelect?.(selected === id ? null : id)
    else if (e.key === 'Escape') onSelect?.(null)
    else return
    e.preventDefault()
  }

  return (
    <div className="ag" role="group" aria-label="The incident agents as a graph of steps. Tab or the arrow keys move between them; Enter filters the activity to one.">
      <svg viewBox={VIEWBOX} role="presentation">
        <g>
          {LINKS.map((l) => {
            const state = linkState(flow.nodes, l.from, l.to)
            const hot = hover != null && (l.from === hover || l.to === hover)
            return (
              <g key={`${l.from}-${l.to}`}>
                <path className={`lk lk-${state}${hot ? ' hot' : ''}`} d={l.path} />
                {state === 'live' && !reduceMotion && (
                  <circle className="pkt" r="2.4">
                    <animateMotion dur="1.3s" repeatCount="indefinite" path={l.path} />
                  </circle>
                )}
              </g>
            )
          })}
        </g>
        <g>
          {NODES.map((n) => {
            const st = flow.nodes[n.id]
            const shape = SHAPES[n.kind]
            const running = st.status === 'running'
            const lift = Math.min(16, (running ? 10 : 0) + (selected === n.id ? 4 : 0) + (hover === n.id ? 6 : near.has(n.id) ? 2 : 0))
            const topZ = HEIGHT[n.kind] + (shape.chip ? CHIP : 0)
            return (
              <g
                key={n.id}
                ref={(el) => {
                  tiles.current[n.id] = el
                }}
                transform={`translate(${r1(n.c[0])} ${r1(n.c[1])})`}
                className={`node st-${st.status}${selected === n.id ? ' sel' : ''}${lift > 0 ? ' up' : ''}`}
                tabIndex={0}
                role="button"
                aria-pressed={selected === n.id}
                aria-label={`${n.label}, ${KIND_WORD[n.kind]}: ${WORD[st.status]}${st.badge ? `, ${st.badge}` : ''}. ${n.role}`}
                onPointerEnter={() => setHover(n.id)}
                onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
                onFocus={() => setHover(n.id)}
                onBlur={() => setHover((h) => (h === n.id ? null : h))}
                onClick={() => onSelect?.(selected === n.id ? null : n.id)}
                onKeyDown={(e) => onKey(e, n.id)}
              >
                <path className="ghost nf" d={FOOTPRINT} />
                <g className="lift" style={{ transform: `translateY(${-lift}px)` }}>
                  <path className="side" d={shape.tile.left} />
                  <path className="side" d={shape.tile.right} />
                  <path className="top" d={shape.tile.top} />
                  {shape.chip && (
                    <>
                      <path className="side" d={shape.chip.left} />
                      <path className="side" d={shape.chip.right} />
                      <path className="chip" d={shape.chip.top} />
                    </>
                  )}
                  {n.kind === 'source' && <ellipse className="ring nf" cx="0" cy={-HEIGHT.source} rx="12" ry="6.5" />}
                  <ellipse className="led" cx="0" cy={-topZ} rx="3.4" ry="2" />
                  {running && !reduceMotion && <ellipse className="pulse nf" cx="0" cy={-topZ} rx="3.4" ry="2" />}
                </g>
                <text className="lbl" y="31">
                  {n.label}
                </text>
                {st.badge && (
                  <text className="bdg" y="42">
                    {st.badge}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>

      <p className="min-h-10 px-1 pt-2 text-xs leading-relaxed text-muted" aria-live="polite">
        {meta && info ? (
          <>
            <b className="font-semibold text-ink">{meta.label}</b> <span className="text-faint">· {KIND_WORD[meta.kind]} · {WORD[info.status]}</span>. {meta.role}
            {info.last && <span className="block truncate text-faint">Latest: {info.last}</span>}
          </>
        ) : (
          <span className="text-faint">Hover or focus a tile to read what that agent does and what it said last. Click one to filter the activity below.</span>
        )}
      </p>
    </div>
  )
}
