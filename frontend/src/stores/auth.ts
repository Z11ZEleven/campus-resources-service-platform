import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<api.AuthUser | null>(null)
  const identities = ref<api.Identity[]>([])
  let restoring: Promise<void> | null = null
  let restored = false
  async function load() {
    user.value = await api.me()
    identities.value = await api.identities()
  }
  async function login(username: string, password: string) {
    await api.login(username, password)
    try { await load(); restored = true }
    catch (error) { clear(); throw error }
  }
  async function restore() {
    if (restored) return
    if (!restoring) restoring = load().catch(() => { clear() }).finally(() => {
      restored = true; restoring = null
    })
    await restoring
  }
  async function logout() { await api.logout(); clear() }
  async function selectIdentity(id: string) { user.value = await api.selectIdentity(id) }
  function clear() { user.value = null; identities.value = [] }
  return { user, identities, login, logout, restore, selectIdentity, clear }
})
