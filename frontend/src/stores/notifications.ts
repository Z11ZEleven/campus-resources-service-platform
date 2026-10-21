import axios from 'axios'
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { getUnreadCount } from '../api/notifications'
import { useAuthStore } from './auth'

const POLL_INTERVAL = 30_000

export interface CountSnapshot {
  userId: string
  session: number
  version: number
}

export const useNotificationsStore = defineStore('notifications', () => {
  const auth = useAuthStore()
  const unreadCount = ref(0)
  const countLoaded = ref(false)
  const countError = ref(false)
  let running = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  let pending: Promise<void> | undefined
  let sequence = 0
  let session = 0
  let issuedVersion = 0
  let acceptedVersion = 0

  function clearTimer() { if (timer !== undefined) clearTimeout(timer); timer = undefined }

  function cancelRequest() {
    sequence++
    controller?.abort()
    controller = undefined
    pending = undefined
  }

  function schedule() {
    clearTimer()
    if (running && auth.user) timer = setTimeout(() => { void refreshUnread() }, POLL_INTERVAL)
  }

  // Capture at request start, so response arrival order cannot roll counts back.
  function captureCountSnapshot(): CountSnapshot | null {
    return auth.user ? { userId: auth.user.id, session, version: ++issuedVersion } : null
  }

  function acceptCount(value: number, snapshot: CountSnapshot | null): boolean {
    if (!snapshot || auth.user?.id !== snapshot.userId || snapshot.session !== session || snapshot.version < acceptedVersion) return false
    acceptedVersion = snapshot.version
    unreadCount.value = value
    countLoaded.value = true
    countError.value = false
    if (!pending) schedule()
    return true
  }

  function refreshUnread(supersede = false): Promise<void> {
    if (!auth.user) return Promise.resolve()
    if (pending && !supersede) return pending
    clearTimer()
    cancelRequest()
    const requestSequence = sequence
    const userId = auth.user.id
    const snapshot = captureCountSnapshot()!
    const requestController = new AbortController()
    controller = requestController
    const current = () => sequence === requestSequence && auth.user?.id === userId && !requestController.signal.aborted
    pending = (async () => {
      try {
        const result = await getUnreadCount(requestController.signal)
        if (current()) {
          acceptCount(result.unread_count, snapshot)
        }
      } catch (error) {
        if (current() && snapshot.version >= acceptedVersion && !axios.isCancel(error)) countError.value = true
      } finally {
        if (current()) { pending = undefined; controller = undefined; schedule() }
      }
    })()
    return pending
  }

  function startPolling() {
    if (running) return
    running = true
    if (auth.user) void refreshUnread()
  }

  function stopPolling() { running = false; clearTimer(); cancelRequest() }

  watch(() => auth.user?.id ?? '', () => {
    session++
    issuedVersion = 0
    acceptedVersion = 0
    clearTimer()
    cancelRequest()
    unreadCount.value = 0
    countLoaded.value = false
    countError.value = false
    if (running && auth.user) void refreshUnread()
  }, { flush: 'sync' })

  return { unreadCount, countLoaded, countError, captureCountSnapshot, refreshUnread, acceptCount, startPolling, stopPolling }
})
