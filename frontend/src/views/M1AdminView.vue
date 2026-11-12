<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as m1 from '../api/m1'
import { rooms, type Room } from '../api/resources'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const canReadStatistics = computed(() => auth.user?.permissions.includes('m1:statistics:read'))
const canReadReservations = computed(() => auth.user?.permissions.includes('m1:reservation:read_scoped'))
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const startDay = ref(today)
const endDay = ref(today)
const roomId = ref('')
const roomOptions = ref<Room[]>([])
const stats = ref<m1.M1Statistics | null>(null)
const reservations = ref<m1.Page<m1.Reservation> | null>(null)
const settings = ref<m1.M1Settings | null>(null)
const maxHours = ref(4)
const checkinRoom = ref('')
const checkinCode = ref<m1.CheckinCode | null>(null)
const busy = ref(false)
const page = ref(1)
const statusLabels: Record<string, string> = { RESERVED: '待签到', CHECKED_IN: '使用中', CANCELLED: '已取消', NO_SHOW: '未签到违约', COMPLETED: '已完成', INTERRUPTED: '已中断' }
const rateText = computed(() => stats.value?.fulfillment_rate == null ? '—' : `${(stats.value.fulfillment_rate * 100).toFixed(1)}%`)
function range() {
  return {
    start: `${startDay.value}T00:00:00+08:00`,
    end: `${endDay.value}T23:59:59.999+08:00`,
  }
}
async function loadSettings() {
  try { settings.value = await m1.settings(); maxHours.value = settings.value.max_hours }
  catch (error) { ElMessage.error(errorMessage(error)) }
}
async function loadRecords(nextPage = page.value) {
  try { reservations.value = await m1.adminReservations(nextPage, roomId.value || undefined); page.value = nextPage }
  catch (error) { ElMessage.error(errorMessage(error)) }
}
async function loadStatistics() {
  if (!startDay.value || !endDay.value || startDay.value > endDay.value) { ElMessage.error('请选择有效的统计日期范围'); return }
  try {
    const { start, end } = range()
    stats.value = await m1.statistics(start, end, roomId.value || undefined)
  } catch (error) { ElMessage.error(errorMessage(error)) }
}
async function loadData() {
  busy.value = true
  try {
    await Promise.all([
      canReadStatistics.value ? loadStatistics() : Promise.resolve(),
      canReadReservations.value ? loadRecords(1) : Promise.resolve(),
    ])
  } finally { busy.value = false }
}
async function saveSettings() {
  if (!settings.value) return
  busy.value = true
  try {
    settings.value = await m1.updateSettings(maxHours.value, settings.value.version)
    maxHours.value = settings.value.max_hours
    ElMessage.success('预约参数已更新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
    await loadSettings()
  } finally { busy.value = false }
}
async function readCode(rotate = false) {
  if (!checkinRoom.value) { ElMessage.error('请先选择教室'); return }
  try {
    checkinCode.value = await m1.checkinCode(checkinRoom.value, rotate)
    ElMessage.success(rotate ? '签到码已轮换' : '已读取当前签到码')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}
function showTime(value: string) { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) }
onMounted(async () => {
  const [availableRooms] = await Promise.all([rooms(1, 100).catch(() => ({ items: [] as Room[], total: 0 })), auth.user?.permissions.includes('m1:settings:write') ? loadSettings() : Promise.resolve()])
  roomOptions.value = availableRooms.items
  await loadData()
})
</script>

<template>
  <section v-loading="busy">
    <div class="eyebrow">M1 ADMINISTRATION</div>
    <h1>自习预约管理</h1>
    <p class="muted">统计范围受当前管理员的教室授权约束；时段按北京时间解释。</p>

    <el-card>
      <template #header><strong>{{ canReadStatistics ? '预约统计' : '预约查询' }}</strong></template>
      <div class="filters">
        <label v-if="canReadStatistics">开始日期 <input v-model="startDay" type="date" /></label>
        <label v-if="canReadStatistics">结束日期 <input v-model="endDay" type="date" /></label>
        <label>教室
          <select v-model="roomId"><option value="">授权范围内全部</option><option v-for="room in roomOptions" :key="room.id" :value="room.id">{{ room.building_name }} · {{ room.name }}</option></select>
        </label>
        <el-button type="primary" :loading="busy" @click="loadData">查询</el-button>
      </div>
      <div v-if="stats" class="stat-grid">
        <article><span>预约数</span><strong>{{ stats.reservation_count }}</strong></article>
        <article><span>已签到</span><strong>{{ stats.checked_in_count }}</strong></article>
        <article><span>已完成</span><strong>{{ stats.completed_count }}</strong></article>
        <article><span>未签到违约</span><strong>{{ stats.no_show_count }}</strong></article>
        <article><span>取消 / 中断</span><strong>{{ stats.cancelled_count }} / {{ stats.interrupted_count }}</strong></article>
        <article><span>计划 / 实际分钟</span><strong>{{ Math.round(stats.planned_minutes) }} / {{ Math.round(stats.actual_minutes) }}</strong></article>
        <article><span>使用兑现率</span><strong>{{ rateText }}</strong></article>
      </div>
    </el-card>

    <el-card v-if="settings" class="settings-card">
      <template #header><strong>预约参数</strong></template>
      <p class="muted">只影响新预约；已有预约保留创建时的参数快照。当前版本：{{ settings.version }}</p>
      <div class="filters"><label>单次最长时长（小时） <el-input-number v-model="maxHours" :min="1" :max="14" /></label><el-button :disabled="maxHours === settings.max_hours" @click="saveSettings">保存参数</el-button></div>
    </el-card>

    <el-card v-if="auth.user?.permissions.includes('m1:checkin_code:read')" class="settings-card">
      <template #header><strong>教室签到码</strong></template>
      <p class="muted">读取和轮换均会记录操作；签到码仅展示当日有效值。</p>
      <div class="filters">
        <label>教室 <select v-model="checkinRoom" aria-label="签到码教室"><option value="">选择教室</option><option v-for="room in roomOptions" :key="room.id" :value="room.id">{{ room.building_name }} · {{ room.name }}</option></select></label>
        <el-button @click="readCode()">读取签到码</el-button>
        <el-button v-if="auth.user.permissions.includes('m1:checkin_code:rotate')" type="warning" @click="readCode(true)">轮换签到码</el-button>
      </div>
      <el-alert v-if="checkinCode" class="code-result" type="success" :closable="false" :title="`签到码：${checkinCode.code} · 有效至 ${showTime(checkinCode.expires_at)}`" />
    </el-card>

    <h2 v-if="canReadReservations">授权范围内的预约</h2>
    <el-table v-if="reservations" :data="reservations.items" stripe>
      <el-table-column prop="id" label="预约编号" min-width="110" />
      <el-table-column label="时段" min-width="240"><template #default="scope">{{ showTime(scope.row.start_at) }} — {{ showTime(scope.row.end_at) }}</template></el-table-column>
      <el-table-column prop="resource_snapshot.room_name" label="教室" />
      <el-table-column prop="resource_snapshot.seat_no" label="座位" />
      <el-table-column label="状态"><template #default="scope">{{ statusLabels[scope.row.status] || scope.row.status }}</template></el-table-column>
    </el-table>
    <el-empty v-if="reservations && !reservations.items.length" description="当前范围没有预约" />
    <el-pagination v-if="reservations && reservations.total > 20" v-model:current-page="page" :page-size="20" :total="reservations.total" layout="prev, pager, next" @current-change="loadRecords" />
  </section>
</template>

<style scoped>
.settings-card { margin-top: 22px; }
.code-result { margin-top: 16px; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 12px; margin-top: 20px; }
.stat-grid article { display: grid; gap: 8px; padding: 15px; border: 1px solid #e3e9f0; border-radius: 9px; }
.stat-grid span { color: #6d8094; font-size: 13px; }
.stat-grid strong { font-size: 21px; font-variant-numeric: tabular-nums; }
input[type='date'], select { padding: 8px; border: 1px solid #d5dce5; border-radius: 6px; }
</style>
