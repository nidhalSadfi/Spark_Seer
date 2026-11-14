import { useMemo, useState } from 'react'
import { flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, Download, FileText } from 'lucide-react'
import { toast } from 'sonner'
import { motion } from 'motion/react'
import { ActionButton, Badge, Card, CardHeader, IllustrativeNotice, Notice, PageHeader, Select, Skeleton } from '../components/ui/primitives'
import { useHotspotRecords } from '../hooks/useFireData'
import { useFilters } from '../context/FilterContext'
import { CONFIDENCE_LEVELS, regionName, sensorLabel } from '../services/constants'
import { downloadText, fmtInt, toCsv } from '../utils/format'

const COLUMNS = [
  { accessorKey: 'date', header: 'Date' },
  { accessorKey: 'sensor', header: 'Sensor' },
  { accessorKey: 'lat', header: 'Latitude' },
  { accessorKey: 'lon', header: 'Longitude' },
  { accessorKey: 'confidence', header: 'Confidence' },
  { accessorKey: 'frp', header: 'FRP (MW)' },
  { accessorKey: 'brightness', header: 'Brightness (K)' },
]

const CSV_COLUMNS = [
  { key: 'id', label: 'id' },
  { key: 'date', label: 'date' },
  { key: 'sensor', label: 'sensor' },
  { key: 'lat', label: 'latitude' },
  { key: 'lon', label: 'longitude' },
  { key: 'confidence', label: 'confidence' },
  { key: 'frp', label: 'frp_mw' },
  { key: 'brightness', label: 'brightness_k' },
]

const confTone = { high: 'fire', nominal: 'info', low: 'neutral' }

export default function DataExplorer() {
  const { region, sensor } = useFilters()
  const [from, setFrom] = useState('2026-08-01')
  const [to, setTo] = useState('2026-10-31')
  const [confidence, setConfidence] = useState('all')
  const [sorting, setSorting] = useState([])

  const { data, isFetching } = useHotspotRecords({ region, sensor, confidence, from, to })
  const rows = useMemo(() => data ?? [], [data])

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
    const name = `spark-seer_${region}_${sensor}_${from}_${to}.csv`
    downloadText(name, toCsv(rows, CSV_COLUMNS))
    toast.success(`Exported ${fmtInt(rows.length)} records`, { description: name })
  }

  return (
    <>
      <PageHeader
        kicker="Retrieval"
        title="Data Explorer & Export"
        description={`Hotspot records for ${regionName(region)} · ${sensorLabel(sensor)}. Region and sensor come from the header filters.`}
        actions={
          <div className="flex gap-2">
            <ActionButton
              icon={FileText}
              onClick={() => toast.info('AOI report generation is planned for a later iteration.')}
            >
              AOI report
            </ActionButton>
            <ActionButton variant="primary" icon={Download} onClick={exportCsv}>
              Download CSV
            </ActionButton>
          </div>
        }
      />

      <Card>
        <CardHeader
          title="Filters"
          action={<Badge>{isFetching ? 'Loading…' : `${fmtInt(rows.length)} records`}</Badge>}
        />
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-faint">
            From
            <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} className="h-9 rounded-lg border border-line bg-sidebar px-2.5 text-sm font-normal normal-case text-ink" />
          </label>
          <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-faint">
            To
            <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} className="h-9 rounded-lg border border-line bg-sidebar px-2.5 text-sm font-normal normal-case text-ink" />
          </label>
          <Select
            label="Confidence"
            value={confidence}
            onChange={setConfidence}
            options={CONFIDENCE_LEVELS.map((c) => ({ value: c.id, label: c.label }))}
          />
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
              {isFetching && !rows.length
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
                          {cell.column.id === 'confidence' ? (
                            <Badge tone={confTone[cell.getValue()]}>{cell.getValue()}</Badge>
                          ) : (
                            flexRender(cell.column.columnDef.cell, cell.getContext())
                          )}
                        </td>
                      ))}
                    </motion.tr>
                  ))}
              {!isFetching && !rows.length && (
                <tr>
                  <td colSpan={COLUMNS.length} className="px-4 py-10 text-center text-muted">
                    No hotspot records match these filters.
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
        <Notice title="Scientific note.">
          Under the Harmonized view, each record keeps its originating sensor. Raw MODIS and VIIRS counts are not
          directly comparable; harmonization method: placeholder-v0.
        </Notice>
        <IllustrativeNotice />
      </div>
    </>
  )
}
