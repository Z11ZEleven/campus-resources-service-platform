import { request } from './request'

export interface AuthUser {
  id: string
  username: string
  display_name: string
  identity_type: string
  department_code: string
  roles: string[]
}
export async function login(username: string, password: string) {
  return (await request.post<{ access_token: string }>('/auth/login', { username, password })).data
}
export async function me() { return (await request.get<AuthUser>('/auth/me')).data }
