import { request } from './request'
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Seat { seat_id: string; room_id: string; seat_no: string; room_name: string; building_name: string; has_fixed_socket: boolean; has_mobile_socket: boolean; resource_version: string }
export interface Reservation { id: string; identity_id: string; seat_id: string; room_id: string; start_at: string; end_at: string; status: string; version: string; resource_snapshot: { seat_no: string; room_name: string; building_name: string } }
export interface Violation { id: string; reservation_id: string; occurred_at: string; reason: string }
export interface BookingInput { identity_id: string; seat_id: string; start_at: string; end_at: string }
export interface M1Statistics {
  reservation_count: number; checked_in_count: number; cancelled_count: number
  no_show_count: number; completed_count: number; interrupted_count: number
  planned_minutes: number; actual_minutes: number; fulfillment_rate: number | null
}
export interface M1Settings { max_hours: number; version: string }
export interface CheckinCode { code: string; expires_at: string }
export async function search(params: Record<string, string | number | boolean>) { return (await request.get<Page<Seat>>('/m1/seats', { params })).data }
export async function create(body: BookingInput, key: string) { return (await request.post<Reservation>('/m1/reservations', body, { headers: { 'Idempotency-Key': key } })).data }
export async function reservations(page = 1) { return (await request.get<Page<Reservation>>('/m1/reservations', { params: { page, page_size: 20 } })).data }
export async function detail(id: string) { return (await request.get<Reservation>(`/m1/reservations/${encodeURIComponent(id)}`)).data }
export async function cancel(id: string) { return (await request.post<Reservation>(`/m1/reservations/${encodeURIComponent(id)}/cancel`)).data }
export async function checkIn(id: string, code: string) { return (await request.post<Reservation>(`/m1/reservations/${encodeURIComponent(id)}/check-in`, { code })).data }
export async function violations(page = 1) { return (await request.get<Page<Violation>>('/m1/violations', { params: { page, page_size: 20 } })).data }
export async function statistics(start_at: string, end_at: string, room_id?: string) {
  return (await request.get<M1Statistics>('/m1/admin/statistics', { params: { start_at, end_at, ...(room_id ? { room_id } : {}) } })).data
}
export async function adminReservations(page = 1, room_id?: string) {
  return (await request.get<Page<Reservation>>('/m1/admin/reservations', { params: { page, page_size: 20, ...(room_id ? { room_id } : {}) } })).data
}
export async function settings() { return (await request.get<M1Settings>('/m1/admin/settings')).data }
export async function updateSettings(max_hours: number, expected_version: string) {
  return (await request.patch<M1Settings>('/m1/admin/settings', { max_hours, expected_version })).data
}
export async function checkinCode(room_id: string, rotate = false) {
  const path = `/m1/admin/rooms/${encodeURIComponent(room_id)}/check-in-code${rotate ? '/rotate' : ''}`
  return (await (rotate ? request.post<CheckinCode>(path) : request.get<CheckinCode>(path))).data
}
