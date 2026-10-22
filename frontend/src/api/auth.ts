import { request } from './request'

export interface AuthUser {
  id: string
  identity_id: string
  username: string
  display_name: string
  identity_type: string
  department_code: string
  roles: string[]
  permissions: string[]
}
export interface Identity { id: string; identity_type: string; department_code: string }
export async function login(username: string, password: string) {
  await request.post('/auth/login', { username, password })
}
export async function me() { return (await request.get<AuthUser>('/auth/me')).data }
export async function logout() { await request.post('/auth/logout') }
export async function identities() { return (await request.get<Identity[]>('/auth/identities')).data }
export async function selectIdentity(identity_id: string) {
  return (await request.post<AuthUser>('/auth/identity', { identity_id })).data
}
