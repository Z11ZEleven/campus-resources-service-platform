<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { isNavigationFailure, useRouter } from 'vue-router'
import { listNotifications, markAllNotificationsRead, markNotificationRead, type NotificationView } from '../api/notifications'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'
import { useNotificationsStore } from '../stores/notifications'
import { notificationDestination } from '../utils/navigation'

const auth = useAuthStore()
const notifications = useNotificationsStore()
const router = useRouter()
const items = ref<NotificationView[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const unreadOnly = ref(false)
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const actionNotice = ref('')
const hasNewMessages = ref(false)
const workingId = ref('')
const markingAll = ref(false)
const busy = computed(() => Boolean(workingId.value) || markingAll.value)
let alive = true
let epoch = 0
let listSequence = 0
let listController: AbortController | undefined
let writeController: AbortController | undefined
const timeFormat = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })

function formatTime(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '时间不可用' : timeFormat.format(parsed)
}

function activeFor(userId: string, requestEpoch: number) {
  return alive && epoch === requestEpoch && auth.user?.id === userId
}

async function refresh() {
  if (!auth.user || busy.value) return
  const userId = auth.user.id
  const requestEpoch = epoch
  const countSnapshot = notifications.captureCountSnapshot()
  const requestSequence = ++listSequence
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  loading.value = true
  loadError.value = ''
  try {
    const result = await listNotifications({ page: page.value, page_size: pageSize.value, unread_only: unreadOnly.value }, controller.signal)
    if (!activeFor(userId, requestEpoch) || requestSequence !== listSequence) return
    items.value = result.items
    total.value = result.total
    const countAccepted = notifications.acceptCount(result.unread_count, countSnapshot)
    hasNewMessages.value = !countAccepted && notifications.unreadCount > result.unread_count
    if (!items.value.length && total.value > 0 && page.value > 1) {
      page.value = Math.max(1, Math.ceil(total.value / pageSize.value))
      await refresh()
    }
  } catch (error) {
    if (activeFor(userId, requestEpoch) && requestSequence === listSequence && !axios.isCancel(error)) loadError.value = errorMessage(error)
  } finally {
    if (activeFor(userId, requestEpoch) && requestSequence === listSequence) loading.value = false
  }
}

function filterChanged() {
  page.value = 1
  items.value = []
  total.value = 0
  void refresh()
}

async function readOne(item: NotificationView, reload = true): Promise<boolean> {
  if (!auth.user || busy.value || loading.value) return false
  if (item.read_at) return true
  const userId = auth.user.id
  const requestEpoch = epoch
  const controller = new AbortController()
  writeController = controller
  workingId.value = item.id
  actionError.value = ''
  actionNotice.value = ''
  let succeeded = false
  try {
    const result = await markNotificationRead(item.id, controller.signal)
    if (!activeFor(userId, requestEpoch)) return false
    items.value = items.value.map(current => current.id === result.id ? result : current)
    await notifications.refreshUnread(true)
    if (!activeFor(userId, requestEpoch)) return false
    succeeded = true
  } catch (error) {
    if (activeFor(userId, requestEpoch) && !axios.isCancel(error)) actionError.value = `${errorMessage(error)}。可以重试标记已读。`
  } finally {
    if (activeFor(userId, requestEpoch)) { workingId.value = ''; writeController = undefined }
  }
  if (succeeded && reload && activeFor(userId, requestEpoch)) {
    actionNotice.value = '已标记为已读。'
    await refresh()
  }
  return succeeded && activeFor(userId, requestEpoch)
}

async function readAll() {
  if (!auth.user || busy.value || loading.value) return
  const userId = auth.user.id
  const requestEpoch = epoch
  const controller = new AbortController()
  writeController = controller
  markingAll.value = true
  const countSnapshot = notifications.captureCountSnapshot()
  actionError.value = ''
  actionNotice.value = ''
  let succeeded = false
  try {
    const result = await markAllNotificationsRead(controller.signal)
    if (!activeFor(userId, requestEpoch)) return
    notifications.acceptCount(result.unread_count, countSnapshot)
    actionNotice.value = result.updated ? `已将 ${result.updated} 条通知标记为已读。` : '当前没有需要标记的未读通知。'
    succeeded = true
  } catch (error) {
    if (activeFor(userId, requestEpoch) && !axios.isCancel(error)) actionError.value = `${errorMessage(error)}。可以重试全部已读。`
  } finally {
    if (activeFor(userId, requestEpoch)) { markingAll.value = false; writeController = undefined }
  }
  if (succeeded && activeFor(userId, requestEpoch)) await refresh()
}

async function openNotification(item: NotificationView) {
  if (!auth.user || busy.value || loading.value) return
  const userId = auth.user.id
  const requestEpoch = epoch
  const destination = notificationDestination(item.link)
  actionError.value = ''
  actionNotice.value = ''
  if (!destination) { actionError.value = '这条通知的工单链接不可用，请从“报修工单”中查看。'; return }
  if (!item.read_at && !await readOne(item, false)) return
  if (!activeFor(userId, requestEpoch)) return
  workingId.value = item.id
  try {
    const failure = await router.push(destination)
    if (isNavigationFailure(failure) && activeFor(userId, requestEpoch)) actionError.value = '暂时无法打开工单，通知已保留，请重试。'
  } catch {
    if (activeFor(userId, requestEpoch)) actionError.value = '暂时无法打开工单，通知已保留，请重试。'
  } finally {
    if (activeFor(userId, requestEpoch)) workingId.value = ''
  }
}

watch(() => notifications.unreadCount, (value, previous) => {
  if (!loading.value && value > previous) hasNewMessages.value = true
})

watch(() => auth.user?.id ?? '', () => {
  epoch++
  listSequence++
  listController?.abort()
  writeController?.abort()
  items.value = []
  total.value = 0
  page.value = 1
  loading.value = false
  workingId.value = ''
  markingAll.value = false
  loadError.value = ''
  actionError.value = ''
  actionNotice.value = ''
  hasNewMessages.value = false
  if (auth.user) void refresh()
}, { flush: 'sync' })

onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; epoch++; listSequence++; listController?.abort(); writeController?.abort() })
</script>

<template>
  <section class="notifications-page">
    <header class="page-heading">
      <div>
        <p class="section-kicker">校园服务 / 消息</p>
        <h1>通知中心</h1>
        <p class="page-description">查看报修进度与超时提醒，继续处理相关工单。</p>
      </div>
      <el-button type="primary" plain :loading="markingAll" :disabled="loading || busy || !notifications.unreadCount" @click="readAll">全部标记为已读</el-button>
    </header>

    <div v-if="hasNewMessages" class="new-message-note" role="status">
      <span>收到新的通知。</span><el-button link type="primary" :disabled="busy || loading" @click="refresh">刷新当前列表</el-button>
    </div>
    <el-alert v-if="notifications.countError" class="page-alert" title="未读数量暂时无法更新，已显示的通知仍可查看。" type="warning" :closable="false" show-icon>
      <el-button link type="primary" @click="notifications.refreshUnread(true)">重新获取未读数</el-button>
    </el-alert>
    <el-alert v-if="actionError" class="page-alert" :title="actionError" type="error" :closable="false" show-icon />
    <p v-if="actionNotice" class="action-notice" role="status">{{ actionNotice }}</p>

    <section class="notifications-panel" aria-label="通知列表">
      <div class="list-toolbar">
        <div class="notification-filters" aria-label="通知筛选">
          <button type="button" :class="{ selected: !unreadOnly }" :aria-pressed="!unreadOnly" :disabled="busy || loading" @click="unreadOnly = false; filterChanged()">全部通知</button>
          <button type="button" :class="{ selected: unreadOnly }" :aria-pressed="unreadOnly" :disabled="busy || loading" @click="unreadOnly = true; filterChanged()">仅看未读<span v-if="notifications.unreadCount" class="filter-count">{{ notifications.unreadCount }}</span></button>
        </div>
        <el-button :loading="loading" :disabled="busy" @click="refresh">刷新列表</el-button>
      </div>
      <div class="result-caption"><span>{{ loading ? '正在读取通知…' : `共 ${total} 条${unreadOnly ? '未读' : ''}通知` }}</span><span>时间按北京时间显示</span></div>

      <div v-if="loadError" class="load-error" role="alert">
        <span>暂时无法加载通知：{{ loadError }}</span><el-button link type="primary" :disabled="busy || loading" @click="refresh">重新加载</el-button>
      </div>
      <div v-loading="loading" class="notification-results" :aria-busy="loading">
        <div v-if="items.length" class="notification-items">
          <article v-for="item in items" :key="item.id" :class="['notification-item', { unread: !item.read_at }]">
            <span class="read-indicator" aria-hidden="true" />
            <div class="notification-body">
              <div class="notification-meta"><span>{{ item.source_module === 'M3' ? '物业报修' : '业务通知' }}</span><span class="read-state">{{ item.read_at ? '已读' : '未读' }}</span><time :datetime="item.created_at">{{ formatTime(item.created_at) }}</time></div>
              <h2>{{ item.title }}</h2>
              <p class="notification-content">{{ item.content }}</p>
              <div class="notification-actions">
                <el-button type="primary" link :loading="workingId === item.id" :disabled="loading || busy" @click="openNotification(item)">查看工单</el-button>
                <el-button v-if="!item.read_at" link :disabled="loading || busy" @click="readOne(item)">标记已读</el-button>
              </div>
            </div>
          </article>
        </div>
        <el-empty v-else-if="!loading && !loadError" :description="unreadOnly ? '暂无未读通知' : '暂无通知'" :image-size="95">
          <p class="empty-note">工单有新的处理进展时，会在这里通知你。</p>
        </el-empty>
      </div>
      <div v-if="total > 0" class="list-pagination">
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10, 20, 50]" layout="prev, pager, next, sizes" :pager-count="5" :disabled="loading || busy" @current-change="refresh" @size-change="filterChanged" />
      </div>
    </section>
    <p class="page-footnote">仅展示发送给当前账号的站内通知。每 30 秒检查新消息；刷新页面后需重新登录。</p>
  </section>
</template>

<style scoped>
.notifications-page { color: #173b56; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 26px; }
.section-kicker { color: #64748b; font-size: 12px; margin: 0 0 9px; letter-spacing: .04em; }
.page-heading h1 { font-size: clamp(26px, 3vw, 34px); letter-spacing: -.04em; margin: 0 0 10px; font-weight: 650; }
.page-description { margin: 0; color: #64748b; font-size: 14px; line-height: 1.7; }
.page-alert { margin: 0 0 14px; }
.new-message-note { padding: 10px 16px; border: 1px solid #d7e4ff; border-radius: 8px; background: #eef4ff; color: #285eab; font-size: 13px; display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.action-notice { margin: 0 0 16px; color: #26836a; font-size: 13px; line-height: 1.7; }
.notifications-panel { background: #fff; border: 1px solid #e0e7ef; border-radius: 12px; overflow: hidden; box-shadow: 0 3px 16px #173b5604; }
.list-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 24px 16px; }
.notification-filters { display: flex; align-items: center; gap: 6px; }
.notification-filters button { display: inline-flex; align-items: center; gap: 7px; background: transparent; border: 0; border-radius: 6px; padding: 10px 13px; color: #64748b; font: inherit; font-size: 13px; white-space: nowrap; cursor: pointer; }
.notification-filters button:hover { background: #f5f8fc; color: #173b56; }.notification-filters button.selected { background: #eaf1ff; color: #2563eb; font-weight: 600; }
.notification-filters button:disabled { cursor: default; opacity: .65; }
.filter-count { font-size: 11px; min-width: 16px; }
.result-caption { display: flex; justify-content: space-between; gap: 12px; padding: 0 24px 14px; color: #8391a0; font-size: 12px; border-bottom: 1px solid #edf1f5; }
.notification-results { min-height: 240px; }
.notification-item { display: flex; align-items: flex-start; gap: 12px; padding: 23px 24px; border-bottom: 1px solid #edf1f5; }
.notification-item:last-child { border-bottom: 0; }.notification-item.unread { background: #fbfdff; }
.read-indicator { flex: 0 0 6px; width: 6px; height: 6px; margin-top: 6px; border-radius: 50%; background: transparent; }.unread .read-indicator { background: #3d77d7; }
.notification-body { min-width: 0; flex: 1; }
.notification-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; color: #8190a0; font-size: 11px; line-height: 1.6; }.notification-meta time { margin-left: auto; font-family: Consolas, monospace; }
.read-state { color: #94a3b8; }.unread .read-state { color: #3d77d7; }
.notification-body h2 { margin: 9px 0 8px; font-size: 15px; font-weight: 600; line-height: 1.6; overflow-wrap: anywhere; }
.notification-content { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #64748b; font-size: 13px; line-height: 1.8; }
.notification-actions { display: flex; flex-wrap: wrap; gap: 15px; margin-top: 12px; }.notification-actions :deep(.el-button + .el-button) { margin-left: 0; }
.list-pagination { display: flex; justify-content: flex-end; padding: 18px 24px; border-top: 1px solid #edf1f5; }
.load-error { padding: 15px 24px; background: #fff8ed; color: #916523; font-size: 13px; line-height: 1.7; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.empty-note { margin: 0; color: #8391a0; font-size: 12px; line-height: 1.7; }
.page-footnote { margin: 18px 2px; font-size: 12px; color: #8391a0; line-height: 1.7; }
button:focus-visible { outline: 2px solid #2563eb; outline-offset: 3px; }
@media (max-width: 600px) {
  .page-heading { flex-direction: column; align-items: flex-start; gap: 16px; margin-bottom: 20px; }
  .list-toolbar { padding: 14px 12px; gap: 9px; }.notification-filters { gap: 0; }.notification-filters button { padding: 9px 10px; font-size: 12px; }
  .list-toolbar > :deep(.el-button) { padding-left: 10px; padding-right: 10px; }
  .result-caption { padding: 0 16px 12px; font-size: 11px; }.result-caption span:last-child { font-size: 10px; }
  .notification-item { padding: 19px 16px; gap: 10px; }.notification-meta { column-gap: 10px; row-gap: 3px; }.notification-meta time { margin-left: 0; flex-basis: 100%; }
  .notification-body h2 { font-size: 14px; }.notification-content { font-size: 12px; }
  .load-error { padding: 13px 16px; }.list-pagination { padding: 16px 12px; justify-content: center; }.list-pagination :deep(.el-pagination__sizes) { display: none; }
}
</style>
