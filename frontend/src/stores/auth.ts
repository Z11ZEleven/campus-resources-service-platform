import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/auth'
import { setAccessToken } from '../api/request'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<api.AuthUser | null>(null)
  async function login(username: string, password: string) {
    const token = await api.login(username, password)
    setAccessToken(token.access_token)
    try { user.value = await api.me() }
    catch (error) { clear(); throw error }
  }
  function clear() { user.value = null; setAccessToken('') }
  return { user, login, clear }
})
