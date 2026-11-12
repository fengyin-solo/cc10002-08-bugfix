/** 数据建模 / 维度管理共用的结构化字段渲染与登记表单。 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { request } from '@/api/client'

export type Row = Record<string, string | number | null | unknown>

/** 结构化数组里每一列的声明。 */
export interface StructColumn {
  key: string
  label: string
  required?: boolean
  placeholder?: string
  /** 关联维度专用：候选维度编号 -> 名称，选中后自动带出名称。 */
  options?: { code: string; name: string }[]
}

export interface FieldDef {
  key: string
  label: string
  required?: boolean
  kind: 'text' | 'structured' | 'partition'
  columns?: StructColumn[]
  placeholder?: string
  multiline?: boolean
}

/** 列表单元格：结构化值显示摘要徽标，历史文本保持原文，不把对象渲染成 [object Object]。 */
export function StructuredCell({ value }: { value: unknown }): ReactNode {
  if (value === null || value === undefined || value === '') {
    return <span className="cell-empty">—</span>
  }
  if (Array.isArray(value)) {
    return (
      <div className="chip-stack">
        {value.map((item, index) => {
          const label =
            item && typeof item === 'object'
              ? Object.entries(item as Record<string, unknown>)
                  .filter(([, v]) => v !== '' && v !== null && v !== undefined)
                  .map(([k, v]) => `${k}:${String(v)}`)
                  .join(' / ')
              : String(item)
          return (
            <span key={index} className="chip">
              {label}
            </span>
          )
        })}
      </div>
    )
  }
  return <span className="cell-legacy" title={String(value)}>{String(value)}</span>
}

function isBlankScalar(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === 'string' && value.trim() === '')
}

function toInitialValue(value: unknown, def: FieldDef): unknown {
  if (def.kind === 'text') {
    return value === null || value === undefined ? '' : String(value)
  }
  if (def.kind === 'partition') {
    if (Array.isArray(value)) return value.map((v) => String(v))
    return value ? [String(value)] : []
  }
  if (Array.isArray(value)) {
    return value.map((item) =>
      item && typeof item === 'object' ? { ...(item as Record<string, unknown>) } : { [def.columns?.[0]?.key ?? '值']: String(item) },
    )
  }
  // 历史文本不进结构化编辑器，单独用文本框承载，避免覆盖原文。
  return value ? [structuredPlaceholderRow(def)] : [structuredPlaceholderRow(def)]
}

function structuredPlaceholderRow(def: FieldDef): Record<string, string> {
  const row: Record<string, string> = {}
  for (const col of def.columns ?? []) row[col.key] = ''
  return row
}

interface ModalFormProps {
  title: string
  fields: FieldDef[]
  initial: Record<string, unknown> | null
  endpoint: string
  onClose: () => void
  onSaved: () => void
}

/** 通用登记/编辑弹窗：保存的是服务端返回的同一份数据，页面不缓存业务值。 */
export function ModalForm({ title, fields, initial, endpoint, onClose, onSaved }: ModalFormProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const draft: Record<string, unknown> = {}
    for (const def of fields) draft[def.key] = toInitialValue(initial?.[def.key], def)
    return draft
  })
  const [legacyText, setLegacyText] = useState<Record<string, string>>(() => {
    const draft: Record<string, string> = {}
    for (const def of fields) {
      const value = initial?.[def.key]
      if (def.kind !== 'text' && typeof value === 'string') draft[def.key] = value
    }
    return draft
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const legacyKeys = useMemo(() => new Set(Object.keys(legacyText)), [legacyText])

  function setText(key: string, value: string) {
    setValues((prev) => ({ ...prev, [key]: value }))
  }

  function setStructRow(key: string, index: number, colKey: string, value: string, def: FieldDef) {
    setValues((prev) => {
      const rows = [...((prev[key] as Record<string, string>[]) ?? [])]
      rows[index] = { ...rows[index], [colKey]: value }
      // 关联维度：编号选中后自动带出登记名称，保证编号-名称一致。
      if (def.kind === 'structured' && colKey === '维度编号') {
        const option = def.columns?.find((c) => c.key === '维度编号')?.options?.find((o) => o.code === value)
        if (option) rows[index] = { ...rows[index], 维度名称: option.name }
      }
      return { ...prev, [key]: rows }
    })
  }

  function setPartitionRow(key: string, index: number, value: string) {
    setValues((prev) => {
      const rows = [...((prev[key] as string[]) ?? [])]
      rows[index] = value
      return { ...prev, [key]: rows }
    })
  }

  function addRow(key: string, def: FieldDef) {
    setValues((prev) => {
      if (def.kind === 'partition') return { ...prev, [key]: [...((prev[key] as string[]) ?? []), ''] }
      return { ...prev, [key]: [...((prev[key] as Record<string, string>[]) ?? []), structuredPlaceholderRow(def)] }
    })
  }

  function removeRow(key: string, index: number) {
    setValues((prev) => {
      const current = prev[key]
      if (!Array.isArray(current)) return prev
      return { ...prev, [key]: current.filter((_, i) => i !== index) }
    })
  }

  async function submit() {
    setError('')
    const payload: Record<string, unknown> = {}
    for (const def of fields) {
      if (legacyKeys.has(def.key)) {
        const text = legacyText[def.key]?.trim() ?? ''
        if (text) payload[def.key] = text
        continue
      }
      if (def.kind === 'text') {
        const text = String(values[def.key] ?? '').trim()
        if (text) payload[def.key] = text
        continue
      }
      const rows = values[def.key]
      if (!Array.isArray(rows) || rows.length === 0) continue
      if (def.kind === 'partition') {
        const cleaned = (rows as string[]).map((v) => v.trim()).filter(Boolean)
        if (cleaned.length) payload[def.key] = cleaned
        continue
      }
      const cleaned = (rows as Record<string, unknown>[])
        .map((row) => {
          const item: Record<string, unknown> = {}
          for (const col of def.columns ?? []) {
            const cell = row[col.key]
            if (!isBlankScalar(cell)) item[col.key] = String(cell).trim()
          }
          return item
        })
        .filter((item) => Object.keys(item).length > 0)
      if (cleaned.length) payload[def.key] = cleaned
    }

    setSubmitting(true)
    try {
      const response = await request(endpoint, {
        method: 'POST',
        body: JSON.stringify({ values: payload }),
      })
      const body = await response.json()
      if (!response.ok || body.ok === false) {
        setError(body.detail || body.message || '登记失败，服务端未接受该提交')
        return
      }
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : '登记请求未送达')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-mask" role="presentation" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h3>{title}</h3>
          <button type="button" className="link" onClick={onClose}>关闭</button>
        </header>
        <div className="modal-body">
          {fields.map((def) => (
            <label key={def.key} className="form-field">
              <span className="form-label">
                {def.label}
                {def.required && <em className="required-mark">*</em>}
              </span>
              {legacyKeys.has(def.key) ? (
                <textarea
                  rows={2}
                  value={legacyText[def.key] ?? ''}
                  placeholder="历史文本将原样保留，不做结构化转换"
                  onChange={(e) => setLegacyText((prev) => ({ ...prev, [def.key]: e.target.value }))}
                />
              ) : def.kind === 'text' ? (
                def.multiline ? (
                  <textarea rows={2} value={String(values[def.key] ?? '')} placeholder={def.placeholder} onChange={(e) => setText(def.key, e.target.value)} />
                ) : (
                  <input value={String(values[def.key] ?? '')} placeholder={def.placeholder} onChange={(e) => setText(def.key, e.target.value)} />
                )
              ) : def.kind === 'partition' ? (
                <StructRows
                  rows={(values[def.key] as string[]) ?? []}
                  columns={[{ key: '__value', label: '字段名', required: true }]}
                  onCell={(index, colKey, value) => setPartitionRow(def.key, index, value)}
                  onAdd={() => addRow(def.key, def)}
                  onRemove={(index) => removeRow(def.key, index)}
                />
              ) : (
                <StructRows
                  rows={(values[def.key] as Record<string, string>[]) ?? []}
                  columns={def.columns ?? []}
                  onCell={(index, colKey, value) => setStructRow(def.key, index, colKey, value, def)}
                  onAdd={() => addRow(def.key, def)}
                  onRemove={(index) => removeRow(def.key, index)}
                />
              )}
            </label>
          ))}
          {error && <p className="error-text">{error}</p>}
        </div>
        <footer className="modal-foot">
          <button type="button" className="btn ghost" onClick={onClose} disabled={submitting}>取消</button>
          <button type="button" className="btn primary" onClick={() => void submit()} disabled={submitting}>
            {submitting ? '提交中…' : '保存登记'}
          </button>
        </footer>
      </div>
    </div>
  )
}

interface StructRowsProps {
  rows: Record<string, string>[] | string[]
  columns: StructColumn[]
  onCell: (index: number, colKey: string, value: string) => void
  onAdd: () => void
  onRemove: (index: number) => void
}

function StructRows({ rows, columns, onCell, onAdd, onRemove }: StructRowsProps) {
  return (
    <div className="struct-editor">
      {rows.map((row, index) => (
        <div key={index} className="struct-row">
          {columns.map((col) => {
            const value = typeof row === 'string' ? row : (row[col.key] ?? '')
            return col.options ? (
              <select key={col.key} value={value} onChange={(e) => onCell(index, col.key, e.target.value)}>
                <option value="">请选择{col.label}</option>
                {col.options.map((option) => (
                  <option key={option.code} value={option.code}>
                    {option.code}（{option.name}）
                  </option>
                ))}
              </select>
            ) : (
              <input
                key={col.key}
                value={value}
                placeholder={col.placeholder ?? col.label}
                onChange={(e) => onCell(index, col.key, e.target.value)}
              />
            )
          })}
          <button type="button" className="link danger" onClick={() => onRemove(index)}>删除</button>
        </div>
      ))}
      <button type="button" className="btn ghost small" onClick={onAdd}>+ 增加一行</button>
    </div>
  )
}

/** 拉取维度选项：模型登记表单的关联维度下拉用。 */
export function useDimensionOptions(open: boolean) {
  const [options, setOptions] = useState<{ code: string; name: string }[]>([])
  useEffect(() => {
    if (!open) return
    let cancelled = false
    void (async () => {
      try {
        const response = await request('/api/dimension?page=1&size=200')
        if (!response.ok) return
        const body = await response.json()
        if (cancelled) return
        setOptions(
          (body.items ?? [])
            .map((item: Row) => ({ code: String(item['维度编号'] ?? ''), name: String(item['维度名称'] ?? '') }))
            .filter((item: { code: string }) => item.code),
        )
      } catch {
        // 拉取失败时下拉为空，用户仍可手工输入编号由服务端校验。
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open])
  return options
}
