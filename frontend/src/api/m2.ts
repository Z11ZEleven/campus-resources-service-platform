import { request } from './request'

export type ApplicationStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED'
export interface Application {
  id: string
  applicant_id: string
  room_id: string
  title: string
  purpose: string
  participant_count: number
  start_at: string
  end_at: string
  status: ApplicationStatus
  display_status: ApplicationStatus | 'ENDED'
  reviewer_id: string | null
  reviewed_at: string | null
  review_comment: string | null
  cancelled_at: string | null
  request_key: string
  created_at: string
  updated_at: string
}
export interface ApplicationEvent {
  id: string; action: string; actor_id: string; from_status: string | null
  to_status: string | null; note: string | null; created_at: string
}
export interface ApplicationDetail extends Application { events: ApplicationEvent[] }
export interface CreateApplication {
  room_id: string; title: string; purpose: string; participant_count: number
  start_at: string; end_at: string; request_key: string
}
export async function applications(params: {
  page: number; page_size: number; status?: ApplicationStatus; mine: boolean
}) {
  return (await request.get<{ items: Application[]; total: number }>('/m2/applications', { params })).data
}
export async function application(id: string) {
  return (await request.get<ApplicationDetail>(`/m2/applications/${id}`)).data
}
export async function createApplication(body: CreateApplication) {
  return (await request.post<Application>('/m2/applications', body)).data
}
export async function reviewApplication(id: string, passed: boolean, comment: string) {
  return (await request.post<Application>(`/m2/applications/${id}/review`, { passed, comment })).data
}
export async function cancelApplication(id: string) {
  return (await request.post<Application>(`/m2/applications/${id}/cancel`)).data
}
