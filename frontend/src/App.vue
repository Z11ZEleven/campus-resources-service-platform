<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()
function logout() { auth.clear(); void router.replace('/login') }
onMounted(() => window.addEventListener('auth-expired', logout))
onUnmounted(() => window.removeEventListener('auth-expired', logout))
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div><strong>校园资源</strong><span class="muted"> / 第一次功能迭代 · 公共层</span></div>
      <div v-if="auth.user" class="user-info">
        <span>{{ auth.user.display_name }} · {{ auth.user.department_code }}</span>
        <el-button size="small" @click="logout">退出</el-button>
      </div>
    </header>
    <main><router-view /></main>
  </div>
</template>
