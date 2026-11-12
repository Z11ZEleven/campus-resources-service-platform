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
let fingerprint = '', requestKey = ''
const statusLabels: Record<string, string> = { RESERVED: '待签到', CHECKED_IN: '使用中', CANCELLED: '已取消', NO_SHOW: '未签到违约', COMPLETED: '已完成', INTERRUPTED: '已中断' }
const range = computed(() => ({ start_at: `${day.value}T${String(startHour.value).padStart(2, '0')}:00:00+08:00`, end_at: `${day.value}T${String(endHour.value).padStart(2, '0')}:00:00+08:00` }))
const showTime = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
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
  if (typeof id !== 'string' || !/^[1-9]\d*$/.test(id)) return
  try { selected.value = await api.detail(id) } catch (error) { ElMessage.error(errorMessage(error)) }
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
watch(() => route.params.id, loadDetail)
onMounted(async () => {
  await Promise.all([loadList(), loadViolations(), loadDetail(), rooms().then(result => { roomOptions.value = result.items }).catch(error => ElMessage.error(errorMessage(error)))])
})
</script>
<template>
  <section>
    <div class="eyebrow">STUDY SEATS</div><h1>自习座位预约</h1><p class="muted">按北京时间选择当天整点时段；提交时系统会再次检查资源。</p>
    <el-alert v-if="reuseSeat" title="已选择历史座位，请填写新时段并搜索，符合条件时可重新预约。" type="info" :closable="true" @close="reuseSeat = ''; reuseRoom = ''" />
    <div class="filters"><label>教室 <select v-model="reuseRoom"><option value="">全部教室</option><option v-for="room in roomOptions" :key="room.id" :value="room.id">{{ room.building_name }} · {{ room.name }}</option></select></label><label>日期 <input v-model="day" type="date" /></label><label>开始 <select v-model.number="startHour"><option v-for="hour in 14" :key="hour" :value="hour + 7">{{ hour + 7 }}:00</option></select></label><label>结束 <select v-model.number="endHour"><option v-for="hour in 14" :key="hour" :value="hour + 8">{{ hour + 8 }}:00</option></select></label><label><input v-model="fixed" type="checkbox" /> 固定插座</label><label><input v-model="mobile" type="checkbox" /> 移动插座</label><el-button type="primary" :loading="searching" @click="search(1)">查找座位</el-button></div>
    <div v-if="searchResult" class="table-wrap"><table><thead><tr><th>教室</th><th>座位</th><th>插座</th><th></th></tr></thead><tbody><tr v-for="seat in searchResult.items" :key="seat.seat_id"><td>{{ seat.building_name }} · {{ seat.room_name }}</td><td>{{ seat.seat_no }} <span v-if="seat.seat_id === reuseSeat">（历史座位）</span></td><td>{{ [seat.has_fixed_socket && '固定', seat.has_mobile_socket && '移动'].filter(Boolean).join('、') || '无' }}</td><td><el-button :disabled="busy" @click="book(seat)">预约</el-button></td></tr></tbody></table><el-empty v-if="!searchResult.items.length" description="该时段没有可预约座位" /><el-pagination v-if="searchResult.total > 20" layout="prev, pager, next" :total="searchResult.total" :page-size="20" :current-page="searchPage" @current-change="search" /></div>
    <h2>我的预约</h2><div v-if="selected" class="detail"><h3>预约详情 #{{ selected.id }}</h3><p>{{ selected.resource_snapshot.building_name }} · {{ selected.resource_snapshot.room_name }} · {{ selected.resource_snapshot.seat_no }}</p><p>{{ showTime(selected.start_at) }} — {{ showTime(selected.end_at) }}　{{ statusLabels[selected.status] || selected.status }}</p><template v-if="selected.status === 'RESERVED'"><label>签到码 <input v-model="code" inputmode="numeric" maxlength="8" autocomplete="off" /></label> <el-button :disabled="busy" @click="checkIn(selected)">签到</el-button> <el-button :disabled="busy" @click="cancel(selected)">取消预约</el-button></template><el-button @click="reuse(selected)">复用座位</el-button></div>
    <div v-if="list" class="table-wrap"><table><thead><tr><th>教室与座位</th><th>开始</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="row in list.items" :key="row.id"><td>{{ row.resource_snapshot.room_name }} · {{ row.resource_snapshot.seat_no }}</td><td>{{ showTime(row.start_at) }}</td><td>{{ statusLabels[row.status] || row.status }}</td><td><router-link :to="`/m1/reservations/${row.id}`">详情</router-link>　<el-button link @click="reuse(row)">复用</el-button></td></tr></tbody></table><el-empty v-if="!list.items.length" description="暂无预约" /><el-pagination v-if="list.total > 20" layout="prev, pager, next" :total="list.total" :page-size="20" :current-page="listPage" @current-change="loadList" /></div>
    <h2>违约记录</h2><div v-if="violations" class="table-wrap"><table><thead><tr><th>时间</th><th>预约</th><th>原因</th></tr></thead><tbody><tr v-for="item in violations.items" :key="item.id"><td>{{ showTime(item.occurred_at) }}</td><td><router-link :to="`/m1/reservations/${item.reservation_id}`">{{ item.reservation_id }}</router-link></td><td>{{ item.reason }}</td></tr></tbody></table><el-empty v-if="!violations.items.length" description="暂无违约记录" /><el-pagination v-if="violations.total > 20" layout="prev, pager, next" :total="violations.total" :page-size="20" :current-page="violationPage" @current-change="loadViolations" /></div>
  </section>
</template>
<style scoped>.detail { padding: 16px; margin-bottom: 20px; border: 1px solid #dce3eb; border-radius: 8px; }.detail input { width: 120px; } select { padding: 7px; }</style>
