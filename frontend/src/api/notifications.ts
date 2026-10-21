import { request } from './request'

export interface NotificationView {
  id: string
  recipient_id: string
  source_module: string
  business_id: string
  event_key: string
  title: string
  content: string
  link: string
  created_at: string
  read_at: string | null
}

export interface NotificationPage {
  items: NotificationView[]
  total: number
  page: number
  page_size: number
  unread_count: number
}

export interface NotificationQuery {
  page?: number
  page_size?: number
  unread_only?: boolean
}

export async function listNotifications(query: NotificationQuery, signal?: AbortSignal) {
  return (await request.get<NotificationPage>('/notifications', { params: query, signal })).data
}

export async function getUnreadCount(signal?: AbortSignal) {
  return (await request.get<{ unread_count: number }>('/notifications/unread-count', { signal })).data
}

export async function markNotificationRead(id: string, signal?: AbortSignal) {
  return (await request.post<NotificationView>(`/notifications/${encodeURIComponent(id)}/read`, undefined, { signal })).data
}

export async function markAllNotificationsRead(signal?: AbortSignal) {
  return (await request.post<{ updated: number; unread_count: number }>('/notifications/read-all', undefined, { signal })).data
}
