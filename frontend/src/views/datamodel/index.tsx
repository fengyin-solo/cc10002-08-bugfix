import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { ModalForm, StructuredCell, useDimensionOptions, type FieldDef, type Row } from '@/views/shared/registration'

const ENDPOINT = '/api/datamodel'
const columns = [
  "模型编号",
  "模型名称",
  "事实表名",
  "关联维度",
  "度量列定义",
  "分区字段",
  "增量键",
  "刷新周期"
]
const actions = [
  "提交审核",
  "生效发布",
  "废弃下线"
]
const statuses = [
  "草稿",
  "待审核",
  "已生效",
  "已废弃"
]

export default function DatamodelView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const dimensionOptions = useDimensionOptions(modalOpen)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const fieldDefs: FieldDef[] = useMemo(() => [
    { key: '模型编号', label: '模型编号', required: true, kind: 'text', placeholder: '如 MDL-0001，重复编号按同一条更新' },
    { key: '模型名称', label: '模型名称', required: true, kind: 'text' },
    { key: '事实表名', label: '事实表名', required: true, kind: 'text' },
    {
      key: '关联维度', label: '关联维度', kind: 'structured',
      columns: [
        { key: '维度编号', label: '维度编号', required: true, options: dimensionOptions },
        { key: '维度名称', label: '维度名称', placeholder: '选择编号后自动带出' },
      ],
    },
    {
      key: '度量列定义', label: '度量列定义', kind: 'structured',
      columns: [
        { key: '列名', label: '列名', required: true },
        { key: '聚合方式', label: '聚合方式', required: true, placeholder: 'sum/count/avg' },
        { key: '数据类型', label: '数据类型' },
        { key: '备注', label: '备注' },
      ],
    },
    { key: '分区字段', label: '分区字段', kind: 'partition' },
    { key: '增量键', label: '增量键', kind: 'text' },
    { key: '刷新周期', label: '刷新周期', kind: 'text', placeholder: '如 每日 / 每小时' },
  ], [dimensionOptions])

  const stats = useMemo(() => [
    { label: '模型定义总数', value: total },
    { label: '已生效模型', value: rows.filter((r) => r.status === '已生效').length },
    { label: '待审核模型', value: rows.filter((r) => r.status === '待审核').length },
  ], [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const params = new URLSearchParams()
    if (filters['模型编号']) params.set('keyword', filters['模型编号'])
    if (filters.status) params.set('status', filters.status)
    params.set('page', '1')
    params.set('size', '200')
    try {
      const response = await request(`${ENDPOINT}?${params.toString()}`)
      if (!response.ok) {
        throw new Error('模型定义列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据建模列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${String(row.id)}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const body = await response.json().catch(() => null)
      if (!response.ok || body?.ok === false) {
        throw new Error(body?.message || '数据建模动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据建模操作失败')
    }
  }

  async function openEdit(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${String(row.id)}`)
      if (!response.ok) throw new Error('模型定义详情读取失败')
      setEditing(await response.json())
      setModalOpen(true)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据建模详情读取失败')
    }
  }

  function refStatus(row: Row): { text: string; className: string } | null {
    const refs = row['关联维度引用']
    if (!Array.isArray(refs) || refs.length === 0) return null
    const statuses = refs.map((ref) => (ref as Row)['引用状态'] as string)
    if (statuses.includes('无效')) return { text: '存在无效引用', className: 'ref-bad' }
    if (statuses.includes('已停用')) return { text: '含已停用维度', className: 'ref-warn' }
    if (statuses.includes('历史文本')) return { text: '历史文本待整理', className: 'ref-warn' }
    return { text: '引用有效', className: 'ref-ok' }
  }

  return (
    <section className="page" data-module="datamodel">
      <header className="page-head">
        <div>
          <h2>数据建模管理</h2>
          <p className="page-desc">维护数据模型定义的输入、执行约束与结果记录，为数据建模提供可验证的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => { setEditing(null); setModalOpen(true) }}>
            登记模型定义
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据建模清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <label className="filter-item">
          <span>状态</span>
          <select value={filters.status ?? ''} onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}>
            <option value="">全部</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>引用状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const ref = refStatus(row)
            return (
              <tr key={String(row.id)}>
                {columns.map((column) => (
                  <td key={column}>
                    {column === '模型编号' ? (
                      <button type="button" className="link" onClick={() => void openEdit(row)}>{String(row[column] ?? '')}</button>
                    ) : (
                      <StructuredCell value={row[column]} />
                    )}
                  </td>
                ))}
                <td>
                  {ref ? (
                    <span className={`ref-tag ${ref.className}`} title={referenceHint(row)}>{ref.text}</span>
                  ) : '—'}
                </td>
                <td className="row-actions">
                  {actions.map((action) => (
                    <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无数据建模数据，可先登记模型定义</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条数据建模记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {modalOpen && (
        <ModalForm
          title={editing ? `编辑模型定义 ${String(editing['模型编号'] ?? '')}` : '登记模型定义'}
          fields={fieldDefs}
          initial={editing}
          endpoint={ENDPOINT}
          onClose={() => setModalOpen(false)}
          onSaved={() => void reload()}
        />
      )}
    </section>
  )
}

function referenceHint(row: Row): string {
  const refs = row['关联维度引用']
  if (!Array.isArray(refs)) return ''
  return refs
    .map((ref) => {
      const item = ref as Row
      return item['错误原因'] ? `${String(item['维度编号'])}：${String(item['错误原因'])}` : ''
    })
    .filter(Boolean)
    .join('；')
}
