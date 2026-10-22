import axios from 'axios'

export const request = axios.create({ baseURL: '/api/v1', timeout: 10000, withCredentials: true })
request.interceptors.request.use(config => {
  if (!['get', 'head', 'options'].includes((config.method ?? 'get').toLowerCase())) {
    const csrf = document.cookie.split('; ').find(value => value.startsWith('campus_csrf='))?.split('=')[1]
    if (csrf) config.headers['X-CSRF-Token'] = decodeURIComponent(csrf)
  }
  return config
})
request.interceptors.response.use(response => response, error => {
  if (error.response?.status === 401) {
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
