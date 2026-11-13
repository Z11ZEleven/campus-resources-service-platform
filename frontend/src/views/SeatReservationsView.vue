<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'
import * as api from '../api/m1'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'
import { rooms, type Room } from '../api/resources'

const auth = useAuthStore(), route = useRoute(), router = useRouter()
const day = ref(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()))
const startHour = ref(9), endHour = ref(10), fixed = ref(false), mobile = ref(false)
const searchPage = ref(1), listPage = ref(1), violationPage = ref(1)
const searchResult = ref<api.Page<api.Seat> | null>(null), list = ref<api.Page<api.Reservation> | null>(null)
const violations = ref<api.Page<api.Violation> | null>(null), selected = ref<api.Reservation | null>(null)
const roomOptions = ref<Room[]>([])
const reuseSeat = ref(''), reuseRoom = ref(''), code = ref(''), busy = ref(false), searching = ref(false)
const detailOpen = ref(false), detailLoading = ref(false)
let fingerprint = '', requestKey = ''
const statusLabels: Record<string, string> = { RESERVED: '待签到', CHECKED_IN: '使用中', CANCELLED: '已取消', NO_SHOW: '未签到违约', COMPLETED: '已完成', INTERRUPTED: '已中断' }
const range = computed(() => ({ start_at: `${day.value}T${String(startHour.value).padStart(2, '0')}:00:00+08:00`, end_at: `${day.value}T${String(endHour.value).padStart(2, '0')}:00:00+08:00` }))
const showTime = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
const statusTagType = (value: string): 'success' | 'warning' | 'info' | 'danger' | 'primary' => ({ RESERVED: 'warning', CHECKED_IN: 'success', CANCELLED: 'info', NO_SHOW: 'danger', COMPLETED: 'info', INTERRUPTED: 'danger' }[value] as 'success' | 'warning' | 'info' | 'danger' | undefined) || 'primary'
const socketLabel = (seat: api.Seat) => [seat.has_fixed_socket && '固定插座', seat.has_mobile_socket && '移动插座'].filter(Boolean).join('、') || '无插座'
async function search(page = 1) {
  if (!auth.user) return
  searching.value = true; searchResult.value = null
  try {
    const params: Record<string, string | number | boolean> = { identity_id: auth.user.identity_id, ...range.value, page, page_size: 20 }
    if (reuseRoom.value) params.room_id = reuseRoom.value
    if (fixed.value) params.has_fixed_socket = true
    if (mobile.value) params.has_mobile_socket = true
    searchResult.value = await api.search(params); searchPage.value = page
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { searching.value = false }
}
async function loadList(page = listPage.value) {
  try { list.value = await api.reservations(page); listPage.value = page } catch (error) { ElMessage.error(errorMessage(error)) }
}
async function loadViolations(page = violationPage.value) {
  try { violations.value = await api.violations(page); violationPage.value = page } catch (error) { ElMessage.error(errorMessage(error)) }
}
async function loadDetail() {
  const id = route.params.id; selected.value = null
  if (typeof id !== 'string' || !/^[1-9]\d*$/.test(id)) { detailOpen.value = false; return }
  detailOpen.value = true; detailLoading.value = true
  try { selected.value = await api.detail(id) } catch (error) { ElMessage.error(errorMessage(error)); detailOpen.value = false }
  finally { detailLoading.value = false }
}
async function book(seat: api.Seat) {
  if (!auth.user || busy.value) return
  busy.value = true
  try { await ElMessageBox.confirm(`预约 ${seat.building_name} ${seat.room_name} ${seat.seat_no}，${day.value} ${startHour.value}:00–${endHour.value}:00？`, '确认预约') } catch { busy.value = false; return }
  const body = { identity_id: auth.user.identity_id, seat_id: seat.seat_id, ...range.value }
  const current = JSON.stringify(body)
  if (current !== fingerprint) { fingerprint = current; requestKey = crypto.randomUUID() }
  busy.value = true
  try {
    const result = await api.create(body, requestKey)
    fingerprint = ''; requestKey = ''; reuseSeat.value = ''; reuseRoom.value = ''
    ElMessage.success('预约成功'); await loadList(1); await router.push(`/m1/reservations/${result.id}`); await search(searchPage.value)
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 409) { fingerprint = ''; requestKey = '' }
    ElMessage.error(errorMessage(error)); await search(searchPage.value)
  }
  finally { busy.value = false }
}
async function cancel(row: api.Reservation) {
  try { await ElMessageBox.confirm('确认取消此预约？', '取消预约') } catch { return }
  busy.value = true
  try { selected.value = await api.cancel(row.id); ElMessage.success('预约已取消'); await loadList() }
  catch (error) { ElMessage.error(errorMessage(error)); await loadDetail() } finally { busy.value = false }
}
async function checkIn(row: api.Reservation) {
  if (!/^\d{8}$/.test(code.value)) { ElMessage.error('请输入八位签到码'); return }
  busy.value = true
  try { selected.value = await api.checkIn(row.id, code.value); code.value = ''; ElMessage.success('签到成功'); await loadList() }
  catch (error) { ElMessage.error(errorMessage(error)); await loadDetail() } finally { busy.value = false }
}
function reuse(row: api.Reservation) { reuseSeat.value = row.seat_id; reuseRoom.value = row.room_id; searchResult.value = null; window.scrollTo({ top: 0, behavior: 'smooth' }) }
function openDetail(id: string) { void router.push(`/m1/reservations/${id}`) }
function closeDetail() { if (typeof route.params.id === 'string') void router.push('/m1/reservations') }
watch(() => route.params.id, loadDetail)
onMounted(async () => {
  await Promise.all([loadList(), loadViolations(), loadDetail(), rooms().then(result => { roomOptions.value = result.items }).catch(error => ElMessage.error(errorMessage(error)))])
})
</script>
<template>
  <section class="seat-page">
    <header class="page-heading">
      <div><p class="section-kicker">校园空间 / 自习预约</p><h1>自习座位预约</h1><p class="page-description">选择日期、时段和座位条件，系统将在提交时再次确认可用性。</p></div>
    </header>

    <section class="seat-panel" aria-label="查找座位">
      <div class="panel-heading"><div><h2>查找可用座位</h2><p>所有时间均按北京时间显示</p></div></div>
      <el-alert v-if="reuseSeat" title="已带入历史座位所在教室，请选择新时段后重新搜索。" type="info" show-icon closable @close="reuseSeat = ''; reuseRoom = ''" />
      <div class="query-controls">
        <el-select v-model="reuseRoom" clearable filterable placeholder="全部教室" aria-label="教室">
          <el-option v-for="room in roomOptions" :key="room.id" :label="`${room.building_name} · ${room.name}`" :value="room.id" />
        </el-select>
        <el-date-picker v-model="day" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" placeholder="选择日期" aria-label="日期" />
        <el-select v-model="startHour" placeholder="开始时间" aria-label="开始时间"><el-option v-for="hour in 14" :key="hour" :label="`${hour + 7}:00`" :value="hour + 7" /></el-select>
        <el-select v-model="endHour" placeholder="结束时间" aria-label="结束时间"><el-option v-for="hour in 14" :key="hour" :label="`${hour + 8}:00`" :value="hour + 8" /></el-select>
        <div class="socket-options"><el-checkbox v-model="fixed">固定插座</el-checkbox><el-checkbox v-model="mobile">移动插座</el-checkbox></div>
        <el-button type="primary" :loading="searching" @click="search(1)">查找座位</el-button>
      </div>
      <div v-if="searchResult" class="results-area">
        <div class="result-caption">共 {{ searchResult.total }} 个可用座位</div>
        <el-table v-if="searchResult.items.length" :data="searchResult.items" row-key="seat_id">
          <el-table-column label="教室" min-width="220"><template #default="{ row }">{{ row.building_name }} · {{ row.room_name }}</template></el-table-column>
          <el-table-column label="座位" min-width="120"><template #default="{ row }"><strong>{{ row.seat_no }}</strong><el-tag v-if="row.seat_id === reuseSeat" class="history-tag" size="small" type="info">历史座位</el-tag></template></el-table-column>
          <el-table-column label="配套设施" min-width="150"><template #default="{ row }">{{ socketLabel(row) }}</template></el-table-column>
          <el-table-column label="操作" width="100" align="right"><template #default="{ row }"><el-button type="primary" link :disabled="busy" @click="book(row)">立即预约</el-button></template></el-table-column>
        </el-table>
        <el-empty v-else description="该时段没有可预约座位" />
        <el-pagination v-if="searchResult.total > 20" layout="total, prev, pager, next" :total="searchResult.total" :page-size="20" :current-page="searchPage" @current-change="search" />
      </div>
    </section>

    <section class="seat-panel" aria-label="我的预约">
      <div class="panel-heading"><div><h2>我的预约</h2><p>查看状态、签到或复用历史座位</p></div><el-button :loading="busy" @click="loadList()">刷新</el-button></div>
      <el-table v-if="list?.items.length" :data="list.items" row-key="id">
        <el-table-column label="教室与座位" min-width="220"><template #default="{ row }"><strong>{{ row.resource_snapshot.room_name }}</strong><div class="secondary">{{ row.resource_snapshot.building_name }} · 座位 {{ row.resource_snapshot.seat_no }}</div></template></el-table-column>
        <el-table-column label="使用时段" min-width="250"><template #default="{ row }">{{ showTime(row.start_at) }}<div class="secondary">至 {{ showTime(row.end_at) }}</div></template></el-table-column>
        <el-table-column label="状态" min-width="110"><template #default="{ row }"><el-tag :type="statusTagType(row.status)" round>{{ statusLabels[row.status] || row.status }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="150" align="right"><template #default="{ row }"><el-button link type="primary" @click="openDetail(row.id)">详情</el-button><el-button link @click="reuse(row)">复用</el-button></template></el-table-column>
      </el-table>
      <el-empty v-else description="暂无预约" />
      <el-pagination v-if="list && list.total > 20" layout="total, prev, pager, next" :total="list.total" :page-size="20" :current-page="listPage" @current-change="loadList" />
    </section>

    <section class="seat-panel" aria-label="违约记录">
      <div class="panel-heading"><div><h2>违约记录</h2><p>查看未签到等违约情况</p></div></div>
      <el-table v-if="violations?.items.length" :data="violations.items" row-key="id">
        <el-table-column label="发生时间" min-width="190"><template #default="{ row }">{{ showTime(row.occurred_at) }}</template></el-table-column>
        <el-table-column label="关联预约" min-width="120"><template #default="{ row }"><el-button link type="primary" @click="openDetail(row.reservation_id)">#{{ row.reservation_id }}</el-button></template></el-table-column>
        <el-table-column label="原因" min-width="240" prop="reason" />
      </el-table>
      <el-empty v-else description="暂无违约记录" />
      <el-pagination v-if="violations && violations.total > 20" layout="total, prev, pager, next" :total="violations.total" :page-size="20" :current-page="violationPage" @current-change="loadViolations" />
    </section>

    <el-dialog v-model="detailOpen" title="预约详情" width="min(640px, 94vw)" destroy-on-close @closed="closeDetail">
      <div v-loading="detailLoading" class="detail-body">
        <template v-if="selected">
          <div class="detail-title"><div><span class="secondary">预约 #{{ selected.id }}</span><h2>{{ selected.resource_snapshot.room_name }} · {{ selected.resource_snapshot.seat_no }}</h2></div><el-tag :type="statusTagType(selected.status)" round>{{ statusLabels[selected.status] || selected.status }}</el-tag></div>
          <el-descriptions :column="1" border><el-descriptions-item label="地点">{{ selected.resource_snapshot.building_name }} · {{ selected.resource_snapshot.room_name }}</el-descriptions-item><el-descriptions-item label="座位">{{ selected.resource_snapshot.seat_no }}</el-descriptions-item><el-descriptions-item label="使用时段">{{ showTime(selected.start_at) }} 至 {{ showTime(selected.end_at) }}</el-descriptions-item></el-descriptions>
          <div v-if="selected.status === 'RESERVED'" class="check-in"><el-input v-model="code" aria-label="签到码" inputmode="numeric" maxlength="8" autocomplete="off" placeholder="请输入八位签到码" /><el-button type="primary" :loading="busy" @click="checkIn(selected)">签到</el-button></div>
        </template>
      </div>
      <template v-if="selected" #footer><el-button @click="reuse(selected); detailOpen = false">复用座位</el-button><el-button v-if="selected.status === 'RESERVED'" type="danger" plain :loading="busy" @click="cancel(selected)">取消预约</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.seat-page{display:grid;gap:22px}.page-heading{display:flex;justify-content:space-between;align-items:flex-end}.section-kicker{margin:0 0 7px;color:#2563eb;font-size:12px;font-weight:700;letter-spacing:.12em}.page-heading h1{margin:0;font-size:30px}.page-description{margin:9px 0 0;color:#64748b}.seat-panel{padding:22px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;box-shadow:0 8px 24px rgba(15,23,42,.04)}.panel-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}.panel-heading h2{margin:0;font-size:18px}.panel-heading p{margin:5px 0 0;color:#64748b;font-size:13px}.query-controls{display:grid;grid-template-columns:minmax(180px,1.35fr) minmax(150px,1fr) 120px 120px auto auto;gap:12px;align-items:center}.query-controls :deep(.el-select),.query-controls :deep(.el-date-editor){width:100%}.socket-options{display:flex;gap:12px;white-space:nowrap}.results-area{margin-top:20px}.result-caption{margin-bottom:10px;color:#64748b;font-size:13px}.history-tag{margin-left:8px}.secondary{margin-top:4px;color:#64748b;font-size:12px}.el-pagination{justify-content:flex-end;margin-top:18px}.detail-body{min-height:130px}.detail-title{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:18px}.detail-title h2{margin:5px 0 0;font-size:21px}.check-in{display:flex;gap:10px;margin-top:18px}.check-in .el-input{max-width:260px}
@media(max-width:1050px){.query-controls{grid-template-columns:repeat(2,minmax(0,1fr))}.socket-options{min-height:32px;align-items:center}}
@media(max-width:640px){.seat-panel{padding:16px}.page-heading h1{font-size:25px}.query-controls{grid-template-columns:1fr}.socket-options{flex-wrap:wrap}.panel-heading{align-items:flex-start}.check-in{flex-direction:column}.check-in .el-input{max-width:none}}
</style>
