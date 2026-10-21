<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { errorMessage } from '../api/request'

const username = ref('')
const password = ref('')
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()
async function submit() {
  if (loading.value || !username.value.trim() || !password.value) return
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    await router.replace('/resources')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}
</script>

<template>
  <section class="login-card">
    <div class="eyebrow">CAMPUS RESOURCES</div>
    <h1>登录校园资源平台</h1>
    <p class="muted">使用初始化脚本生成的测试账号。刷新页面后需要重新登录。</p>
    <form @submit.prevent="submit">
      <label>用户名<input v-model="username" autocomplete="username" required maxlength="64" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" required maxlength="256" /></label>
      <el-button type="primary" native-type="submit" :loading="loading" class="full-width">登录</el-button>
    </form>
    <p class="small muted">测试账号包括 student、teacher、room_admin、property_admin、repairer。密码由本地初始化环境配置提供。</p>
  </section>
</template>
