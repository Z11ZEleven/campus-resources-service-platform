<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import QRCode from 'qrcode'
import * as m1 from '../api/m1'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const auth = useAuthStore()
const roomId = computed(() => String(route.params.roomId ?? ''))
const data = ref<m1.ScreenCode | null>(null)
const qr = ref('')
const loading = ref(false)
const error = ref('')
const stale = ref(false)
const username = ref('')
const password = ref('')
const loggingIn = ref(false)
const now = ref(Date.now())
let refreshTimer: number | undefined
let countdownTimer: number | undefined

const remaining = computed(() => {
  if (!data.value) return ''
  const left = Math.max(0, new Date(data.value.expires_at).getTime() - now.value)
  const hours = Math.floor(left / 3_600_000)
  const minutes = Math.floor((left % 3_600_000) / 60_000)
  const seconds = Math.floor((left % 60_000) / 1000)
  return [hours, minutes, seconds].map(value => String(value).padStart(2, '0')).join(':')
})
const needLogin = computed(() => !data.value && !loading.value && !error.value)
const qrUrl = computed(() =>
  data.value ? window.location.origin + data.value.checkin_path : '',
)

async function load() {
  loading.value = true
  try {
    const result = await m1.screenCode(roomId.value)
    data.value = result
    error.value = ''
    stale.value = false
    qr.value = await QRCode.toDataURL(window.location.origin + result.checkin_path, {
      width: 360,
      margin: 1,
      errorCorrectionLevel: 'M',
      color: { dark: '#111827', light: '#ffffff' },
    })
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response?.status
    if (status === 401) {
      data.value = null
      qr.value = ''
      error.value = ''
    } else if (status === 403) {
      error.value = '该账号没有读取此教室签到码的权限，请换用屏幕账号登录'
    } else {
      stale.value = data.value !== null
      error.value = errorMessage(err)
    }
  } finally {
    loading.value = false
  }
}

async function signIn() {
  loggingIn.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    password.value = ''
    await load()
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loggingIn.value = false
  }
}

onMounted(() => {
  now.value = Date.now()
  void load()
  refreshTimer = window.setInterval(() => void load(), 30_000)
  countdownTimer = window.setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (countdownTimer) clearInterval(countdownTimer)
})
</script>

<template>
  <section class="screen-shell">
    <div v-if="needLogin" class="panel">
      <p class="eyebrow">教室签到屏幕</p>
      <h1>登录屏幕账号</h1>
      <p class="hint">账号只需具备该教室的签到码读取权限。</p>
      <form class="login-form" @submit.prevent="signIn">
        <el-input v-model="username" placeholder="屏幕账号" autocomplete="username" />
        <el-input
          v-model="password"
          type="password"
          show-password
          placeholder="密码"
          autocomplete="current-password"
        />
        <el-button type="primary" native-type="submit" :loading="loggingIn">登录</el-button>
      </form>
      <p v-if="error" class="error">{{ error }}</p>
    </div>

    <div v-else-if="error && !data" class="panel">
      <h1>无法显示签到码</h1>
      <p class="error">{{ error }}</p>
      <el-button @click="load">重试</el-button>
    </div>

    <div v-else-if="data" class="display">
      <header class="display-head">
        <div>
          <p class="eyebrow">教室签到</p>
          <h1>{{ data.room.name }}</h1>
          <p class="room-code">{{ data.room.code }}</p>
        </div>
        <div class="date-block">
          <p class="eyebrow">校园日期</p>
          <p class="date">{{ data.business_date }}</p>
          <p class="countdown">有效剩余 {{ remaining }}</p>
        </div>
      </header>

      <div class="code-area" :class="{ stale }">
        <p class="code" aria-live="polite">{{ stale ? '••••••••' : data.code }}</p>
        <p class="code-label">8 位签到码</p>
      </div>

      <div class="qr-area" :class="{ stale }">
        <img v-if="qr && !stale" :src="qr" alt="扫码签到二维码" />
        <p v-if="qrUrl && !stale" class="qr-url">{{ qrUrl }}</p>
        <p v-if="stale" class="stale-note">连接中断，暂不显示二维码</p>
      </div>

      <footer class="display-foot">
        <p>使用手机扫描二维码，或在前端页面输入上方 8 位签到码。</p>
        <p v-if="stale" class="error">无法连接后端，正在重试。</p>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.screen-shell { min-height: 100vh; display: grid; place-items: center; padding: 4vh 4vw; background: #0f172a; color: #f8fafc; }
.panel { width: min(440px, 92vw); padding: 36px; border-radius: 20px; background: #111c33; box-shadow: 0 24px 60px rgb(0 0 0 / 35%); }
.panel h1 { margin: 6px 0 10px; font-size: 26px; }
.login-form { display: grid; gap: 14px; margin-top: 22px; }
.hint, .error { color: #cbd5e1; font-size: 14px; }
.error { color: #fca5a5; }
.display { width: min(1100px, 94vw); display: grid; grid-template-columns: 1fr 380px; gap: 4vw; align-items: center; }
.display-head { grid-column: 1 / -1; display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid #26334d; padding-bottom: 24px; }
.display-head h1 { margin: 4px 0; font-size: clamp(34px, 4vw, 58px); }
.eyebrow { margin: 0; color: #93a4c4; letter-spacing: .18em; font-size: 13px; }
.room-code, .date { margin: 2px 0; color: #cbd5e1; font-size: 20px; }
.date-block { text-align: right; }
.countdown { margin: 6px 0 0; color: #7dd3fc; font-variant-numeric: tabular-nums; }
.code-area { text-align: center; }
.code { margin: 0; font-size: clamp(90px, 13vw, 190px); line-height: 1; letter-spacing: .08em; font-variant-numeric: tabular-nums; font-weight: 650; }
.code-label { margin: 12px 0 0; color: #93a4c4; letter-spacing: .3em; }
.qr-area { display: grid; place-items: center; 
.qr-url { margin: 10px 0 0; color: #475569; font-size: 11px; word-break: break-all; text-align: center; }padding: 24px; border-radius: 24px; background: #fff; min-height: 380px; }
.qr-area img { width: 100%; max-width: 340px; }
.stale { opacity: .35; }
.stale-note { color: #334155; text-align: center; }
.display-foot { grid-column: 1 / -1; border-top: 1px solid #26334d; padding-top: 20px; color: #93a4c4; text-align: center; }
@media (max-width: 900px) { .display { grid-template-columns: 1fr; } .qr-area { justify-self: center; width: min(360px, 80vw); } .display-head { flex-direction: column; gap: 16px; } .date-block { text-align: left; } }
</style>
