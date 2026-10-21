<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { AuthUser } from '../api/auth'
import {
  acceptWorkOrder, assignWorkOrder, getWorkOrder, listRepairers,
  startWorkOrder, submitCompletion, type RatingView, type WorkOrderDetail,
} from '../api/m3'
import { errorMessage } from '../api/request'
import { rooms, type Room } from '../api/resources'
import M3ExtensionsPanel from '../components/m3/M3ExtensionsPanel.vue'
import { useAuthStore } from '../stores/auth'
import {
  categoryLabels, formatTime, historyLabel, historyNote, orderPermissions,
  statusLabels, statusTagType,
} from '../utils/m3'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const order = ref<WorkOrderDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const unavailable = ref(false)
const notice = ref('')
const room = ref<Room | null>(null)
const submitting = ref(false)
const actionError = ref('')
const dialog = ref<'assign' | 'start' | 'complete' | 'accept' | null>(null)
// Keep the closing dialog's content stable during Element Plus's leave animation.
const displayedAction = ref<'assign' | 'start' | 'complete' | 'accept'>('assign')
const assignee = ref('')
const completionNote = ref('')
const acceptanceComment = ref('')
const acceptancePassed = ref(true)
const acceptanceRound = ref(0)
const repairers = ref<AuthUser[]>([])
const repairerPage = ref(1)
const repairerTotal = ref(0)
const repairersLoading = ref(false)
const repairersError = ref('')
const knownUsers = new Map<string, AuthUser>()
let detailSequence = 0
let repairerSequence = 0
let detailController: AbortController | null = null
let repairerController: AbortController | null = null
let disposed = false

const id = computed(() => String(route.params.id ?? ''))
const permissions = computed(() => order.value ? orderPermissions(auth.user, order.value) : {
  assign: false, start: false, complete: false, accept: false,
})
const selfAcceptanceBlocked = computed(() => order.value?.status === 'WAIT_ACCEPTANCE'
  && auth.user?.roles.includes('PROPERTY_ADMIN') && order.value.assignee_id === auth.user.id)
const progressIndex = computed(() => ({ REPORTED: 0, ASSIGNED: 1, REPAIRING: 2,
  WAIT_ACCEPTANCE: 3, COMPLETED: 4 })[order.value?.status ?? 'REPORTED'])
const stages = ['已上报', '已派单', '维修中', '待验收', '已完成']
const currentDescription = computed(() => {
  switch (order.value?.status) {
    case 'REPORTED': return '已收到报修，等待物业管理员安排维修人员。'
    case 'ASSIGNED': return '已指定维修人员，等待开始维修。'
    case 'REPAIRING': return order.value.completion_round > 0
      ? '验收未通过，维修人员正在继续处理；教室维修限制保持生效。'
      : '维修进行中，教室维修限制已生效。'
    case 'WAIT_ACCEPTANCE': return '维修人员已提交完工说明，等待物业管理员验收；教室维修限制仍生效。'
    case 'COMPLETED': return '本工单已验收完成；教室是否可用还需结合其他占用与维修情况确认。'
    default: return ''
  }
})
const actionTitle = computed(() => ({ assign: '安排维修人员', start: '开始维修',
  complete: '提交完工', accept: acceptancePassed.value ? '确认验收通过' : '驳回并继续维修' })[displayedAction.value])
const noActionText = computed(() => {
  if (order.value?.status === 'COMPLETED') return '工单已完成，可以在下方查看完整处理记录。'
  if (selfAcceptanceBlocked.value) return '你是本单维修人员，不能验收自己负责维修的工单。请由其他物业管理员验收。'
  if (order.value?.status === 'REPORTED') return '等待物业管理员派单。'
  if (order.value?.status === 'ASSIGNED') return '等待指定维修人员开始维修。'
  if (order.value?.status === 'REPAIRING') return '等待指定维修人员完成处理。'
  return '等待物业管理员验收。'
})
const hasAction = computed(() => Object.values(permissions.value).some(Boolean))
const orderedHistory = computed(() => [...(order.value?.history ?? [])].sort((a, b) => {
  const byTime = a.created_at.localeCompare(b.created_at)
  if (byTime !== 0) return byTime
  return a.id.length - b.id.length || a.id.localeCompare(b.id)
}))

function personName(userId: string | null) {
  if (!userId) return '尚未指定'
  if (auth.user?.id === userId) return `${auth.user.display_name}（你）`
  const known = knownUsers.get(userId)
  return known ? known.display_name : `用户 #${userId}`
}
function updateOrder(value: WorkOrderDetail) {
  if (value.id === id.value) order.value = value
}
function ratingSubmitted(value: RatingView) {
  if (!order.value || value.work_order_id !== id.value) return
  // Keep the confirmed rating visible while the new request retrieves its audit
  // event. Never reuse a child component's older copy of the complete detail.
  order.value = { ...order.value, rating: value }
  void refresh(false, true)
}
function cancelled(error: unknown) { return axios.isCancel(error) }
function historyStatus(status: string | null) {
  if (status === 'ACTIVE') return '维修限制生效'
  if (status === 'RELEASED') return '维修限制已解除'
  return status ? statusLabels[status as keyof typeof statusLabels] ?? status : ''
}
async function refresh(resetNotice = false, preserveOrder = false) {
  if (resetNotice) notice.value = ''
  const target = id.value
  const current = ++detailSequence
  detailController?.abort()
  detailController = new AbortController()
  const signal = detailController.signal
  loading.value = true
  loadError.value = ''
  unavailable.value = false
  if (!preserveOrder) {
    order.value = null
    room.value = null
  }
  try {
    const result = await getWorkOrder(target, signal)
    if (disposed || current !== detailSequence || target !== id.value) return
    order.value = result
    // A repairer's assigned order may refer to a room outside their room-query scope.
    // The optional name lookup must never prevent viewing the authorized order.
    void rooms(1, 100, signal).then(result => {
      if (!disposed && current === detailSequence && target === id.value) {
        room.value = result.items.find(item => item.id === order.value?.room_id) ?? null
      }
    }).catch(() => { /* The order's string room ID remains a usable fallback. */ })
  } catch (error) {
    if (disposed || current !== detailSequence || cancelled(error)) return
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    if (preserveOrder && order.value?.id === target && status !== 401 && status !== 403 && status !== 404) {
      notice.value = '评价已成功提交，完整处理记录暂时未刷新。当前保留已确认的评价，请稍后刷新详情；无需再次提交。'
      return
    }
    if (preserveOrder) order.value = null
    unavailable.value = axios.isAxiosError(error) && error.response?.status === 404
    loadError.value = unavailable.value ? '这张工单不存在，或当前账号无权查看。' : errorMessage(error)
  } finally {
    if (current === detailSequence) loading.value = false
  }
}
async function fetchRepairers(page = 1) {
  const current = ++repairerSequence
  repairerController?.abort()
  repairerController = new AbortController()
  repairersLoading.value = true
  repairersError.value = ''
  repairerPage.value = page
  try {
    const result = await listRepairers(page, 100, repairerController.signal)
    if (disposed || current !== repairerSequence || dialog.value !== 'assign') return
    repairers.value = result.items
    repairerTotal.value = result.total
    result.items.forEach(user => knownUsers.set(user.id, user))
  } catch (error) {
    if (disposed || current !== repairerSequence || cancelled(error)) return
    repairers.value = []
    repairersError.value = errorMessage(error)
  } finally {
    if (current === repairerSequence) repairersLoading.value = false
  }
}
function openAction(action: 'assign' | 'start' | 'complete' | 'accept', passed = true) {
  if (!order.value || submitting.value || !permissions.value[action]) return
  actionError.value = ''
  notice.value = ''
  displayedAction.value = action
  dialog.value = action
  assignee.value = ''
  completionNote.value = ''
  acceptanceComment.value = ''
  acceptancePassed.value = passed
  // Preserve the round being reviewed. A refresh must never silently approve a newer round.
  acceptanceRound.value = order.value.completion_round
  if (action === 'assign') void fetchRepairers()
}
function closeDialog() {
  if (submitting.value) return
  dialog.value = null
  repairerController?.abort()
  ++repairerSequence
}
function beforeClose(done: () => void) {
  if (submitting.value) return
  closeDialog()
  done()
}
function updateDialogVisibility(visible: boolean) { if (!visible) closeDialog() }
function changeRepairerPage(page: number) { assignee.value = ''; void fetchRepairers(page) }
async function submitAction() {
  if (!order.value || !dialog.value || submitting.value) return
  const action = dialog.value
  if (!permissions.value[action]) {
    actionError.value = '当前状态或身份不允许执行此操作，请刷新工单。'
    return
  }
  const completion = completionNote.value.trim()
  const comment = acceptanceComment.value.trim()
  if (action === 'assign' && !assignee.value) { actionError.value = '请选择一位维修人员。'; return }
  if (action === 'complete' && (!completion || completion.length > 2000)) {
    actionError.value = '请填写 1～2000 字的完工说明。'; return
  }
  if (action === 'accept' && ((!acceptancePassed.value && !comment) || comment.length > 500)) {
    actionError.value = !comment ? '请填写驳回原因，便于维修人员继续处理。' : '验收意见不能超过 500 字。'; return
  }
  const target = order.value.id
  submitting.value = true
  actionError.value = ''
  try {
    if (action === 'assign') await assignWorkOrder(target, { assignee_id: assignee.value })
    if (action === 'start') await startWorkOrder(target)
    if (action === 'complete') await submitCompletion(target, { completion_note: completion })
    if (action === 'accept') await acceptWorkOrder(target, {
      passed: acceptancePassed.value, comment: comment || null,
      expected_completion_round: acceptanceRound.value,
    })
    if (disposed || target !== id.value) return
    dialog.value = null
    ElMessage.success(action === 'assign' ? '派单成功' : action === 'start' ? '已开始维修'
      : action === 'complete' ? '已提交完工，等待验收' : acceptancePassed.value ? '验收通过，工单已完成' : '已驳回，等待继续维修')
    await refresh()
  } catch (error) {
    if (disposed || target !== id.value) return
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    if (status === 409 || status === 403 || status === 404) {
      dialog.value = null
      notice.value = status === 409
        ? '工单状态或验收轮次已变化，已重新获取详情。请核对当前处理记录，重新打开操作并确认。'
        : errorMessage(error)
      await refresh()
    } else if (!status || status >= 500) {
      dialog.value = null
      notice.value = '本次操作结果尚未确认，已尝试重新获取详情。请先核对状态与处理记录，再决定是否重新操作；系统不会自动重提。'
      await refresh()
    } else {
      actionError.value = errorMessage(error)
    }
  } finally { submitting.value = false }
}
watch(id, () => {
  dialog.value = null
  repairerController?.abort()
  ++repairerSequence
  actionError.value = ''
  notice.value = ''
  void refresh()
}, { immediate: true })
onBeforeUnmount(() => {
  disposed = true
  ++detailSequence
  ++repairerSequence
  detailController?.abort()
  repairerController?.abort()
})
</script>

<template>
  <section class="order-detail" aria-labelledby="detail-title">
    <div class="page-toolbar">
      <el-button text @click="router.push('/m3/work-orders')"><span aria-hidden="true">←</span> 返回工单列表</el-button>
      <el-button :loading="loading" :disabled="submitting || dialog !== null" @click="refresh(true)">刷新详情</el-button>
    </div>
    <el-alert v-if="notice" :title="notice" type="warning" :closable="false" show-icon class="notice" role="status" />
    <div v-if="loading && !order" class="panel loading-panel" aria-live="polite" aria-busy="true">
      <el-skeleton :rows="8" animated />
      <span class="sr-only">正在加载工单详情</span>
    </div>
    <div v-else-if="loadError" class="panel error-panel" role="alert">
      <el-result :icon="unavailable ? 'warning' : 'error'" :title="unavailable ? '暂时无法查看工单' : '详情加载失败'" :sub-title="loadError">
        <template #extra>
          <el-button type="primary" @click="refresh()">重新加载</el-button>
          <el-button @click="router.push('/m3/work-orders')">返回列表</el-button>
        </template>
      </el-result>
    </div>
    <template v-else-if="order">
      <header class="detail-heading">
        <div>
          <div class="eyebrow">工单详情 · #{{ order.id }}</div>
          <h1 id="detail-title">{{ order.title }}</h1>
          <div class="heading-meta">
            <el-tag :type="statusTagType(order.status)" effect="light">{{ statusLabels[order.status] }}</el-tag>
            <span>{{ categoryLabels[order.category] }}</span>
            <span>上报于 {{ formatTime(order.created_at) }}</span>
          </div>
        </div>
        <span class="timezone">时间均为北京时间</span>
      </header>

      <section class="panel progress-panel" aria-label="工单处理进度">
        <ol class="progress-track">
          <li v-for="(stage, index) in stages" :key="stage" :class="{ reached: index <= progressIndex, current: index === progressIndex }"
            :aria-current="index === progressIndex ? 'step' : undefined">
            <span class="progress-dot" aria-hidden="true">{{ index < progressIndex ? '✓' : index + 1 }}</span>
            <span>{{ stage }}</span>
          </li>
        </ol>
        <p class="progress-caption">{{ currentDescription }}</p>
      </section>

      <div class="detail-grid">
        <div class="main-column">
          <section class="panel information-panel">
            <div class="panel-heading"><h2>故障信息</h2><span>工单 #{{ order.id }}</span></div>
            <dl class="facts">
              <div><dt>报修教室</dt><dd>{{ room ? `${room.building_name} · ${room.name}` : `教室 #${order.room_id}` }}<span v-if="room" class="secondary">{{ room.code }}</span></dd></div>
              <div><dt>故障分类</dt><dd>{{ categoryLabels[order.category] }}</dd></div>
              <div><dt>上报人</dt><dd>{{ personName(order.reporter_id) }}<span class="secondary">ID {{ order.reporter_id }}</span></dd></div>
              <div><dt>维修人员</dt><dd>{{ personName(order.assignee_id) }}<span v-if="order.assignee_id" class="secondary">ID {{ order.assignee_id }}</span></dd></div>
            </dl>
            <div class="description-block"><h3>故障描述</h3><p class="preserve-lines">{{ order.description }}</p></div>
          </section>

          <section v-if="order.completion_note || order.acceptance_comment" class="panel latest-panel">
            <div class="panel-heading"><h2>最近处理结果</h2><span>第 {{ order.completion_round }} 轮完工</span></div>
            <div v-if="order.completion_note" class="result-entry">
              <div class="result-caption"><h3>完工说明</h3><time>{{ formatTime(order.submitted_at) }}</time></div>
              <p class="preserve-lines">{{ order.completion_note }}</p>
            </div>
            <div v-if="order.acceptance_comment" class="result-entry">
              <div class="result-caption"><h3>最近验收意见</h3><span v-if="order.accepted_at">{{ formatTime(order.accepted_at) }}</span></div>
              <p class="preserve-lines">{{ order.acceptance_comment }}</p>
              <p class="context-note">对应轮次和验收结果请见下方处理记录。</p>
            </div>
          </section>

          <M3ExtensionsPanel :key="order.id" :order="order" @updated="updateOrder" @rated="ratingSubmitted" />

          <section class="panel history-panel">
            <div class="panel-heading"><h2>完整处理记录</h2><span>{{ order.history.length }} 条记录 · 北京时间</span></div>
            <ol v-if="orderedHistory.length" class="history-list">
              <li v-for="(event, index) in orderedHistory" :key="event.id" :class="{ 'latest-event': index === orderedHistory.length - 1 }">
                <span class="history-marker" aria-hidden="true" />
                <div class="event-heading"><h3>{{ historyLabel(event.action) }}</h3><time :datetime="event.created_at">{{ formatTime(event.created_at) }}</time></div>
                <div class="event-meta">
                  <span>{{ personName(event.actor_id) }}</span>
                  <span v-if="event.from_status || event.to_status" class="transition"><template v-if="event.from_status">{{ historyStatus(event.from_status) }} → </template>{{ historyStatus(event.to_status) || '—' }}</span>
                </div>
                <p v-if="historyNote(event.note)" class="event-note preserve-lines">{{ historyNote(event.note) }}</p>
              </li>
            </ol>
            <p v-else class="context-note">暂未返回处理记录。</p>
          </section>
        </div>

        <aside class="side-column" aria-label="工单处理操作">
          <section class="panel action-panel">
            <div class="panel-heading"><h2>当前待办</h2><span class="live-dot" aria-hidden="true" /></div>
            <el-tag :type="statusTagType(order.status)" effect="light">{{ statusLabels[order.status] }}</el-tag>
            <p class="action-description">{{ currentDescription }}</p>
            <div v-if="hasAction" class="action-buttons">
              <el-button v-if="permissions.assign" type="primary" :disabled="submitting" @click="openAction('assign')">安排维修人员</el-button>
              <el-button v-if="permissions.start" type="primary" :disabled="submitting" @click="openAction('start')">开始维修</el-button>
              <el-button v-if="permissions.complete" type="primary" :disabled="submitting" @click="openAction('complete')">填写完工说明</el-button>
              <template v-if="permissions.accept">
                <el-button type="primary" :disabled="submitting" @click="openAction('accept', true)">验收通过</el-button>
                <el-button :disabled="submitting" @click="openAction('accept', false)">驳回返修</el-button>
              </template>
            </div>
            <p v-else class="next-action" :class="{ 'self-acceptance-warning': selfAcceptanceBlocked }">{{ noActionText }}</p>
          </section>
          <section class="panel timestamps-panel">
            <h2>关键时间</h2>
            <dl>
              <div><dt>上报</dt><dd>{{ formatTime(order.created_at) }}</dd></div>
              <div><dt>派单</dt><dd>{{ formatTime(order.assigned_at) }}</dd></div>
              <div><dt>开始维修</dt><dd>{{ formatTime(order.started_at) }}</dd></div>
              <div><dt>最近提交完工</dt><dd>{{ formatTime(order.submitted_at) }}</dd></div>
              <div><dt>验收通过</dt><dd>{{ formatTime(order.accepted_at) }}</dd></div>
            </dl>
          </section>
          <div class="resource-note"><span aria-hidden="true">ⓘ</span><p>维修开始后，教室暂停新的预约与审批；待验收期间继续保持。已有预约请按实际情况协调。</p></div>
        </aside>
      </div>
    </template>

    <el-dialog :model-value="dialog !== null" :title="actionTitle" width="min(560px, calc(100vw - 32px))"
      :close-on-click-modal="false" :close-on-press-escape="!submitting" :show-close="!submitting"
      :before-close="beforeClose" class="m3-action-dialog" @update:model-value="updateDialogVisibility">
      <div v-if="order" class="dialog-context">工单 #{{ order.id }} · {{ order.title }}</div>
      <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon class="dialog-error" role="alert" />
      <el-form label-position="top" @submit.prevent="submitAction">
        <template v-if="displayedAction === 'assign'">
          <el-form-item label="维修人员" required>
            <el-select v-model="assignee" placeholder="选择负责本单的维修人员" filterable :loading="repairersLoading" :disabled="submitting || repairersLoading" class="wide-control">
              <el-option v-for="user in repairers" :key="user.id" :label="`${user.display_name} · ${user.username}（ID ${user.id}）`" :value="user.id" />
            </el-select>
          </el-form-item>
          <div v-if="repairersError" class="repairers-error"><span>{{ repairersError }}</span><el-button link type="primary" @click="fetchRepairers(repairerPage)">重新加载</el-button></div>
          <p v-else-if="!repairersLoading && !repairers.length" class="context-note">暂无可分配的维修人员，请联系管理员确认人员账号与权限。</p>
          <el-pagination v-if="repairerTotal > 100" small layout="prev, pager, next" :current-page="repairerPage" :page-size="100" :total="repairerTotal"
            :disabled="submitting || repairersLoading" @current-change="changeRepairerPage" />
          <p class="context-note">本次派单后，由指定人员开始维修。</p>
        </template>
        <template v-else-if="displayedAction === 'start'">
          <el-alert title="开始后，教室将进入维修限制状态" type="warning" :closable="false" show-icon />
          <p class="dialog-copy">请确认你已准备好处理本单。教室将暂停新的预约与审批；完工后仍需物业管理员验收。</p>
        </template>
        <template v-else-if="displayedAction === 'complete'">
          <el-form-item label="完工说明" required>
            <el-input v-model="completionNote" type="textarea" :rows="6" maxlength="2000" show-word-limit :disabled="submitting"
              placeholder="说明故障原因、维修内容及检查结果，便于管理员验收。" />
          </el-form-item>
          <p class="context-note">提交后进入待验收状态，教室维修限制继续保持。请确认内容后提交一次。</p>
        </template>
        <template v-else-if="displayedAction === 'accept'">
          <el-alert :title="`正在验收第 ${acceptanceRound} 轮完工`" :type="acceptancePassed ? 'info' : 'warning'" :closable="false" show-icon />
          <div v-if="order?.completion_note" class="review-note"><h3>本轮完工说明</h3><p class="preserve-lines">{{ order.completion_note }}</p></div>
          <el-form-item :label="acceptancePassed ? '验收意见（选填）' : '驳回原因'" :required="!acceptancePassed">
            <el-input v-model="acceptanceComment" type="textarea" :rows="4" maxlength="500" show-word-limit :disabled="submitting"
              :placeholder="acceptancePassed ? '可填写复测情况与验收结论。' : '请说明仍存在的问题与需要继续处理的事项。'" />
          </el-form-item>
          <p class="context-note">{{ acceptancePassed ? '通过后本单完成；如有其他占用或维修，教室仍可能不可预约。' : '驳回后工单回到维修中，继续保持教室维修限制。' }}</p>
        </template>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="closeDialog">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="displayedAction === 'assign' && (repairersLoading || !assignee || !!repairersError)" @click="submitAction">
          {{ displayedAction === 'assign' ? '确认派单' : displayedAction === 'start' ? '确认开始维修' : displayedAction === 'complete' ? '提交完工' : acceptancePassed ? '确认验收通过' : '确认驳回' }}
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.order-detail { --m3-ink: #173b56; color: var(--m3-ink); }
.page-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; gap: 12px; }
.page-toolbar > .el-button:first-child { margin-left: -12px; color: #64748b; }
.page-toolbar .el-button span[aria-hidden] { margin-right: 8px; }
.notice { margin-bottom: 20px; }
.panel { background: #fff; border: 1px solid #e3eaf1; border-radius: 14px; padding: 28px; min-width: 0; }
.loading-panel { min-height: 360px; }
.detail-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 26px; }
.detail-heading h1 { font-size: clamp(23px, 2.6vw, 32px); line-height: 1.4; margin: 10px 0 14px; color: var(--m3-ink); overflow-wrap: anywhere; }
.heading-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 18px; color: #64748b; font-size: 13px; line-height: 1.6; }
.timezone { color: #8191a2; font-size: 12px; white-space: nowrap; margin-top: 5px; }
.progress-panel { margin-bottom: 24px; padding: 26px 30px 20px; }
.progress-track { display: flex; margin: 0; padding: 0; list-style: none; }
.progress-track li { position: relative; display: flex; flex-direction: column; align-items: center; gap: 10px; flex: 1; color: #91a0af; font-size: 13px; }
.progress-track li::before { content: ''; position: absolute; height: 2px; background: #e6edf3; top: 15px; left: -50%; right: 50%; }
.progress-track li:first-child::before { content: none; }
.progress-track li.reached::before { background: #bad1ed; }
.progress-dot { display: flex; align-items: center; justify-content: center; z-index: 1; background: #f2f5f9; border: 1px solid #e1e8f0; width: 32px; height: 32px; border-radius: 50%; font-size: 12px; font-weight: 700; }
.reached .progress-dot { color: #2563eb; background: #eef4ff; border-color: #c4d7fd; }
.progress-track .current { color: #173b56; font-weight: 700; }
.current .progress-dot { background: #2563eb; border-color: #2563eb; color: #fff; box-shadow: 0 0 0 5px #f0f5ff; }
.progress-caption { margin: 22px 0 0; padding-top: 18px; border-top: 1px solid #eef2f6; color: #64748b; font-size: 13px; line-height: 1.8; }
.detail-grid { display: grid; grid-template-columns: minmax(0, 1fr) 300px; align-items: start; gap: 24px; }
.main-column, .side-column { display: grid; gap: 24px; min-width: 0; }
.side-column { position: sticky; top: 24px; }
.panel-heading { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 24px; }
.panel h2 { margin: 0; font-size: 17px; line-height: 1.5; font-weight: 650; }
.panel-heading > span { color: #94a3b8; font-size: 12px; }
.facts { display: grid; grid-template-columns: 1fr 1fr; margin: 0; gap: 26px 20px; }
.facts dt, .timestamps-panel dt { color: #7b8da1; font-size: 12px; margin-bottom: 8px; }
.facts dd { margin: 0; line-height: 1.6; font-size: 14px; overflow-wrap: anywhere; }
.secondary { display: block; color: #94a3b8; font-size: 12px; margin-top: 2px; }
.description-block { border-top: 1px solid #edf2f7; margin-top: 28px; padding-top: 23px; }
h3 { font-size: 13px; margin: 0; font-weight: 650; line-height: 1.6; }
.preserve-lines { white-space: pre-wrap; overflow-wrap: anywhere; }
.description-block p, .result-entry p { color: #52677c; font-size: 14px; line-height: 1.9; margin: 12px 0 0; }
.result-entry + .result-entry { border-top: 1px solid #edf2f7; padding-top: 22px; margin-top: 22px; }
.result-caption { display: flex; justify-content: space-between; gap: 10px; align-items: center; flex-wrap: wrap; }
.result-caption time, .result-caption > span { color: #94a3b8; font-size: 12px; }
.context-note, .result-entry .context-note { color: #8191a2; font-size: 12px; line-height: 1.8; margin: 14px 0 0; }
.history-list { padding: 0 0 0 8px; list-style: none; margin: 6px 0 0; }
.history-list li { position: relative; border-left: 1px solid #e1e8f0; padding: 0 0 29px 26px; }
.history-list li:last-child { border-color: transparent; padding-bottom: 0; }
.history-marker { position: absolute; top: 5px; left: -5px; width: 9px; height: 9px; background: #fff; border: 2px solid #a9bfd8; border-radius: 50%; }
.latest-event .history-marker { background: #2563eb; border-color: #2563eb; box-shadow: 0 0 0 4px #eef4ff; }
.event-heading { display: flex; flex-wrap: wrap; gap: 4px 16px; justify-content: space-between; align-items: center; }
.event-heading time { font-size: 12px; color: #94a3b8; }
.event-meta { display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 12px; color: #8191a2; margin-top: 7px; }
.transition { color: #6987a7; }
.event-note { background: #f7f9fc; padding: 12px 14px; border-radius: 8px; color: #60758a; font-size: 13px; line-height: 1.8; margin: 11px 0 0; }
.action-panel .panel-heading { margin-bottom: 20px; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: #4e91c5; }
.action-description, .next-action { font-size: 13px; color: #64748b; line-height: 1.9; margin: 16px 0 0; }
.action-buttons { display: grid; gap: 10px; margin-top: 24px; }
.action-buttons .el-button { width: 100%; margin: 0; min-height: 40px; }
.next-action { padding-top: 16px; border-top: 1px solid #edf2f7; }
.self-acceptance-warning { color: #b77925; }
.timestamps-panel h2 { margin-bottom: 24px; }
.timestamps-panel dl { display: grid; gap: 20px; margin: 0; }
.timestamps-panel dd { margin: 0; font-size: 12px; color: #526b83; font-variant-numeric: tabular-nums; }
.resource-note { display: flex; align-items: flex-start; gap: 10px; padding: 0 8px; color: #7f92a6; }
.resource-note > span { margin-top: 2px; }
.resource-note p { font-size: 12px; line-height: 1.9; margin: 0; }
.dialog-context { color: #64748b; font-size: 13px; margin-bottom: 20px; line-height: 1.7; overflow-wrap: anywhere; }
.dialog-error { margin-bottom: 18px; }
.wide-control { width: 100%; }
.repairers-error { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: #b45309; font-size: 13px; }
.dialog-copy { color: #64748b; font-size: 14px; line-height: 1.9; margin: 18px 0 0; }
.review-note { margin: 18px 0 22px; padding: 16px; background: #f6f8fb; border-radius: 8px; }
.review-note h3 { color: #52677c; }
.review-note p { font-size: 13px; line-height: 1.8; color: #64748b; margin: 8px 0 0; max-height: 220px; overflow-y: auto; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (max-width: 1000px) {
  .detail-grid { grid-template-columns: minmax(0, 1fr) 260px; gap: 18px; }
  .panel { padding: 22px; }
  .main-column, .side-column { gap: 18px; }
}
@media (max-width: 760px) {
  .detail-grid { grid-template-columns: 1fr; }
  .side-column { position: static; grid-row: 1; }
  .timestamps-panel { display: none; }
  .detail-heading { gap: 12px; flex-direction: column; }
  .timezone { margin-top: 0; }
  .action-panel { padding: 20px; }
  .action-panel .panel-heading { margin-bottom: 12px; }
  .action-description { margin-top: 12px; }
  .action-buttons { margin-top: 16px; }
  .panel-heading { margin-bottom: 20px; }
}
@media (max-width: 440px) {
  .panel { padding: 20px 17px; border-radius: 10px; }
  .facts { gap: 22px 16px; }
  .progress-panel { padding: 22px 12px 16px; }
  .progress-track li { font-size: 11px; gap: 10px; }
  .progress-caption { margin-left: 5px; margin-right: 5px; font-size: 12px; }
  .event-heading { flex-direction: column; align-items: flex-start; }
  .heading-meta { gap: 8px 12px; }
  .page-toolbar { margin-bottom: 20px; }
}
</style>
