<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '../api/m2'
import { rooms, type Room } from '../api/resources'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const canCreate = computed(() => auth.user?.identity_type === 'TEACHER' && auth.user.roles.includes('USER'))
const canReview = computed(() => !!auth.user?.roles.includes('ROOM_ADMIN'))
const items = ref<api.Application[]>([])
const roomOptions = ref<Room[]>([])
const roomsTruncated = ref(false)
const loading = ref(false)
const busy = ref(false)
const loadError = ref('')
const total = ref(0)
const page = ref(1)
const status = ref<api.ApplicationStatus | ''>(canReview.value ? 'PENDING' : '')
const mine = ref(!canReview.value)
const createOpen = ref(false)
const detail = ref<api.ApplicationDetail | null>(null)
const detailOpen = ref(false)
const reviewing = ref<api.Application | null>(null)
const reviewOpen = ref(false)
const reviewForm = reactive({ passed: true, comment: '' })
const labels: Record<string, string> = { PENDING: '待审批', APPROVED: '已通过', REJECTED: '已驳回', CANCELLED: '已撤销', ENDED: '已结束', ACTIVE: '生效', RELEASED: '已释放' }
const actions: Record<string, string> = { CREATE_APPLICATION: '提交申请', REVIEW_APPLICATION: '审批申请', CANCEL_APPLICATION: '撤销申请', ACQUIRE_RESOURCE: '取得整室占用' }
const dateParts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
const part = (name: string) => dateParts.find(p => p.type === name)?.value
const today = `${part('year')}-${part('month')}-${part('day')}`
const tomorrow = new Date(Date.parse(`${today}T00:00:00Z`) + 86400000).toISOString().slice(0, 10)
const form = reactive({ room_id: '', title: '', purpose: '', participant_count: 1, date: tomorrow, start: '09', end: '10' })
const selectedRoom = computed(() => roomOptions.value.find(room => room.id === form.room_id))
const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'))
let requestSequence = 0
let lastPayload = ''
let requestKey = ''

function formatTime(value: string | null) {
  return value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23' }).format(new Date(value)) : '—'
}
function roomLabel(id: string) {
  const room = roomOptions.value.find(item => item.id === id)
  return room ? `${room.name} · ${room.code}` : `教室 #${id}`
}
async function load() {
  const sequence = ++requestSequence
  loading.value = true
  loadError.value = ''
  try {
    const result = await api.applications({ page: page.value, page_size: 10, status: status.value || undefined, mine: mine.value })
    if (sequence === requestSequence) { items.value = result.items; total.value = result.total }
  } catch (error) {
    if (sequence === requestSequence) { items.value = []; total.value = 0; loadError.value = errorMessage(error) }
  } finally { if (sequence === requestSequence) loading.value = false }
}
function filterChanged() { page.value = 1; void load() }
async function loadRooms() {
  const result = await rooms()
  roomOptions.value = result.items
  roomsTruncated.value = result.total > result.items.length
}
async function openCreate() {
  try {
    await loadRooms()
    Object.assign(form, { room_id: roomOptions.value.find(room => room.is_active && (!room.department_code || room.department_code === auth.user?.department_code))?.id ?? '', title: '', purpose: '', participant_count: 1, date: tomorrow, start: '09', end: '10' })
    lastPayload = ''; requestKey = ''; createOpen.value = true
  } catch (error) { ElMessage.error(errorMessage(error)) }
}
async function submit() {
  if (busy.value) return
  if (!form.room_id || Number(form.end) <= Number(form.start)) { ElMessage.warning('请选择教室和有效的同日时段'); return }
  const body = { room_id: form.room_id, title: form.title.trim(), purpose: form.purpose.trim(), participant_count: form.participant_count,
    start_at: `${form.date}T${form.start}:00:00+08:00`, end_at: `${form.date}T${form.end}:00:00+08:00` }
  if (!body.title || !body.purpose) { ElMessage.warning('请填写申请名称和用途'); return }
  const payload = JSON.stringify(body)
  if (payload !== lastPayload) { requestKey = crypto.randomUUID(); lastPayload = payload }
  busy.value = true
  try {
    await api.createApplication({ ...body, request_key: requestKey })
    createOpen.value = false; ElMessage.success('申请已提交，待审批时不占用教室')
    status.value = ''; mine.value = true; page.value = 1; await load()
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { busy.value = false }
}
async function showDetail(row: api.Application) {
  try { detail.value = await api.application(row.id); detailOpen.value = true }
  catch (error) { ElMessage.error(errorMessage(error)) }
}
function openReview(row: api.Application) {
  reviewing.value = row; reviewForm.passed = true; reviewForm.comment = ''; reviewOpen.value = true
}
async function submitReview() {
  if (!reviewing.value || busy.value) return
  if (!reviewForm.passed && !reviewForm.comment.trim()) { ElMessage.warning('驳回时必须填写意见'); return }
  busy.value = true
  try {
    await api.reviewApplication(reviewing.value.id, reviewForm.passed, reviewForm.comment.trim())
    reviewOpen.value = false; ElMessage.success(reviewForm.passed ? '已通过并取得整室占用' : '已驳回申请'); await load()
  } catch (error) { ElMessage.error(errorMessage(error)); await load() }
  finally { busy.value = false }
}
async function cancel(row: api.Application) {
  if (busy.value) return
  try { await ElMessageBox.confirm(`撤销「${row.title}」？`, '撤销待审批申请', { confirmButtonText: '确认撤销', cancelButtonText: '保留申请' }) }
  catch { return }
  busy.value = true
  try { await api.cancelApplication(row.id); ElMessage.success('申请已撤销'); await load() }
  catch (error) { ElMessage.error(errorMessage(error)); await load() }
  finally { busy.value = false }
}
onMounted(async () => {
  void load()
  try { await loadRooms() } catch (error) { ElMessage.error(errorMessage(error)) }
})
</script>

<template>
  <section>
    <div class="eyebrow">ROOM APPLICATIONS · M2</div>
    <div class="heading-row"><h1>{{ canReview ? '教室申请与审批' : '我的教室申请' }}</h1><el-button v-if="canCreate" type="primary" @click="openCreate">新建申请</el-button></div>
    <p class="muted">教师提交整教室申请，由教室管理员单级审批。待审批不预占教室，仅待审批申请可由本人撤销。</p>
    <el-alert v-if="!canCreate && !canReview" title="本轮仅 USER 角色的教师可新建申请；此处仍可查看本人的历史申请。" type="info" :closable="false" />
    <div class="filters">
      <label>状态<select v-model="status" @change="filterChanged"><option value="">全部状态</option><option v-for="key in ['PENDING', 'APPROVED', 'REJECTED', 'CANCELLED'] as const" :key="key" :value="key">{{ labels[key] }}</option></select></label>
      <label v-if="canReview">查看范围<select v-model="mine" @change="filterChanged"><option :value="false">全部测试教室申请</option><option :value="true">仅我的申请</option></select></label>
      <el-button :loading="loading" @click="load">刷新申请</el-button>
    </div>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" />
    <div v-loading="loading" class="table-wrap">
      <table v-if="items.length"><thead><tr><th>申请</th><th>教室 / 人数</th><th>使用时间（北京时间）</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="row in items" :key="row.id">
          <td class="title-cell"><strong>{{ row.title }}</strong><div class="muted">#{{ row.id }} · 申请人 #{{ row.applicant_id }}</div></td>
          <td>{{ roomLabel(row.room_id) }}<div class="muted">{{ row.participant_count }} 人</div></td>
          <td>{{ formatTime(row.start_at) }}<br />至 {{ formatTime(row.end_at) }}</td>
          <td><span :class="['state', row.display_status]">{{ labels[row.display_status] }}</span></td>
          <td><el-button size="small" @click="showDetail(row)">详情</el-button>
            <el-button v-if="canReview && row.status === 'PENDING' && row.applicant_id !== auth.user?.id" size="small" type="primary" :disabled="busy" @click="openReview(row)">审批</el-button>
            <el-button v-if="row.status === 'PENDING' && row.applicant_id === auth.user?.id" size="small" :disabled="busy" @click="cancel(row)">撤销</el-button>
          </td>
        </tr></tbody>
      </table>
      <el-empty v-else-if="!loading && !loadError" description="暂无符合条件的申请" />
    </div>
    <el-pagination v-model:current-page="page" :page-size="10" :total="total" layout="total, prev, pager, next" class="pagination" @current-change="load" />

    <el-dialog v-model="createOpen" title="新建教室申请" width="min(640px, 94vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <form @submit.prevent="submit">
        <label>教室<select v-model="form.room_id" required><option value="" disabled>请选择教室</option><option v-for="room in roomOptions.filter(r => r.is_active && (!r.department_code || r.department_code === auth.user?.department_code))" :key="room.id" :value="room.id">{{ room.name }} · {{ room.code }}（容量 {{ room.capacity }} 人）</option></select></label>
        <p v-if="roomsTruncated" class="muted">当前选项仅显示前 100 间可见教室。</p>
        <p v-if="selectedRoom" class="muted">开放时间 {{ selectedRoom.open_time.slice(0, 5) }}–{{ selectedRoom.close_time.slice(0, 5) }}；申请时间须为未来的同日整点，遵循公共预约窗口（默认 7 天）。</p>
        <label>申请名称<input v-model="form.title" required maxlength="100" /></label>
        <label>用途说明<textarea v-model="form.purpose" required maxlength="4000" rows="3" /></label>
        <div class="form-grid"><label>参与人数<input v-model.number="form.participant_count" type="number" min="1" :max="selectedRoom?.capacity" step="1" required /></label><label>日期（北京时间）<input v-model="form.date" type="date" :min="today" required /></label></div>
        <div class="form-grid"><label>开始时间<select v-model="form.start"><option v-for="hour in hours" :key="hour" :value="hour">{{ hour }}:00</option></select></label><label>结束时间<select v-model="form.end"><option v-for="hour in hours" :key="hour" :value="hour">{{ hour }}:00</option></select></label></div>
        <el-button type="primary" native-type="submit" :loading="busy">提交申请</el-button>
      </form>
    </el-dialog>

    <el-dialog v-model="reviewOpen" title="审批教室申请" width="min(560px, 94vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <template v-if="reviewing"><p><strong>{{ reviewing.title }}</strong> · {{ roomLabel(reviewing.room_id) }} · {{ reviewing.participant_count }} 人</p><p>{{ formatTime(reviewing.start_at) }} 至 {{ formatTime(reviewing.end_at) }}</p><p class="purpose">{{ reviewing.purpose }}</p></template>
      <form @submit.prevent="submitReview"><label>审批决定<select v-model="reviewForm.passed"><option :value="true">通过申请</option><option :value="false">驳回申请</option></select></label>
        <label>审批意见{{ reviewForm.passed ? '（选填）' : '（必填）' }}<textarea v-model="reviewForm.comment" :required="!reviewForm.passed" maxlength="500" rows="3" /></label>
        <p class="muted">通过时会重新检查资源。发生冲突时申请仍保持待审批，可填写理由后明确驳回。</p>
        <el-button type="primary" native-type="submit" :loading="busy">确认审批</el-button>
      </form>
    </el-dialog>

    <el-dialog v-model="detailOpen" title="申请详情" width="min(660px, 94vw)">
      <template v-if="detail"><h2>{{ detail.title }}</h2><p>{{ roomLabel(detail.room_id) }} · {{ detail.participant_count }} 人 · {{ labels[detail.display_status] }}</p><p>{{ formatTime(detail.start_at) }} 至 {{ formatTime(detail.end_at) }}（北京时间）</p><p class="purpose">{{ detail.purpose }}</p><p>审批意见：{{ detail.review_comment || '—' }}</p><h3>操作记录</h3>
        <ol class="events"><li v-for="event in detail.events" :key="event.id"><strong>{{ actions[event.action] || event.action }}</strong> · {{ event.to_status ? (labels[event.to_status] || event.to_status) : '' }}<div class="muted">{{ formatTime(event.created_at) }} · 操作者 #{{ event.actor_id }}</div><p v-if="event.note" class="purpose">{{ event.note }}</p></li></ol>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.heading-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
select, textarea { font: inherit; color: inherit; padding: 10px; border: 1px solid #cbd5e1; border-radius: 4px; background: white; max-width: 100%; }
select:focus, textarea:focus { outline: 2px solid #93c5fd; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-grid input { width: 100%; min-width: 0; }
.pagination { margin-top: 20px; }
.title-cell { white-space: normal; min-width: 160px; max-width: 320px; overflow-wrap: anywhere; }
.purpose { white-space: pre-wrap; overflow-wrap: anywhere; }
.events { padding-left: 20px; }
.events li { margin: 18px 0; }
.PENDING { background: #fef3c7; color: #92400e; }
.APPROVED { background: #dcfce7; color: #166534; }
.REJECTED { background: #fee2e2; color: #991b1b; }
@media (max-width: 600px) { .form-grid { grid-template-columns: 1fr; } }
</style>
