<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { request, errorMessage } from '../api/request'
import { getBuildings, getSeats, getSchedule, saveBuilding, saveRoom, saveSeat, saveSchedule, type Building, type ManagedRoom, type RoomInput, type SeatInput, type Schedule } from '../api/spaceManagement'
import type { Seat } from '../api/resources'
const buildings = ref<Building[]>([])
const roomList = ref<ManagedRoom[]>([])
const selected = ref('')
const roomPage = ref(1)
const roomTotal = ref(0)
const seatPage = ref(1)
const seatTotal = ref(0)
const seats = ref<Seat[]>([])
const busy = ref(false)
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
function campusInput(value: string) {
  const time = Date.parse(value)
  return Number.isNaN(time) ? '' : new Date(time + 8 * 3600000).toISOString().slice(0, 16)
}
function campusValue(value: string) { return value ? `${value}:00+08:00` : '' }
const roomId = ref<string>()
const seatId = ref<string>()
const buildingId = ref<string>()
const building = reactive({ code: '', name: '', is_active: true })
const room = reactive<RoomInput>({ code: '', name: '', building_id: '', capacity: 1, department_code: null, open_time: '08:00:00', close_time: '22:00:00', is_active: true })
const seat = reactive<SeatInput>({ seat_no: '', has_fixed_socket: false, has_mobile_socket: false, is_active: true })
const schedule = reactive<Schedule>({ weekly: [], exceptions: [] })
function success(message: string) { ElMessage.success(message) }
async function perform(action: () => Promise<void>) {
  busy.value = true
  try { await action() } catch (e) { ElMessage.error(errorMessage(e)) } finally { busy.value = false }
}
async function reload() {
  buildings.value = await getBuildings()
  const value = (await request.get<{ items: ManagedRoom[]; total: number }>('/resources/rooms', { params: { page: roomPage.value, page_size: 20 } })).data
  roomList.value = value.items; roomTotal.value = value.total
}
async function selectRoom(value: ManagedRoom) {
  selected.value = value.id; roomId.value = value.id; seatPage.value = 1; newSeat()
  Object.assign(room, { code: value.code, name: value.name, building_id: value.building_id ?? '', capacity: value.capacity, department_code: value.department_code, open_time: value.open_time, close_time: value.close_time, is_active: value.is_active })
  await reloadSeats()
  Object.assign(schedule, await getSchedule(value.id))
}
async function reloadSeats() { const value = await getSeats(selected.value, seatPage.value); seats.value = value.items; seatTotal.value = value.total }
function newRoom() { selected.value = ''; seats.value = []; roomId.value = undefined; Object.assign(room, { code: '', name: '', building_id: buildings.value[0]?.id ?? '', capacity: 1, department_code: null, open_time: '08:00:00', close_time: '22:00:00', is_active: true }) }
function newSeat() { seatId.value = undefined; Object.assign(seat, { seat_no: '', has_fixed_socket: false, has_mobile_socket: false, is_active: true }) }
onMounted(() => perform(reload))
</script>
<template>
  <section v-loading="busy">
    <h1>空间与座位维护</h1>
    <p class="muted">维护授权范围内的建筑、教室、插座与开放时间。停用保留历史记录；与有效占用冲突的变更会被拒绝。</p>
    <h2>建筑</h2>
    <el-select placeholder="选择建筑编辑" @change="(id: string) => { const b = buildings.find(x => x.id === id); if (b) { buildingId = id; Object.assign(building, { code: b.code, name: b.name, is_active: b.is_active }) } }">
      <el-option v-for="b in buildings" :key="b.id" :value="b.id" :label="b.name" />
    </el-select>
    <el-button @click="buildingId = undefined; Object.assign(building, { code: '', name: '', is_active: true })">新建建筑</el-button>
    <el-form label-position="top" @submit.prevent>
      <el-form-item label="建筑编码"><el-input v-model="building.code" maxlength="32" /></el-form-item>
      <el-form-item label="建筑名称"><el-input v-model="building.name" maxlength="100" /></el-form-item>
      <el-checkbox v-model="building.is_active">建筑启用</el-checkbox>
      <el-button @click="perform(async () => { const saved = await saveBuilding(building, buildingId); buildingId = saved.id; await reload(); success('建筑已保存') })">保存建筑</el-button>
    </el-form>
    <h2>教室</h2>
    <el-table :data="roomList" @row-click="(r: ManagedRoom) => perform(() => selectRoom(r))">
      <el-table-column prop="code" label="编码" /><el-table-column prop="name" label="教室" /><el-table-column prop="building_name" label="建筑" /><el-table-column prop="resource_version" label="资源版本" />
    </el-table>
    <el-pagination v-model:current-page="roomPage" :page-size="20" :total="roomTotal" layout="prev, pager, next" @current-change="() => perform(reload)" />
    <el-button @click="newRoom">新建教室</el-button>
    <el-form label-position="top" @submit.prevent>
      <el-form-item label="教室编码"><el-input v-model="room.code" maxlength="32" /></el-form-item>
      <el-form-item label="教室名称"><el-input v-model="room.name" maxlength="100" /></el-form-item>
      <el-form-item label="所属建筑"><el-select v-model="room.building_id"><el-option v-for="b in buildings" :key="b.id" :value="b.id" :label="b.name" /></el-select></el-form-item>
      <el-form-item label="容量"><el-input-number v-model="room.capacity" :min="1" :max="100000" /></el-form-item>
      <el-form-item label="院系代码（空为公共）"><el-input :model-value="room.department_code ?? ''" @update:model-value="(v: string) => room.department_code = v || null" /></el-form-item>
      <el-form-item label="默认每日开放时间（校园时间）"><input v-model="room.open_time" type="time" aria-label="开放起点" /><input v-model="room.close_time" type="time" aria-label="开放终点" /></el-form-item>
      <el-checkbox v-model="room.is_active">教室启用</el-checkbox>
      <el-button @click="perform(async () => { const saved = await saveRoom(room, roomId); await reload(); await selectRoom(saved); success('教室已保存') })">保存教室</el-button>
    </el-form>
    <template v-if="selected">
      <h2>座位</h2>
      <el-table :data="seats" @row-click="(s: Seat) => { seatId = s.id; Object.assign(seat, { seat_no: s.seat_no, has_fixed_socket: s.has_fixed_socket, has_mobile_socket: s.has_mobile_socket, is_active: s.is_active }) }">
        <el-table-column prop="seat_no" label="座位号" /><el-table-column prop="has_fixed_socket" label="固定插座" /><el-table-column prop="has_mobile_socket" label="移动插座" /><el-table-column prop="is_active" label="启用" />
      </el-table>
      <el-pagination v-model:current-page="seatPage" :page-size="100" :total="seatTotal" layout="prev, pager, next" @current-change="() => perform(reloadSeats)" />
      <el-button @click="newSeat">新增座位</el-button>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="座位号"><el-input v-model="seat.seat_no" maxlength="32" /></el-form-item>
        <el-checkbox v-model="seat.has_fixed_socket">固定插座</el-checkbox><el-checkbox v-model="seat.has_mobile_socket">移动插座</el-checkbox><el-checkbox v-model="seat.is_active">座位启用</el-checkbox>
        <el-button @click="perform(async () => { await saveSeat(selected, seat, seatId); await reloadSeats(); await reload(); success('座位已保存') })">保存座位</el-button>
      </el-form>
      <h2>周开放规则</h2>
      <p>选择星期与校园时间；结束早于开始表示跨午夜。保存后替代默认每日时间。空规则表示关闭，特殊开放可单独启用。</p>
      <div v-for="(w, i) in schedule.weekly" :key="i" class="filters"><el-select v-model="w.weekday" aria-label="星期" style="width: 100px"><el-option v-for="(day, index) in weekdays" :key="index" :value="index" :label="day" /></el-select><input v-model="w.start_time" type="time" aria-label="规则起点" /><input v-model="w.end_time" type="time" aria-label="规则终点" /><el-button @click="schedule.weekly.splice(i, 1)">移除</el-button></div>
      <el-button @click="schedule.weekly.push({ weekday: 0, start_time: '08:00:00', end_time: '22:00:00' })">添加周规则</el-button>
      <h2>特殊开放与关闭</h2>
      <p>日期与时间均按北京时间填写；特殊关闭优先。</p>
      <div v-for="(e, i) in schedule.exceptions" :key="i" class="filters"><el-select v-model="e.kind"><el-option value="OPEN" label="开放" /><el-option value="CLOSE" label="关闭" /></el-select><input :value="campusInput(e.start_at)" type="datetime-local" aria-label="特殊时段起点" @input="e.start_at = campusValue(($event.target as HTMLInputElement).value)" /><input :value="campusInput(e.end_at)" type="datetime-local" aria-label="特殊时段终点" @input="e.end_at = campusValue(($event.target as HTMLInputElement).value)" /><el-button @click="schedule.exceptions.splice(i, 1)">移除</el-button></div>
      <el-button @click="schedule.exceptions.push({ kind: 'CLOSE', start_at: '', end_at: '' })">添加特殊时段</el-button>
      <el-button @click="perform(async () => { await saveSchedule(selected, schedule); await reload(); success('开放规则已保存') })">保存全部开放规则</el-button>
    </template>
  </section>
</template>
