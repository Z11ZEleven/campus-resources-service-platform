<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { availability, rooms, type Availability, type Room, type Slot } from '../api/resources'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const options = ref<Room[]>([])
const selected = ref('')
const dateParts = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).formatToParts(new Date())
const getPart = (name: string) => dateParts.find(part => part.type === name)?.value
const date = ref(`${getPart('year')}-${getPart('month')}-${getPart('day')}`)
const result = ref<Availability | null>(null)
const loading = ref(false)
let requestSequence = 0
const hours = Array.from({ length: 14 }, (_, index) => index + 8)
const labels: Record<string, string> = {
  AVAILABLE: '可用', RESERVED: '已预约', ROOM_BOOKED: '整室占用',
  MAINTENANCE: '维修', CLOSED: '未开放', DISABLED: '停用',
}
const slotMap = computed(() => {
  const value = new Map<string, Slot>()
  for (const slot of result.value?.slots ?? []) {
    const hour = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23',
    }).format(new Date(slot.slot_start))
    value.set(`${slot.seat_id}-${Number(hour)}`, slot)
  }
  return value
})
function slot(seatId: string, hour: number) { return slotMap.value.get(`${seatId}-${hour}`) }
async function refresh() {
  if (!selected.value || !date.value) return
  const current = ++requestSequence
  loading.value = true
  result.value = null
  try {
    const value = await availability(selected.value, date.value)
    if (current === requestSequence) result.value = value
  } catch (error) {
    if (current === requestSequence) ElMessage.error(errorMessage(error))
  } finally { if (current === requestSequence) loading.value = false }
}
onMounted(async () => {
  try {
    const value = await rooms()
    options.value = value.items
    if (value.total > value.items.length) ElMessage.info('示例页仅显示前 100 间教室，正式列表请接入分页')
    selected.value = options.value[0]?.id ?? ''
    await refresh()
  } catch (error) { ElMessage.error(errorMessage(error)) }
})
</script>

<template>
  <section>
    <div class="eyebrow">PUBLIC RESOURCES</div>
    <h1>教室与座位状态</h1>
    <p class="muted">当前角色：{{ auth.user?.roles.join('、') }}。可见教室由后端身份与权限决定。</p>
    <el-alert title="本页展示公共资源状态；仅整室类型教室请进入整室申请。可用状态仍需在提交时重新验证。" type="info" :closable="false" />
    <div class="filters">
      <label>教室<el-select v-model="selected" style="width: 280px" @change="refresh">
        <el-option v-for="room in options" :key="room.id" :label="room.name + ' · ' + room.code + (room.booking_mode === 'WHOLE_ROOM' ? ' · 仅整室' : '')" :value="room.id" />
      </el-select></label>
      <label>日期（北京时间）<input v-model="date" type="date" @change="refresh" /></label>
      <el-button :loading="loading" :disabled="!selected" @click="refresh">刷新状态</el-button>
    </div>
    <div class="legend"><span v-for="(label, status) in labels" :key="status" :class="['state', status]">{{ label }}</span></div>
    <div v-loading="loading" class="table-wrap">
      <table v-if="result">
        <thead><tr><th>座位</th><th>插座</th><th v-for="hour in hours" :key="hour">{{ hour }}:00</th></tr></thead>
        <tbody><tr v-for="seat in result.seats" :key="seat.id">
          <th>{{ seat.seat_no }}</th>
          <td>{{ seat.has_fixed_socket ? '固定' : seat.has_mobile_socket ? '导轨' : '无' }}</td>
          <td v-for="hour in hours" :key="hour">
            <span v-if="slot(seat.id, hour)" :class="['state', slot(seat.id, hour)?.status]"
              :title="'来源数：' + slot(seat.id, hour)?.source_count + '；来源：' + (slot(seat.id, hour)?.source_module ?? '无')">
              {{ labels[slot(seat.id, hour)!.status] }}
            </span>
            <span v-else>—</span>
          </td>
        </tr></tbody>
      </table>
      <el-empty v-else-if="!loading" description="请选择可访问的教室与日期" />
    </div>
    <p class="small muted">时间均为北京时间；每格表示从列示时间开始的 1 小时。示例表展示 08:00–22:00，接口返回完整 24 小时。</p>
  </section>
</template>
