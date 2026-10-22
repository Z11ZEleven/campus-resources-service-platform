import { request } from './request'
import type { Room, Seat } from './resources'
export interface Building { id: string; code: string; name: string; is_active: boolean }
export interface ManagedRoom extends Room { building_id: string | null; resource_version: number; opening_rules_enabled: boolean }
export interface RoomInput { code: string; name: string; building_id: string; capacity: number; department_code: string | null; open_time: string; close_time: string; is_active: boolean }
export interface SeatInput { seat_no: string; has_fixed_socket: boolean; has_mobile_socket: boolean; is_active: boolean }
export interface Schedule { weekly: { weekday: number; start_time: string; end_time: string }[]; exceptions: { kind: 'OPEN' | 'CLOSE'; start_at: string; end_at: string }[] }
export const getBuildings = async () => (await request.get<{ items: Building[] }>('/resources/buildings')).data.items
export const saveBuilding = async (data: Omit<Building, 'id'>, id?: string) => (await (id ? request.put(`/resources/buildings/${id}`, data) : request.post('/resources/buildings', data))).data
export const saveRoom = async (data: RoomInput, id?: string) => (await (id ? request.put(`/resources/rooms/${id}`, data) : request.post('/resources/rooms', data))).data
export const getSeats = async (room: string, page = 1) => (await request.get<{ items: Seat[]; total: number }>(`/resources/rooms/${room}/seats`, { params: { page, page_size: 100 } })).data
export const saveSeat = async (room: string, data: SeatInput, id?: string) => (await (id ? request.put(`/resources/rooms/${room}/seats/${id}`, data) : request.post(`/resources/rooms/${room}/seats`, data))).data
export const getSchedule = async (room: string) => (await request.get<Schedule>(`/resources/rooms/${room}/opening`)).data
export const saveSchedule = async (room: string, data: Schedule) => (await request.put(`/resources/rooms/${room}/opening`, data)).data
