import { AlertTriangle, FileCheck2, Loader2, Sparkles } from 'lucide-react'
import { Badge } from '../ui/primitives'
import { usePassport } from '../../hooks/useFireData'
import { fmtInt } from '../../utils/format'
import { FLAGS } from '../../services/constants'

const Row = ({ label, value }) => (
  <div className="flex items-baseline justify-between gap-3 py-1 text-xs">
    <dt className="text-muted">{label}</dt>
    <dd className="text-right font-medium tabular-nums text-ink">{value}</dd>
  </div>
)

function Passport({ p }) {
  const { observations: obs, seasonal, data_support: support } = p
  const flag = FLAGS[seasonal.flag]
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {flag && <Badge tone={flag.tone}>{flag.label}</Badge>}
        {p.ai_checked && p.ai_text && (
          <Badge tone="info" title="Plain-language wording by an AI model, kept only when every number matches the passport.">
            <Sparkles className="size-3" aria-hidden /> AI wording · numbers verified
          </Badge>
        )}
      </div>

      <p className="leading-relaxed text-ink">{p.ai_checked && p.ai_text ? p.ai_text : p.summary_template}</p>
      <p className="text-xs italic text-muted">{p.confidence_statement}</p>

      <dl className="divide-y divide-line rounded-xl border border-line bg-sidebar/60 px-3 py-1">
        <Row label="MODIS (raw)" value={fmtInt(obs.modis_raw)} />
        <Row label="VIIRS (raw)" value={fmtInt(obs.viirs_raw)} />
        <Row label={`Harmonized · ${obs.source}`} value={fmtInt(obs.harmonized)} />
        {obs.k_global != null && <Row label="MODIS→VIIRS ratio k" value={obs.k_global} />}
        <Row label={`Median, same month ${seasonal.median_years?.join('–')}`} value={fmtInt(seasonal.median_same_month)} />
        <Row label="Robust z-score" value={seasonal.z ?? 'n/a'} />
        <Row label="MODIS detections behind ratio" value={fmtInt(support.support)} />
        <Row label="Cells with detections" value={fmtInt(support.cells)} />
      </dl>

      {p.quality_warnings?.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {p.quality_warnings.map((w) => (
            <li key={w} className="flex gap-2 text-xs leading-relaxed text-muted">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-fire" aria-hidden />
              {w}
            </li>
          ))}
        </ul>
      )}

      <details className="text-xs text-muted">
        <summary className="cursor-pointer select-none text-faint hover:text-ink">Sources</summary>
        <ul className="mt-1.5 list-disc pl-4">
          {p.sources.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}

/** "Evidence Passport" for one calendar cell: POST /investigate on demand. `month` is 1-12. */
export default function PassportSection({ year, month }) {
  const passport = usePassport()
  return (
    <section className="border-t border-line pt-4" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <h5 className="font-mono text-[11px] font-medium uppercase tracking-[0.16em] text-fire">Evidence passport</h5>
        {!passport.data && (
          <button
            type="button"
            onClick={() => passport.mutate({ year, month })}
            disabled={passport.isPending}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-fire/50 bg-fire/10 px-3 text-xs font-medium text-orange-200 transition-colors hover:bg-fire/20 disabled:opacity-60"
          >
            {passport.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <FileCheck2 className="size-3.5" aria-hidden />}
            {passport.isPending ? 'Building…' : 'Generate'}
          </button>
        )}
      </div>
      {!passport.data && !passport.isError && (
        <p className="mt-2 text-xs text-faint">A sourced, plain-language summary of this month with its caveats.</p>
      )}
      {passport.isError && (
        <p className="mt-2 text-xs text-fire-high">Could not build the passport: {passport.error.message}</p>
      )}
      {passport.data && (
        <div className="mt-3">
          <Passport p={passport.data} />
        </div>
      )}
    </section>
  )
}
