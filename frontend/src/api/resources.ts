import { request } from './request'

export interface Room {
  id: string
  code: string
  name: string
  building_name: string
  capacity: number
  department_code: string | null
  booking_mode?: 'SEAT' | 'WHOLE_ROOM'
  campus_code?: string
  open_time: string
  close_time: string
  is_active: boolean
}
export interface Seat {
  id: string
  room_id: string
  seat_no: string
  has_fixed_socket: boolean
  has_mobile_socket: boolean
  is_active: boolean
}
export interface Slot {
  seat_id: string
  slot_start: string
  slot_end: string
  status: string
  primary_block_id: string | null
  source_module: string | null
  source_count: number
  resource_version: number
  bookable: boolean
}
export interface Availability { room_id: string; date: string; seats: Seat[]; slots: Slot[] }
export async function rooms(page = 1, pageSize = 100, signal?: AbortSignal) {
  return (await request.get<{ items: Room[]; total: number }>('/resources/rooms', {
    params: { page, page_size: pageSize },
    signal,
  })).data
}
export async function availability(roomId: string, date: string) {
  return (await request.get<Availability>(`/resources/rooms/${roomId}/availability`, {
    params: { date },
  })).data
}
