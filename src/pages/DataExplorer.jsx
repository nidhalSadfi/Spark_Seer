import { useMemo, useState } from 'react'
import { flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, Download, FileText } from 'lucide-react'
import { toast } from 'sonner'
import { motion } from 'motion/react'
import { ActionButton, Badge, Card, CardHeader, Notice, PageHeader, Select, Skeleton } from '../components/ui/primitives'
import { DataSourceNotice, EmptyAreaNotice } from '../components/common/DataSource'
import { useCalendarRows } from '../hooks/useFireData'
import { useFilters } from '../context/FilterContext'
import { FLAGS } from '../services/constants'
import { downloadText, fmtInt, toCsv } from '../utils/format'

const dash = (v, fmt = fmtInt) => (v == null ? '—' : fmt(v))
const one = (v) => v.toFixed(1)

const COLUMNS = [
  { accessorKey: 'period', header: 'Month' },
  { accessorKey: 'modis_raw', header: 'MODIS raw', cell: (c) => dash(c.getValue()) },
  { accessorKey: 'viirs_raw', header: 'VIIRS raw', cell: (c) => dash(c.getValue()) },
  { accessorKey: 'harmonized', header: 'Harmonized', cell: (c) => dash(c.getValue(), one) },
  { accessorKey: 'support', header: 'Support', cell: (c) => dash(c.getValue()) },
  { accessorKey: 'z', header: 'z-score', cell: (c) => dash(c.getValue(), one) },
  { accessorKey: 'flag', header: 'Flag' },
]

const CSV_COLUMNS = [
  { key: 'year', label: 'year' },
  { key: 'month', label: 'month' },
  { key: 'modis_raw', label: 'modis_raw' },
  { key: 'viirs_raw', label: 'viirs_raw' },
  { key: 'harmonized', label: 'harmonized_viirs_equivalent' },
  { key: 'support', label: 'support' },
  { key: 'z', label: 'z' },
  { key: 'flag', label: 'flag' },
]

const FLAG_FILTERS = [{ value: 'all', label: 'All months' }, ...Object.entries(FLAGS).map(([value, f]) => ({ value, label: f.label }))]

export default function DataExplorer() {
  const { target, startYear, endYear } = useFilters()
  const [flag, setFlag] = useState('all')
  const [sorting, setSorting] = useState([{ id: 'period', desc: true }])

  const { data, isFetching, error } = useCalendarRows()
  const rows = useMemo(
    () => (data ?? []).filter((r) => r.year >= startYear && r.year <= endYear && (flag === 'all' || r.flag === flag)),
    [data, startYear, endYear, flag],
  )

  const table = useReactTable({
    data: rows,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 15 } },
  })

  const exportCsv = () => {
    if (!rows.length) return toast.error('No records to export for the current filters.')
    const name = `spark-seer_${target.key.replace(/[^a-z0-9.-]+/gi, '_')}_${startYear}-${endYear}.csv`
    downloadText(name, toCsv(rows, CSV_COLUMNS))
    toast.success(`Exported ${fmtInt(rows.length)} monthly records`, { description: name })
  }

  return (
    <>
      <PageHeader
        kicker="Retrieval"
        title="Data Explorer & Export"
        description={`Monthly harmonized records for ${target.name}. Area and year range come from the header filters.`}
        actions={
          <div className="flex gap-2">
            <ActionButton icon={FileText} onClick={() => toast.info('AOI report generation is planned for a later iteration.')}>
              AOI report
            </ActionButton>
            <ActionButton variant="primary" icon={Download} onClick={exportCsv}>
              Download CSV
            </ActionButton>
          </div>
        }
      />

      {data?.length === 0 && (
        <div className="mb-4">
          <EmptyAreaNotice />
        </div>
      )}
      {error && <p className="mb-4 text-sm text-fire-high">Could not load records: {error.message}</p>}

      <Card>
        <CardHeader title="Filters" action={<Badge>{isFetching && !data ? 'Loading…' : `${fmtInt(rows.length)} months`}</Badge>} />
        <div className="flex flex-wrap items-end gap-4">
          <Select label="Flag" value={flag} onChange={setFlag} options={FLAG_FILTERS} />
        </div>
      </Card>

      <Card className="mt-4 !p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id} className="border-b border-line">
                  {hg.headers.map((h) => {
                    const dir = h.column.getIsSorted()
                    const Icon = dir === 'asc' ? ArrowUp : dir === 'desc' ? ArrowDown : ArrowUpDown
                    return (
                      <th key={h.id} className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-muted" aria-sort={dir ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                        <button onClick={h.column.getToggleSortingHandler()} className="inline-flex items-center gap-1.5 hover:text-ink">
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          <Icon className={`size-3 ${dir ? 'text-fire' : 'text-faint'}`} aria-hidden />
                        </button>
                      </th>
                    )
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {!data
                ? Array.from({ length: 6 }).map((_, i) => (
                    <tr key={i}>
                      <td colSpan={COLUMNS.length} className="px-4 py-2">
                        <Skeleton className="h-6 w-full" />
                      </td>
                    </tr>
                  ))
                : table.getRowModel().rows.map((row, i) => (
                    <motion.tr
                      key={row.id}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.25, delay: Math.min(i, 14) * 0.02 }}
                      className="border-b border-line/60 transition-colors hover:bg-fire/[0.06]"
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-2.5 tabular-nums text-muted">
                          {cell.column.id === 'flag' ? (
                            <Badge tone={FLAGS[cell.getValue()]?.tone}>{FLAGS[cell.getValue()]?.short ?? cell.getValue()}</Badge>
                          ) : (
                            flexRender(cell.column.columnDef.cell, cell.getContext())
                          )}
                        </td>
                      ))}
                    </motion.tr>
                  ))}
              {data && !rows.length && (
                <tr>
                  <td colSpan={COLUMNS.length} className="px-4 py-10 text-center text-muted">
                    No months match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 text-sm text-muted">
          <span>
            Page {table.getState().pagination.pageIndex + 1} of {Math.max(1, table.getPageCount())}
          </span>
          <div className="flex gap-2">
            <button onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} className="h-8 rounded-lg border border-line px-3 hover:text-ink disabled:opacity-40">Previous</button>
            <button onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} className="h-8 rounded-lg border border-line px-3 hover:text-ink disabled:opacity-40">Next</button>
          </div>
        </div>
      </Card>

      <div className="mt-4 flex flex-col gap-3">
        <Notice title="Reading the columns.">
          <strong>Harmonized</strong> is VIIRS-equivalent: MODIS × a fitted ratio before 2012, observed VIIRS from 2012.
          Raw MODIS and VIIRS counts are not directly comparable. VIIRS has no record before 2012 (shown as —).{' '}
          <strong>Support</strong> is the number of MODIS training detections behind the ratio for that calendar month.
          The z-score is robust (median/MAD) against the same month in other years; |z| ≥ 3 is flagged.
        </Notice>
        <DataSourceNotice />
      </div>
    </>
  )
}
