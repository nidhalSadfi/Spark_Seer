import { useState } from 'react'
import { ArrowRight, Bot, BrainCircuit, Gavel } from 'lucide-react'
import { Badge } from '../ui/primitives'
import { fmtInt } from '../../utils/format'
import { fmtKey, fmtMs, fmtUtc } from '../../utils/processing'

const KEYS_SHOWN = 12

function AgentCard({ agent, verdict }) {
  const Icon = agent.decides ? Gavel : Bot
  return (
    <div
      className={`relative flex min-w-0 flex-1 flex-col gap-1.5 rounded-xl border p-3 ${
        agent.decides ? 'border-fire/40 bg-fire/[0.07]' : 'border-line bg-sidebar/60'
      }`}
    >
      <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
        <Icon className={`size-4 ${agent.decides ? 'text-fire' : 'text-info'}`} aria-hidden /> {agent.name}
      </p>
      <p className="text-xs leading-relaxed text-muted">{agent.reviews}</p>
      <p className="mt-auto text-[11px] text-faint">
        {agent.payloadChars != null ? `Prompt summary: ${fmtInt(agent.payloadChars)} characters` : 'No prompt sent'}
      </p>
      {agent.decides && verdict && (
        <Badge tone={verdict.tone} className="self-start">
          verdict: {verdict.decision}
        </Badge>
      )}
    </div>
  )
}

/**
 * The multi-agent review of one job: the three CrewAI reviewers, the Validation reviewer's verdict
 * with its reasons and flagged cell-months, the model used and every LLM attempt (fallbacks).
 * `review` is utils/processing.js reviewView().
 */
export default function ReviewPanel({ review }) {
  const [allKeys, setAllKeys] = useState(false)
  const v = review.verdict
  const keys = v ? (allKeys ? v.flaggedKeys : v.flaggedKeys.slice(0, KEYS_SHOWN)) : []

  return (
    <section aria-labelledby="review-title" className="rounded-2xl border border-fire/35 bg-gradient-to-br from-fire/[0.08] to-transparent p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-fire/80">CrewAI · OpenRouter</p>
          <h3 id="review-title" className="flex items-center gap-2 text-[15px] font-semibold text-ink">
            <BrainCircuit className="size-4 text-fire" aria-hidden /> Multi-agent review
          </h3>
        </div>
        <Badge tone={review.info.tone}>{review.info.label}</Badge>
      </div>

      <p className="text-sm text-muted">{review.info.text}</p>
      <p className="mt-1 text-xs text-faint">{review.rule}</p>
      {review.reasonText && <p className="mt-2 text-sm text-yellow-100">{review.reasonText}</p>}

      <div className="mt-4 flex flex-col gap-2 md:flex-row md:items-stretch">
        {review.agents.map((a, i) => (
          <div key={a.id} className="flex min-w-0 flex-1 flex-col items-stretch gap-2 md:flex-row md:items-center">
            {i > 0 && <ArrowRight className="hidden size-4 shrink-0 self-center text-faint md:block" aria-hidden />}
            <AgentCard agent={a} verdict={review.verdict} />
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-faint">
        The Filter and Harmonization reviewers&apos; notes go to the Validation reviewer inside the run; only its verdict is
        stored. Each attempt sends at most one request per agent.
      </p>

      {v ? (
        <div className="mt-4 rounded-xl border border-line bg-sidebar/70 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs uppercase tracking-wider text-faint">Validation verdict</span>
            <Badge tone={v.tone} className="!text-sm">
              {v.decision}
            </Badge>
          </div>
          {v.reasons.length > 0 && (
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-sm text-muted">
              {v.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          <div className="mt-3">
            <p className="text-xs text-faint">
              Flagged cell-months: {v.flaggedKeys.length ? fmtInt(v.flaggedKeys.length) : 'none'}
              {v.flaggedByMonth.length > 0 && ` (${v.flaggedByMonth.map((m) => `${m.period}: ${m.n}`).join(', ')})`}
            </p>
            {keys.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {keys.map((k) => {
                  const f = fmtKey(k)
                  return (
                    <li key={f.key} className="rounded-md border border-line bg-card px-1.5 py-0.5 font-mono text-[11px] text-muted" title={`cell (cy, cx) = (${k[0]}, ${k[1]}), SW corner ${f.place}`}>
                      {f.period} · {f.place}
                    </li>
                  )
                })}
              </ul>
            )}
            {v.flaggedKeys.length > KEYS_SHOWN && (
              <button type="button" onClick={() => setAllKeys(!allKeys)} className="mt-1.5 text-xs text-info hover:text-sky-200">
                {allKeys ? 'Show fewer' : `Show all ${v.flaggedKeys.length}`}
              </button>
            )}
          </div>
        </div>
      ) : (
        review.aiReview && <p className="mt-4 text-sm text-muted">No verdict was recorded for this job.</p>
      )}

      <div className="mt-4">
        <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-sm font-semibold text-ink">LLM attempts</h4>
          <p className="text-[11px] text-faint">
            Model used: <span className="font-mono text-muted">{review.model ?? 'none'}</span>
            {review.modelsConfigured != null && ` · ${review.modelsConfigured} models configured`}
            {review.quotaRemaining != null && ` · ${review.quotaRemaining} free requests left before the review`}
          </p>
        </div>
        {review.fallback && (
          <p className="mb-2 rounded-lg border border-yellow-300/40 bg-yellow-300/10 px-2.5 py-1.5 text-xs text-yellow-100">
            Fallback between free models: {review.chain}
          </p>
        )}
        {review.attempts.length ? (
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-faint">
                  <th className="px-2.5 py-2">#</th>
                  <th className="px-2.5 py-2">Model</th>
                  <th className="px-2.5 py-2">Outcome</th>
                  <th className="px-2.5 py-2">Latency</th>
                  <th className="px-2.5 py-2">Tokens (prompt / completion / total)</th>
                  <th className="px-2.5 py-2">Error</th>
                </tr>
              </thead>
              <tbody>
                {review.attempts.map((a) => (
                  <tr key={a.id} className="border-b border-line/60 align-top text-muted last:border-0">
                    <td className="px-2.5 py-2 tabular-nums">{a.no}</td>
                    <td className="px-2.5 py-2 font-mono text-[11px] text-ink" title={fmtUtc(a.startedAt)}>
                      {a.model}
                    </td>
                    <td className="px-2.5 py-2">
                      <Badge tone={a.tone}>{a.outcome}</Badge>
                    </td>
                    <td className="px-2.5 py-2 whitespace-nowrap tabular-nums">{fmtMs(a.latencyMs)}</td>
                    <td className="px-2.5 py-2 whitespace-nowrap tabular-nums">
                      {a.tokens.total == null ? 'not reported' : `${fmtInt(a.tokens.prompt)} / ${fmtInt(a.tokens.completion)} / ${fmtInt(a.tokens.total)}`}
                    </td>
                    <td className="px-2.5 py-2">
                      {a.errorClass ? (
                        <span title={a.error ?? undefined}>
                          <span className="font-mono text-[11px] text-orange-200">{a.errorClass}</span>
                          {a.error && <span className="block text-[11px] text-faint">{a.error}</span>}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted">No model was called for this job.</p>
        )}
      </div>
    </section>
  )
}
