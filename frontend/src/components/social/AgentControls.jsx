import { useState } from 'react'
import { CalendarClock, Check, Info, KeyRound, LoaderCircle, Play, Trash2, X } from 'lucide-react'
import { AGENT_KEYS } from '../../utils/incidents'

const MIN_REPLAY_DATE = '2026-08-03' // Kanari's fire archive starts here
const MAX_REPLAY_DAYS = 31
const HOURS = [6, 12, 24, 48, 72]
const DAY = 864e5

const field = 'w-full rounded-lg border border-line bg-sidebar px-2.5 text-sm text-ink placeholder:text-faint hover:border-faint'

/** Why this search cannot be started yet, or null. `latest` is the newest date a replay may end on. */
function problem({ query, period, from, to }, latest) {
  if (query.trim().length < 2) return 'Type a country or zone to search (at least 2 characters).'
  if (period !== 'past') return null
  if (!from || !to) return 'Pick both dates.'
  if (from > to) return '“From” must be on or before “To”.'
  if (from < MIN_REPLAY_DATE) return 'Kanari’s archive starts on 3 Aug 2026, so earlier dates cannot be replayed.'
  if (to > latest) return 'A replay can end yesterday at the latest.'
  if ((Date.parse(to) - Date.parse(from)) / DAY + 1 > MAX_REPLAY_DAYS) return `A replay covers at most ${MAX_REPLAY_DAYS} days.`
  return null
}

const fmtIn = (ms) => {
  const min = Math.round(ms / 60000)
  if (min <= 0) return 'now'
  return min < 90 ? `in ${min} min` : `in ${(min / 60).toFixed(1)} h`
}

/**
 * Search form of the agent console: the text to search, live (last N hours) or a replay of past
 * dates, and schedules. Starting a run asks a model and searches X, Bluesky and GDELT: it uses the
 * free quotas of the agents' keys, so the form says so and waits while a run is going on.
 */
export default function AgentControls({ running, starting, schedules, scheduling, now, agents, onRun, onSchedule, onRemoveSchedule }) {
  const [latest] = useState(() => new Date(Date.now() - DAY).toISOString().slice(0, 10)) // yesterday, UTC
  const [form, setForm] = useState(() => {
    const from = new Date(Date.parse(latest) - 2 * DAY).toISOString().slice(0, 10)
    return { query: 'Tunisia and northern Algeria', period: 'recent', hours: 24, from: from < MIN_REPLAY_DATE ? MIN_REPLAY_DATE : from, to: latest, every: 60 }
  })
  const set = (patch) => setForm((f) => ({ ...f, ...patch }))
  const why = problem(form, latest)
  const past = form.period === 'past'
  // which keys the agents can see (GET /api/status): a run cannot even start without the model's
  const keys = agents?.keys
  const noModel = keys != null && !keys.groq
  const missing = keys ? AGENT_KEYS.filter((k) => !keys[k.id]) : []

  const run = (e) => {
    e.preventDefault()
    if (why) return
    const query = form.query.trim()
    onRun(past ? { query, replay_from: form.from, replay_to: form.to } : { query, hours: form.hours })
  }

  return (
    <section className="rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31] p-4" aria-label="Start a search">
      <h2 className="text-[15px] font-semibold text-ink">Search</h2>
      <p className="mt-0.5 text-xs text-muted">Type countries or zones. The agents search the sources and return incident tickets.</p>

      <form onSubmit={run} className="mt-3 flex flex-col gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">Where</span>
          <textarea
            value={form.query}
            onChange={(e) => set({ query: e.target.value })}
            maxLength={300}
            rows={2}
            placeholder="e.g. France, or: forest fires in Kabylie and Tizi Ouzou"
            className={`${field} resize-none py-2`}
          />
        </label>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">Period</legend>
          <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Period">
            {[
              ['recent', 'Recent hours'],
              ['past', 'Past dates'],
            ].map(([id, label]) => (
              <label
                key={id}
                className={`cursor-pointer rounded-lg border px-2.5 py-2 text-center text-xs transition-colors has-focus-visible:outline-2 has-focus-visible:outline-info ${
                  form.period === id ? 'border-fire/50 bg-fire/10 font-medium text-orange-100' : 'border-line text-muted hover:border-faint hover:text-ink'
                }`}
              >
                <input type="radio" name="period" value={id} checked={form.period === id} onChange={() => set({ period: id })} className="sr-only" />
                {label}
              </label>
            ))}
          </div>
          {past ? (
            <>
              <div className="grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-1 text-[11px] text-faint">
                  From
                  <input type="date" min={MIN_REPLAY_DATE} max={latest} value={form.from} onChange={(e) => set({ from: e.target.value })} className={`${field} h-9`} />
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-faint">
                  To
                  <input type="date" min={MIN_REPLAY_DATE} max={latest} value={form.to} onChange={(e) => set({ to: e.target.value })} className={`${field} h-9`} />
                </label>
              </div>
              <p className="text-[11px] leading-relaxed text-faint">
                From 3 Aug 2026 (the start of Kanari’s fire archive) to yesterday, up to 31 days. The agents start from the fires the satellites saw
                then and search posts around each one. Replay tickets get a Replay badge and never mix with live ones.
              </p>
            </>
          ) : (
            <label className="flex items-center gap-2 text-xs text-muted">
              Last
              <select value={form.hours} onChange={(e) => set({ hours: +e.target.value })} className={`${field} h-9 w-auto`}>
                {HOURS.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
              hours
            </label>
          )}
        </fieldset>

        {why && form.query.trim().length >= 2 && <p className="text-xs text-yellow-200">{why}</p>}

        {keys && (
          <div className="rounded-lg border border-line bg-sidebar/60 px-3 py-2.5 text-xs">
            <p className="mb-1.5 flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">
              <KeyRound className="size-3.5" aria-hidden /> Keys the agents can see
            </p>
            <ul className="flex flex-wrap gap-x-4 gap-y-1">
              {AGENT_KEYS.map((k) => (
                <li key={k.id} className={`flex items-center gap-1 ${keys[k.id] ? 'text-muted' : k.required ? 'text-red-300' : 'text-yellow-200'}`} title={keys[k.id] ? undefined : k.without}>
                  {keys[k.id] ? <Check className="size-3.5 text-ok" aria-hidden /> : <X className="size-3.5" aria-hidden />}
                  {k.label}
                  <span className="sr-only">{keys[k.id] ? ' is set' : ` is missing. ${k.without}`}</span>
                </li>
              ))}
            </ul>
            {missing.length > 0 && (
              <p className="mt-1.5 leading-relaxed text-faint">
                {noModel
                  ? 'Searches need the Groq key. Ask whoever runs this dashboard to add it.'
                  : 'Sources without a key are skipped; the others still run.'}
              </p>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={Boolean(why) || running || starting || noModel}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-fire px-4 text-sm font-semibold text-[#1a0d02] transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:bg-line disabled:text-faint"
        >
          {starting || running ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Play className="size-4 fill-current" aria-hidden />}
          {running ? 'A run is in progress…' : starting ? 'Starting…' : noModel ? 'Groq key needed' : past ? 'Run on these dates' : 'Run now'}
        </button>
        <p className="flex items-start gap-2 text-[11px] leading-relaxed text-faint">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            A run asks a model and searches X, Bluesky and GDELT: about 15 to 25 model calls, against the free quotas of the keys the agents hold.
            Only one run goes at a time.
          </span>
        </p>
      </form>

      {!past && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-2 flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">
            <CalendarClock className="size-3.5" aria-hidden /> Schedule
          </p>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <label className="flex items-center gap-2">
              Every
              <input
                type="number"
                min={15}
                max={1440}
                value={form.every}
                onChange={(e) => set({ every: +e.target.value })}
                className={`${field} h-9 w-20`}
                aria-label="Minutes between runs"
              />
              min
            </label>
            <button
              type="button"
              disabled={Boolean(why) || scheduling || form.every < 1}
              onClick={() => onSchedule({ query: form.query.trim(), hours: form.hours, interval_min: form.every })}
              className="inline-flex h-9 items-center rounded-lg border border-line px-3 text-ink transition-colors hover:border-fire/40 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Schedule this search
            </button>
          </div>
          <p className="mt-1.5 text-[11px] text-faint">Live runs only; at most one every 15 minutes, to protect the free quotas.</p>

          {schedules.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1.5">
              {schedules.map((s) => (
                <li key={s.id} className="flex items-start justify-between gap-2 rounded-lg border border-line bg-sidebar/60 px-3 py-2 text-xs text-muted">
                  <span className="min-w-0">
                    <b className="block truncate font-medium text-ink">“{s.query}”</b>
                    every {s.intervalMin} min, last {s.hours} h{s.nextRunAt != null && ` · next ${fmtIn(s.nextRunAt - now)}`}
                  </span>
                  <button
                    type="button"
                    onClick={() => onRemoveSchedule(s.id)}
                    aria-label={`Remove the schedule for ${s.query}`}
                    className="shrink-0 rounded-md p-1 text-faint transition-colors hover:bg-fire-high/15 hover:text-red-300"
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
