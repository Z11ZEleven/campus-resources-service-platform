import type { AuthUser } from '../api/auth'
import type { WorkOrder, WorkOrderCategory, WorkOrderDetail, WorkOrderStatus } from '../api/m3'

export const statusLabels: Record<WorkOrderStatus, string> = {
  REPORTED: '待派单',
  ASSIGNED: '已派单',
  REPAIRING: '维修中',
  WAIT_ACCEPTANCE: '待验收',
  COMPLETED: '已完成',
}

export const categoryLabels: Record<WorkOrderCategory, string> = {
  AIR_CONDITIONING: '空调',
  LIGHTING: '照明',
  POWER_SOCKET: '电源插座',
  FURNITURE: '家具',
  OTHER: '其他',
}

export const statusOptions = (Object.keys(statusLabels) as WorkOrderStatus[])
  .map(value => ({ value, label: statusLabels[value] }))
export const categoryOptions = (Object.keys(categoryLabels) as WorkOrderCategory[])
  .map(value => ({ value, label: categoryLabels[value] }))

export function statusTagType(status: WorkOrderStatus): 'info' | 'primary' | 'warning' | 'success' {
  const types = {
    REPORTED: 'info',
    ASSIGNED: 'primary',
    REPAIRING: 'warning',
    WAIT_ACCEPTANCE: 'warning',
    COMPLETED: 'success',
  } as const
  return types[status] ?? 'info'
}

const schoolTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间格式无效'
  return schoolTime.format(date).replaceAll('/', '-')
}

export function canCreate(user: AuthUser | null | undefined): boolean {
  return Boolean(user?.roles.includes('USER'))
}

export function canRate(user: AuthUser | null | undefined, order: WorkOrderDetail): boolean {
  return Boolean(user && order.status === 'COMPLETED' && order.reporter_id === user.id && !order.rating)
}

export function orderPermissions(user: AuthUser | null | undefined, order: WorkOrder) {
  const propertyAdmin = Boolean(user?.roles.includes('PROPERTY_ADMIN'))
  const assignedRepairer = Boolean(user?.roles.includes('REPAIRER') && order.assignee_id === user.id)
  return {
    assign: propertyAdmin && order.status === 'REPORTED',
    start: assignedRepairer && order.status === 'ASSIGNED',
    complete: assignedRepairer && order.status === 'REPAIRING',
    accept: propertyAdmin && order.status === 'WAIT_ACCEPTANCE' && order.assignee_id !== user?.id,
  }
}

export function scopeDescription(user: AuthUser | null | undefined): string {
  if (!user) return '登录后可查看有权访问的工单。'
  if (user.roles.includes('PROPERTY_ADMIN')) return '显示你可管理的全部工单，可派单和验收。'
  if (user.roles.includes('REPAIRER')) return '显示本人上报及分配给本人的工单；仅可处理分配给本人的维修任务。'
  return '显示本人上报的工单。'
}

const historyLabels: Record<string, string> = {
  CREATE_WORK_ORDER: '上报故障',
  ASSIGN_WORK_ORDER: '派单',
  START_REPAIR: '开始维修',
  SUBMIT_COMPLETION: '提交完工',
  ACCEPT_WORK_ORDER: '验收通过',
  REJECT_WORK_ORDER: '验收驳回',
  ACQUIRE_RESOURCE: '添加本工单维修限制',
  RELEASE_RESOURCE: '释放本工单维修限制',
  RATE_WORK_ORDER: '报修人评价',
  ESCALATE_WORK_ORDER: '超时升级',
}

export function historyLabel(action: string): string {
  return Object.hasOwn(historyLabels, action) ? historyLabels[action] : action
}

export function historyNote(note: string | null | undefined): string {
  if (!note) return ''
  let parsed: unknown
  try {
    parsed = JSON.parse(note)
  } catch {
    return note
  }
  if (typeof parsed === 'string') return parsed
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return note
  const data = parsed as Record<string, unknown>
  const details: string[] = []
  if (typeof data.assignee_id === 'string') details.push(`维修人员 #${data.assignee_id}`)
  if (typeof data.completion_round === 'number') details.push(`第 ${data.completion_round} 轮完工`)
  if (typeof data.passed === 'boolean') details.push(data.passed ? '验收通过' : '验收驳回')
  if (typeof data.completion_note === 'string') details.push(`完工说明：${data.completion_note}`)
  if (typeof data.comment === 'string' && data.comment) details.push(`${typeof data.score === 'number' ? '评价内容' : '验收意见'}：${data.comment}`)
  if (typeof data.score === 'number') details.push(`评价：${data.score} 星`)
  if (typeof data.reason === 'string' && data.reason) details.push(data.reason)
  return details.length ? details.join('\n') : note
}
