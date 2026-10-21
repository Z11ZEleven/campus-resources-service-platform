<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getTimeoutPolicy, listWorkOrders, scanTimeouts, type TimeoutPolicy, type WorkOrder, type WorkOrderCategory, type WorkOrderStatus } from '../api/m3'
import { errorMessage } from '../api/request'
import { rooms, type Room } from '../api/resources'
import CreateWorkOrderDialog from '../components/m3/CreateWorkOrderDialog.vue'
import { useAuthStore } from '../stores/auth'
import { canCreate, categoryLabels, categoryOptions, formatTime, scopeDescription, statusLabels, statusOptions, statusTagType } from '../utils/m3'

const auth = useAuthStore()
const router = useRouter()
const items = ref<WorkOrder[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const page = ref(1)
const pageSize = ref(10)
const status = ref<WorkOrderStatus | ''>('')
const category = ref<WorkOrderCategory | ''>('')
const roomId = ref('')
const roomOptions = ref<Room[]>([])
const roomLoading = ref(false)
const roomError = ref('')
const showCreate = ref(false)
const timeoutPolicy = ref<TimeoutPolicy | null>(null)
const timeoutPolicyError = ref('')
const scanning = ref(false)
const scanNotice = ref('')
const scanError = ref(false)
let listController: AbortController | null = null
let roomController: AbortController | null = null
let policyController: AbortController | null = null
let scanController: AbortController | null = null
let listSequence = 0
let alive = true
const roomNames = computed(() => new Map(roomOptions.value.map(room => [room.id, `${room.code} · ${room.name}`])))
const hasFilters = computed(() => Boolean(status.value || category.value || roomId.value))
const scope = computed(() => scopeDescription(auth.user))
const isPropertyAdmin = computed(() => Boolean(auth.user?.roles.includes('PROPERTY_ADMIN')))
const emptyText = computed(() => hasFilters.value ? '没有符合当前条件的工单' : auth.user?.roles.includes('REPAIRER') && !canCreate(auth.user) ? '暂无分配给你的维修任务' : '暂无可查看的工单')

async function refresh() {
  const sequence = ++listSequence
  listController?.abort()
  listController = new AbortController()
  loading.value = true
  loadError.value = ''
  items.value = []
  try {
    const result = await listWorkOrders({
      page: page.value, page_size: pageSize.value,
      status: status.value || undefined, category: category.value || undefined,
      room_id: roomId.value || undefined,
    }, listController.signal)
    if (!alive || sequence !== listSequence) return
    items.value = result.items
    total.value = result.total
    // A concurrent change can remove the last item on the last page.
    if (!items.value.length && total.value > 0 && page.value > 1) {
      page.value = Math.max(1, Math.ceil(total.value / pageSize.value))
      await refresh()
    }
  } catch (error) {
    if (alive && sequence === listSequence && !axios.isCancel(error)) {
      loadError.value = errorMessage(error)
      total.value = 0
    }
  } finally {
    if (alive && sequence === listSequence) loading.value = false
  }
}

async function loadRooms() {
  roomController?.abort()
  const controller = new AbortController()
  roomController = controller
  roomLoading.value = true
  roomError.value = ''
  try {
    const collected: Room[] = []
    for (let current = 1; ; current++) {
      const result = await rooms(current, 100, controller.signal)
      collected.push(...result.items)
      if (!result.items.length || collected.length >= result.total) break
    }
    if (alive && !controller.signal.aborted) roomOptions.value = collected
  } catch (error) {
    if (alive && !controller.signal.aborted && !axios.isCancel(error)) roomError.value = errorMessage(error)
  } finally {
    if (alive && !controller.signal.aborted) roomLoading.value = false
  }
}

async function loadTimeoutPolicy() {
  policyController?.abort()
  const controller = new AbortController()
  policyController = controller
  timeoutPolicyError.value = ''
  try {
    const result = await getTimeoutPolicy(controller.signal)
    if (alive && !controller.signal.aborted) timeoutPolicy.value = result
  } catch (error) {
    if (alive && !controller.signal.aborted && !axios.isCancel(error)) timeoutPolicyError.value = errorMessage(error)
  }
}

async function scanOverdue() {
  if (!isPropertyAdmin.value || scanning.value) return
  scanning.value = true
  scanNotice.value = ''
  scanError.value = false
  scanController = new AbortController()
  try {
    const result = await scanTimeouts(scanController.signal)
    if (!alive) return
    scanNotice.value = `超时补扫完成：检查 ${result.checked} 条候选工单，本次升级 ${result.escalated} 条。重复扫描不会重复升级同一阶段。`
    await refresh()
  } catch (error) {
    if (!alive || axios.isCancel(error)) return
    scanError.value = true
    const statusCode = axios.isAxiosError(error) ? error.response?.status : undefined
    scanNotice.value = !statusCode || statusCode >= 500
      ? '本次补扫结果尚未确认，已尝试刷新列表。请核对工单升级记录；系统不会自动重发补扫请求。'
      : errorMessage(error)
    await refresh()
  } finally {
    if (alive) scanning.value = false
  }
}

function filterChanged() { page.value = 1; void refresh() }
function chooseStatus(value: WorkOrderStatus | '') { status.value = value; filterChanged() }
function resetFilters() { status.value = ''; category.value = ''; roomId.value = ''; filterChanged() }
function openOrder(id: string) { void router.push(`/m3/work-orders/${encodeURIComponent(id)}`) }
function created(order: WorkOrder) { showCreate.value = false; openOrder(order.id) }

onMounted(() => { void refresh(); void loadRooms(); void loadTimeoutPolicy() })
onBeforeUnmount(() => { alive = false; listSequence++; listController?.abort(); roomController?.abort(); policyController?.abort(); scanController?.abort() })
</script>

<template>
  <section class="work-orders-page">
    <header class="page-heading">
      <div>
        <p class="section-kicker">校园服务 / 物业报修</p>
        <h1>报修工单</h1>
        <p class="page-description">从故障上报到验收，查看每一步的处理进度。</p>
      </div>
      <el-button v-if="canCreate(auth.user)" type="primary" size="large" @click="showCreate = true">
        <span class="button-plus" aria-hidden="true">＋</span>上报故障
      </el-button>
    </header>

    <div class="scope-note"><span class="scope-dot" aria-hidden="true" />{{ scope }}</div>

    <section class="orders-panel" aria-label="工单列表">
      <div class="status-filters" aria-label="按工单状态筛选">
        <button type="button" :class="{ selected: status === '' }" :aria-pressed="status === ''" @click="chooseStatus('')">全部工单</button>
        <button v-for="option in statusOptions" :key="option.value" type="button" :class="{ selected: status === option.value }" :aria-pressed="status === option.value" @click="chooseStatus(option.value)">
          <span :class="['status-dot', option.value]" aria-hidden="true" />{{ option.label }}
        </button>
      </div>

      <div class="list-toolbar">
        <div class="query-controls">
          <el-select v-model="category" clearable aria-label="故障分类" placeholder="全部故障分类" @change="filterChanged">
            <el-option v-for="option in categoryOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-select v-model="roomId" clearable filterable aria-label="筛选教室" placeholder="全部可见教室" :loading="roomLoading" @change="filterChanged">
            <el-option v-for="room in roomOptions" :key="room.id" :label="`${room.code} · ${room.name}${room.is_active ? '' : '（已停用）'}`" :value="room.id" />
          </el-select>
          <el-button v-if="hasFilters" text @click="resetFilters">清除筛选</el-button>
        </div>
        <div class="toolbar-actions">
          <el-button v-if="isPropertyAdmin" :loading="scanning" @click="scanOverdue">补扫超时工单</el-button>
          <el-button :loading="loading" @click="refresh">刷新列表</el-button>
        </div>
      </div>
      <div v-if="roomError" class="option-warning" role="status">教室选项暂时无法加载，仍可查看工单。<el-button link type="primary" @click="loadRooms">重试</el-button></div>
      <el-alert v-if="scanNotice" :title="scanNotice" :type="scanError ? 'warning' : 'success'" :closable="false" show-icon class="scan-notice" role="status" />

      <div class="result-caption"><span>{{ loading ? '正在读取工单…' : loadError ? '列表加载失败' : `共 ${total} 条工单` }}</span><span>时间按北京时间显示</span></div>

      <div v-if="loadError" class="list-error">
        <el-result icon="warning" title="暂时无法加载工单" :sub-title="loadError"><template #extra><el-button type="primary" @click="refresh">重新加载</el-button></template></el-result>
      </div>
      <div v-else v-loading="loading" class="results-area" :aria-busy="loading">
        <el-table v-if="items.length" :data="items" class="desktop-orders" row-key="id" :border="false">
          <el-table-column label="工单 / 故障内容" min-width="270">
            <template #default="{ row }">
              <span class="order-number">#{{ row.id }}</span>
              <button class="order-title" type="button" @click="openOrder(row.id)">{{ row.title }}</button>
              <p class="order-excerpt">{{ row.description }}</p>
            </template>
          </el-table-column>
          <el-table-column label="教室" min-width="180"><template #default="{ row }"><span class="room-cell">{{ roomNames.get(row.room_id) ?? `教室 #${row.room_id}` }}</span></template></el-table-column>
          <el-table-column label="分类" min-width="100"><template #default="{ row }">{{ categoryLabels[row.category as WorkOrderCategory] }}</template></el-table-column>
          <el-table-column label="当前状态" min-width="108"><template #default="{ row }"><el-tag :type="statusTagType(row.status)" effect="light" round>{{ statusLabels[row.status as WorkOrderStatus] }}</el-tag></template></el-table-column>
          <el-table-column label="超时升级" min-width="98"><template #default="{ row }"><el-tag v-if="row.escalation_count > 0" type="warning" effect="plain">{{ row.escalation_count }} 次</el-tag><span v-else class="no-escalation">暂无</span></template></el-table-column>
          <el-table-column label="上报时间" min-width="165"><template #default="{ row }"><time class="time-cell" :datetime="row.created_at">{{ formatTime(row.created_at) }}</time></template></el-table-column>
          <el-table-column label="操作" width="82" align="right"><template #default="{ row }"><el-button link type="primary" @click="openOrder(row.id)">查看</el-button></template></el-table-column>
        </el-table>

        <div v-if="items.length" class="mobile-orders">
          <article v-for="order in items" :key="order.id" class="mobile-order">
            <div class="mobile-order-top"><span class="order-number">#{{ order.id }} · {{ categoryLabels[order.category] }}</span><el-tag :type="statusTagType(order.status)" round>{{ statusLabels[order.status] }}</el-tag></div>
            <button class="order-title" type="button" @click="openOrder(order.id)">{{ order.title }}</button>
            <p class="order-excerpt">{{ order.description }}</p>
            <p class="room-cell">{{ roomNames.get(order.room_id) ?? `教室 #${order.room_id}` }}</p>
            <p class="mobile-escalation">超时升级：<span :class="{ 'has-escalation': order.escalation_count > 0 }">{{ order.escalation_count > 0 ? `${order.escalation_count} 次` : '暂无' }}</span></p>
            <div class="mobile-order-bottom"><time class="time-cell" :datetime="order.created_at">{{ formatTime(order.created_at) }}</time><el-button link type="primary" @click="openOrder(order.id)">查看详情</el-button></div>
          </article>
        </div>

        <el-empty v-if="!loading && !items.length" :description="emptyText" :image-size="100">
          <el-button v-if="hasFilters" @click="resetFilters">清除筛选</el-button>
          <el-button v-else-if="canCreate(auth.user)" type="primary" @click="showCreate = true">上报第一条故障</el-button>
          <p v-else class="empty-hint">有新的工单后，点击“刷新列表”查看。</p>
        </el-empty>
      </div>

      <div v-if="!loadError && total > 0" class="list-pagination">
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10, 20, 50]" layout="prev, pager, next, sizes" :pager-count="5" :disabled="loading" @current-change="refresh" @size-change="filterChanged" />
      </div>
    </section>
    <div v-if="timeoutPolicy" class="timeout-policy">
      <p>处理时限：待派单 {{ timeoutPolicy.reported_hours }} 小时 · 已派单 {{ timeoutPolicy.assigned_hours }} 小时 · 维修中 {{ timeoutPolicy.repairing_hours }} 小时 · 待验收 {{ timeoutPolicy.acceptance_hours }} 小时。</p>
      <p>{{ timeoutPolicy.automatic_scan_enabled ? `系统每 ${timeoutPolicy.scan_interval_seconds} 秒扫描超时工单。` : '当前未启用自动扫描，物业管理员可手动补扫。' }}每个阶段及返修轮次只升级一次，升级后状态保持不变。</p>
    </div>
    <p v-else-if="timeoutPolicyError" class="page-footnote">暂时无法读取处理时限。<el-button link type="primary" @click="loadTimeoutPolicy">重试读取</el-button></p>
    <p class="page-footnote">维修中及待验收期间，教室暂停接受新的预约；已有预约保留。</p>
    <CreateWorkOrderDialog v-model="showCreate" :rooms="roomOptions" :rooms-loading="roomLoading" :rooms-error="roomError" @reload-rooms="loadRooms" @created="created" />
  </section>
</template>

<style scoped>
.work-orders-page { color: #173b56; }
.page-heading { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 24px; }
.section-kicker { color: #64748b; font-size: 12px; margin: 0 0 9px; letter-spacing: .04em; }
.page-heading h1 { font-size: clamp(26px, 3vw, 34px); letter-spacing: -.04em; margin: 0 0 10px; font-weight: 650; }
.page-description { margin: 0; color: #64748b; font-size: 14px; line-height: 1.7; }
.button-plus { font-size: 20px; line-height: 1; margin-right: 5px; }
.scope-note { display: flex; align-items: center; gap: 9px; font-size: 12px; color: #526c81; margin: 0 0 16px; line-height: 1.7; }
.scope-dot { flex: 0 0 6px; height: 6px; border-radius: 50%; background: #5d879f; }
.orders-panel { background: #fff; border: 1px solid #e0e7ef; border-radius: 12px; overflow: hidden; box-shadow: 0 3px 16px #173b5604; }
.toolbar-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.toolbar-actions .el-button { margin: 0; }
.scan-notice { margin: 0 22px 16px; width: auto; }
.no-escalation, .mobile-escalation { color: #8191a2; font-size: 12px; }
.mobile-escalation { margin: 8px 0 0; }
.has-escalation { color: #b77925; font-weight: 600; }
.timeout-policy { margin-top: 18px; color: #7b8da1; font-size: 12px; line-height: 1.9; }
.timeout-policy p { margin: 3px 0; }
.status-filters { display: flex; gap: 7px; padding: 16px 22px; border-bottom: 1px solid #edf1f5; overflow-x: auto; }
.status-filters button { display: flex; align-items: center; gap: 7px; border: 0; border-radius: 6px; background: transparent; padding: 10px 13px; font: inherit; font-size: 13px; white-space: nowrap; color: #64748b; cursor: pointer; }
.status-filters button:hover { background: #f5f8fc; color: #173b56; }
.status-filters button.selected { background: #eaf1ff; color: #2563eb; font-weight: 600; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: #94a3b8; }
.ASSIGNED { background: #5082d5; }.REPAIRING { background: #d78a30; }.WAIT_ACCEPTANCE { background: #a87b29; }.COMPLETED { background: #38977d; }
.list-toolbar { display: flex; gap: 16px; justify-content: space-between; align-items: center; padding: 22px 24px 16px; }
.query-controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.query-controls :deep(.el-select) { width: 180px; }.query-controls :deep(.el-select:nth-child(2)) { width: 250px; }
.option-warning { padding: 0 24px 12px; color: #a56d1e; font-size: 12px; }
.result-caption { display: flex; justify-content: space-between; gap: 15px; color: #8391a0; font-size: 12px; padding: 0 24px 12px; }
.results-area { min-height: 280px; }
.desktop-orders :deep(.el-table__cell) { padding: 19px 12px; text-align: left; }
.desktop-orders :deep(.cell) { padding: 0; }
.desktop-orders :deep(th.el-table__cell) { padding-top: 12px; padding-bottom: 12px; background: #f8fafc; font-size: 12px; color: #7a8999; font-weight: 500; }
.desktop-orders :deep(.el-table__cell:first-child) { padding-left: 24px; }
.desktop-orders :deep(.el-table__cell:last-child) { padding-right: 24px; }
.order-number { display: block; font: 11px/1.5 Consolas, monospace; color: #8997a5; margin-bottom: 5px; }
.order-title { display: block; border: 0; padding: 0; background: transparent; font: inherit; font-size: 14px; color: #173b56; text-align: left; font-weight: 600; cursor: pointer; overflow-wrap: anywhere; }
.order-title:hover { color: #2563eb; }
.order-excerpt { margin: 5px 0 0; color: #8190a0; font-size: 12px; line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; white-space: normal; overflow-wrap: anywhere; }
.room-cell { color: #526579; font-size: 12px; white-space: normal; overflow-wrap: anywhere; }
.time-cell { color: #8391a0; font: 11px/1.6 Consolas, monospace; }
.list-pagination { padding: 18px 24px; display: flex; justify-content: flex-end; border-top: 1px solid #edf1f5; }
.page-footnote { margin: 18px 2px; font-size: 12px; color: #8391a0; line-height: 1.7; }
.empty-hint { font-size: 12px; color: #8391a0; }.mobile-orders { display: none; }
button:focus-visible { outline: 2px solid #2563eb; outline-offset: 3px; }
@media (max-width: 720px) {
  .page-heading { align-items: flex-start; gap: 14px; }.page-heading :deep(.el-button--large) { padding: 10px 13px; }
  .status-filters { padding: 12px; gap: 3px; }.status-filters button { padding: 9px 10px; }
  .list-toolbar { padding: 16px 16px 13px; align-items: flex-start; flex-direction: column; gap: 10px; }.query-controls { width: 100%; gap: 8px; }
  .toolbar-actions { width: 100%; justify-content: space-between; }.scan-notice { margin-left: 16px; margin-right: 16px; }
  .query-controls :deep(.el-select), .query-controls :deep(.el-select:nth-child(2)) { width: 100%; }
  .result-caption { padding: 0 16px 12px; }.desktop-orders { display: none; }.mobile-orders { display: block; }
  .mobile-order { padding: 18px 16px; border-top: 1px solid #edf1f5; }.mobile-order-top, .mobile-order-bottom { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  .mobile-order-top { margin-bottom: 10px; }.mobile-order-top .order-number { margin: 0; }.mobile-order .order-excerpt { -webkit-line-clamp: 2; }.mobile-order .room-cell { margin: 12px 0 8px; }
  .list-pagination { padding: 16px 12px; justify-content: center; }.list-pagination :deep(.el-pagination__sizes) { display: none; }
  .result-caption span:last-child { font-size: 10px; }
}
</style>
