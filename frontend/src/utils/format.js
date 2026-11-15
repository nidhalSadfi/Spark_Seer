const nf = new Intl.NumberFormat('en-US')

export const fmtInt = (n) => (n == null ? '—' : nf.format(Math.round(n)))

/** Compact axis label: 800 → '800', 12000 → '12k'. */
export const fmtAxis = (v) => (Math.abs(v) >= 1000 ? `${+(v / 1000).toFixed(1)}k` : String(v))

export function fmtPct(p, digits = 0) {
  if (p == null) return 'n/a'
  const s = p.toFixed(digits)
  return `${p > 0 ? '+' : ''}${s}%`
}

/** Tailwind text color for an anomaly value. Red is reserved for strongly elevated activity. */
export function anomalyTone(p) {
  if (p == null) return 'text-muted'
  if (p >= 50) return 'text-fire-high'
  if (p >= 15) return 'text-fire'
  if (p <= -15) return 'text-info'
  return 'text-ok'
}

export function toCsv(rows, columns) {
  const esc = (v) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const head = columns.map((c) => esc(c.label)).join(',')
  const body = rows.map((r) => columns.map((c) => esc(r[c.key])).join(','))
  return [head, ...body].join('\n')
}

export function downloadText(filename, text, mime = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
