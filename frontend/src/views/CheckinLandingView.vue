<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import * as m1 from '../api/m1'
import { errorMessage } from '../api/request'

const route = useRoute()
const room = computed(() => String(route.query.room ?? ''))
const code = ref(String(route.query.code ?? ''))
const reservations = ref<m1.Reservation[]>([])
const loading = ref(false)
const busy = ref('')
const message = ref('')
const success = ref('')

const candidates = computed(() =>
  reservations.value.filter(
    item => item.status === 'RESERVED' && (!room.value || String(item.room_id) === room.value),
  ),
)

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const page = await m1.reservations(1)
    reservations.value = page.items
    message.value = ''
  } catch (error) {
    message.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

async function submit(item: m1.Reservation) {
  if (!/^\d{8}$/.test(code.value)) {
    message.value = '请输入 8 位签到码'
    return
  }
  busy.value = item.id
  message.value = ''
  success.value = ''
  try {
    await m1.checkIn(item.id, code.value)
    success.value = '签到成功'
    await load()
  } catch (error) {
    message.value = errorMessage(error)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="checkin-page">
    <header>
      <p class="eyebrow">扫码签到</p>
      <h1>确认签到</h1>
      <p class="hint">二维码已带入签到码，也可以手动输入。</p>
    </header>

    <el-form label-position="top" class="code-form" @submit.prevent>
      <el-form-item label="8 位签到码">
        <el-input v-model="code" maxlength="8" inputmode="numeric" placeholder="例如 12345678" />
      </el-form-item>
    </el-form>

    <el-alert v-if="success" :title="success" type="success" :closable="false" show-icon />
    <el-alert v-if="message" :title="message" type="error" :closable="false" show-icon />

    <section v-loading="loading" class="list">
      <h2>当前可签到的预约</h2>
      <p v-if="!loading && candidates.length === 0" class="empty">
        没有找到可签到的预约。请确认预约是否已开始、是否已签到，或从
        <router-link to="/m1/reservations">我的预约</router-link> 进入。
      </p>
      <article v-for="item in candidates" :key="item.id" class="row">
        <div>
          <p class="seat">{{ item.resource_snapshot.room_name }} · 座位 {{ item.resource_snapshot.seat_no }}</p>
          <p class="muted">{{ formatTime(item.start_at) }} 至 {{ formatTime(item.end_at) }}</p>
        </div>
        <el-button type="primary" :loading="busy === item.id" @click="submit(item)">签到</el-button>
      </article>
    </section>

    <router-link class="back" to="/m1/reservations">返回我的预约</router-link>
  </section>
</template>

<style scoped>
.checkin-page { max-width: 760px; margin: 0 auto; padding: 32px 20px 64px; display: grid; gap: 20px; }
.eyebrow { margin: 0; color: #64748b; letter-spacing: .18em; font-size: 13px; }
h1 { margin: 6px 0 4px; }
.hint, .muted { color: #64748b; }
.code-form { max-width: 260px; }
.list { display: grid; gap: 12px; }
.list h2 { margin: 8px 0; font-size: 18px; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 20px; border: 1px solid #e2e8f0; border-radius: 14px; background: #fff; }
.seat { margin: 0 0 4px; font-weight: 600; }
.empty { padding: 20px; border: 1px dashed #cbd5e1; border-radius: 14px; color: #475569; }
.back { color: #2563eb; }
</style>
