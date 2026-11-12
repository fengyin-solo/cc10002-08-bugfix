import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { ModalForm, StructuredCell, type FieldDef, type Row } from '@/views/shared/registration'

const ENDPOINT = '/api/dimension'
const columns = [
  "维度编号",
  "维度名称",
  "数据来源表",
  "维度键列",
  "层级关系",
  "历史拉链策略",
  "刷新周期",
  "默认取值",
  "日期样例"
]
const actions = [
  "提交审核",
  "生效启用",
  "废弃下线"
]
const statuses = [
  "草稿",
  "待审核",
  "已生效",
  "已废弃"
]

export default function DimensionView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const fieldDefs: FieldDef[] = useMemo(() => [
    { key: '维度编号', label: '维度编号', required: true, kind: 'text', placeholder: '如 DIME-0001，重复编号按同一条更新' },
    { key: '维度名称', label: '维度名称', required: true, kind: 'text' },
    { key: '数据来源表', label: '数据来源表', required: true, kind: 'text' },
    { key: '维度键列', label: '维度键列', kind: 'text' },
    {
      key: '层级关系', label: '层级关系', kind: 'structured',
      columns: [
        { key: '层级名称', label: '层级名称', required: true },
        { key: '上级层级', label: '上级层级' },
        { key: '层级取值列', label: '层级取值列' },
        { key: '备注', label: '备注' },
      ],
    },
    { key: '历史拉链策略', label: '历史拉链策略', kind: 'text' },
    { key: '刷新周期', label: '刷新周期', kind: 'text' },
    { key: '默认取值', label: '默认取值', kind: 'text' },
    {
      key: '日期样例', label: '日期样例', kind: 'structured',
      columns: [
        { key: '字段名', label: '字段名', required: true },
        { key: '样例值', label: '样例值', required: true },
        { key: '日期格式', label: '日期格式', placeholder: 'yyyy-MM-dd' },
      ],
    },
  ], [])

  const stats = useMemo(() => [
    { label: '分析维度总数', value: total },
    { label: '已生效维度', value: rows.filter((r) => r.status === '已生效').length },
    { label: '待审核维度', value: rows.filter((r) => r.status === '待审核').length },
  ], [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const params = new URLSearchParams()
    if (filters['维度编号']) params.set('keyword', filters['维度编号'])
    if (filters.status) params.set('status', filters.status)
    params.set('page', '1')
    params.set('size', '200')
    try {
      const response = await request(`${ENDPOINT}?${params.toString()}`)
      if (!response.ok) {
        throw new Error('分析维度列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '维度管理列表读取失败')
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
        throw new Error(body?.message || '维度管理动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '维度管理操作失败')
    }
  }

  async function openEdit(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${String(row.id)}`)
      if (!response.ok) throw new Error('分析维度详情读取失败')
      setEditing(await response.json())
      setModalOpen(true)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '维度管理详情读取失败')
    }
  }

  function referencedModels(row: Row): Row[] {
    const refs = row['关联模型']
    return Array.isArray(refs) ? (refs as Row[]) : []
  }

  return (
    <section className="page" data-module="dimension">
      <header className="page-head">
        <div>
          <h2>维度管理</h2>
          <p className="page-desc">维护分析维度的输入、执行约束与结果记录；维度停用后模型侧引用与错误原因会继续保留。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => { setEditing(null); setModalOpen(true) }}>
            登记分析维度
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出维度管理清单
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
            <th>关联模型</th>
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const models = referencedModels(row)
            return (
              <tr key={String(row.id)} className={row.status === '已废弃' ? 'row-disabled' : undefined}>
                {columns.map((column) => (
                  <td key={column}>
                    {column === '维度编号' ? (
                      <button type="button" className="link" onClick={() => void openEdit(row)}>{String(row[column] ?? '')}</button>
                    ) : (
                      <StructuredCell value={row[column]} />
                    )}
                  </td>
                ))}
                <td>
                  {models.length === 0 ? (
                    <span className="cell-empty">—</span>
                  ) : (
                    <div className="chip-stack">
                      {models.map((model) => (
                        <span key={String(model['模型id'])} className="chip" title={String(model['模型名称'] ?? '')}>
                          {String(model['模型编号'] ?? '')}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td>
                  <span className={`status-tag status-${String(row.status ?? '')}`}>{String(row.status ?? '—')}</span>
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
              <td className="empty-state" colSpan={columns.length + 3}>暂无维度管理数据，可先登记分析维度</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条维度管理记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {modalOpen && (
        <ModalForm
          title={editing ? `编辑分析维度 ${String(editing['维度编号'] ?? '')}` : '登记分析维度'}
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
