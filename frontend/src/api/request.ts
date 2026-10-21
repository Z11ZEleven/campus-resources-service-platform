import axios from 'axios'

let token = ''
export function setAccessToken(value: string) { token = value }

export const request = axios.create({ baseURL: '/api/v1', timeout: 10000 })
request.interceptors.request.use(config => {
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
request.interceptors.response.use(response => response, error => {
  if (error.response?.status === 401 && token) {
    token = ''
    window.dispatchEvent(new Event('auth-expired'))
  }
  return Promise.reject(error)
})

export function errorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return '操作失败，请重试'
  const detail = error.response?.data?.detail
  if (typeof detail?.message === 'string') return detail.message
  if (Array.isArray(detail)) return detail.map(item => item.msg).join('；')
  return error.response ? '请求失败，请检查输入或权限' : '无法连接后端，请确认服务已启动'
}
