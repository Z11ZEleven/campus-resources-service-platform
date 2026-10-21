import type { AuthUser } from './auth'
import { request } from './request'

export type WorkOrderStatus = 'REPORTED' | 'ASSIGNED' | 'REPAIRING' | 'WAIT_ACCEPTANCE' | 'COMPLETED'
export type WorkOrderCategory = 'AIR_CONDITIONING' | 'LIGHTING' | 'POWER_SOCKET' | 'FURNITURE' | 'OTHER'

export interface WorkOrder {
  id: string
  created_at: string
  reporter_id: string
  room_id: string
  title: string
  description: string
  category: WorkOrderCategory
  status: WorkOrderStatus
  assignee_id: string | null
  assigned_by: string | null
  assigned_at: string | null
  started_at: string | null
  completion_note: string | null
  submitted_at: string | null
  completion_round: number
  accepted_by: string | null
  accepted_at: string | null
  acceptance_comment: string | null
  request_key: string
  updated_at: string
  stage_started_at: string
  escalation_count: number
}

export interface OperationLog {
  id: string
  module: string
  business_id: string
  action: string
  actor_id: string
  from_status: string | null
  to_status: string | null
  note: string | null
  created_at: string
}

export interface WorkOrderDetail extends WorkOrder {
  history: OperationLog[]
  rating: RatingView | null
  escalations: EscalationView[]
  timeout: TimeoutInfo
}

export interface RatingView {
  id: string
  work_order_id: string
  reporter_id: string
  score: number
  comment: string | null
  created_at: string
}

export interface EscalationView {
  id: string
  work_order_id: string
  stage: WorkOrderStatus
  completion_round: number
  stage_started_at: string
  due_at: string
  escalated_at: string
  reason: string
  triggered_by: string | null
}

export interface TimeoutInfo {
  due_at: string | null
  is_overdue: boolean
  current_stage_escalated: boolean
}

export interface TimeoutPolicy {
  reported_hours: number
  assigned_hours: number
  repairing_hours: number
  acceptance_hours: number
  scan_interval_seconds: number
  automatic_scan_enabled: boolean
}

export interface RatingBody { score: number; comment: string | null }

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface WorkOrderQuery {
  page?: number
  page_size?: number
  status?: WorkOrderStatus
  category?: WorkOrderCategory
  room_id?: string
}

export interface CreateWorkOrderBody {
  room_id: string
  title: string
  description: string
  category: WorkOrderCategory
  request_key: string
}

export interface AssignWorkOrderBody { assignee_id: string }
export interface SubmitCompletionBody { completion_note: string }
export interface AcceptanceBody {
  passed: boolean
  comment: string | null
  expected_completion_round: number
}

const workOrdersPath = '/m3/work-orders'

export async function listWorkOrders(query: WorkOrderQuery = {}, signal?: AbortSignal) {
  return (await request.get<Page<WorkOrder>>(workOrdersPath, { params: query, signal })).data
}

export async function getWorkOrder(id: string, signal?: AbortSignal) {
  return (await request.get<WorkOrderDetail>(`${workOrdersPath}/${encodeURIComponent(id)}`, { signal })).data
}

export async function createWorkOrder(body: CreateWorkOrderBody) {
  return (await request.post<WorkOrder>(workOrdersPath, body)).data
}

export async function assignWorkOrder(id: string, body: AssignWorkOrderBody) {
  return (await request.post<WorkOrder>(`${workOrdersPath}/${encodeURIComponent(id)}/assign`, body)).data
}

export async function startWorkOrder(id: string) {
  return (await request.post<WorkOrder>(`${workOrdersPath}/${encodeURIComponent(id)}/start`)).data
}

export async function submitCompletion(id: string, body: SubmitCompletionBody) {
  return (await request.post<WorkOrder>(`${workOrdersPath}/${encodeURIComponent(id)}/submit-completion`, body)).data
}

export async function acceptWorkOrder(id: string, body: AcceptanceBody) {
  return (await request.post<WorkOrder>(`${workOrdersPath}/${encodeURIComponent(id)}/acceptance`, body)).data
}

export async function rateWorkOrder(id: string, body: RatingBody, signal?: AbortSignal) {
  return (await request.post<RatingView>(`${workOrdersPath}/${encodeURIComponent(id)}/rating`, body, { signal })).data
}

export async function getTimeoutPolicy(signal?: AbortSignal) {
  return (await request.get<TimeoutPolicy>(`${workOrdersPath}/timeout-policy`, { signal })).data
}

export async function scanTimeouts(signal?: AbortSignal) {
  return (await request.post<{ checked: number; escalated: number }>(`${workOrdersPath}/scan-timeouts`, undefined, { signal })).data
}

export async function listRepairers(page = 1, pageSize = 100, signal?: AbortSignal) {
  return (await request.get<Page<AuthUser>>('/auth/users', {
    params: { role_code: 'REPAIRER', page, page_size: pageSize },
    signal,
  })).data
}
