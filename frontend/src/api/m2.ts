import { request } from './request'
import type { Room } from './resources'

export interface Application {
  id: string; applicant_id: string; applicant_identity_id: string; identity_type: string
  department_code: string; room_id: string; purpose: string; start_at: string; end_at: string
  status: string; current_level: number | null; step_deadline_at: string | null
  resource_sync_status: string; resource_hold_id: string | null; final_reason: string | null
  finalized_at: string | null; version: number; created_at: string; submitted_at: string; updated_at: string
}
export interface Step {
  id: string; application_id: string; level: number; required_role: string; scope_type: string
  scope_key: string; status: string; deadline_at: string; decided_by: string | null
  decision_reason: string | null; decided_at: string | null; version: number
}
export interface ApplicationEvent {
  id: string; event_type: string; actor_type: string; actor_id: string | null
  from_status: string | null; to_status: string; reason: string | null; version: number; created_at: string
}
export interface Detail extends Application { steps: Step[]; events: ApplicationEvent[]; room: Room; resource_attempts: number; resource_message: string | null }
export interface Task extends Step { application: Application }
export interface Scope { role: string; type: string; key: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Create { room_id: string; purpose: string; start_at: string; end_at: string }
export async function me(signal?: AbortSignal) {
  return (await request.get<{ can_apply: boolean; scopes: Scope[] }>('/m2/me', { signal })).data
}
export async function rooms(page = 1, signal?: AbortSignal) {
  return (await request.get<Page<Room>>('/m2/rooms', { params: { page, page_size: 100 }, signal })).data
}
export async function applications(params: {page: number; mine: boolean; status?: string}, signal?: AbortSignal) {
  return (await request.get<Page<Application>>('/m2/applications', { params: { ...params, page_size: 10 }, signal })).data
}
export async function tasks(page = 1, signal?: AbortSignal) {
  return (await request.get<Page<Task>>('/m2/approval-tasks', { params: { page, page_size: 10 }, signal })).data
}
export async function detail(id: string, signal?: AbortSignal) {
  return (await request.get<Detail>(`/m2/applications/${id}`, { signal })).data
}
export async function create(body: Create, key: string, signal?: AbortSignal) {
  return (await request.post<Application>('/m2/applications', body, { headers: { 'Idempotency-Key': key }, signal })).data
}
export async function review(stepId: string, passed: boolean, comment: string, version: number, key: string, signal?: AbortSignal) {
  return (await request.post<Detail>(`/m2/approval-tasks/${stepId}/${passed ? 'approve' : 'reject'}`,
    { comment, expected_version: version }, { headers: { 'Idempotency-Key': key }, signal })).data
}
export async function withdraw(id: string, version: number, key: string, signal?: AbortSignal) {
  return (await request.post<Application>(`/m2/applications/${id}/withdraw`, { expected_version: version },
    { headers: { 'Idempotency-Key': key }, signal })).data
}
