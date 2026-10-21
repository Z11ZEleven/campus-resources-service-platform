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
    <nav v-if="auth.user" class="module-nav" aria-label="模块导航">
      <router-link to="/resources">教室与座位</router-link>
      <router-link to="/m2/applications">教室申请 · M2</router-link>
    </nav>
    <main><router-view /></main>
  </div>
</template>

<style scoped>
.module-nav { display: flex; gap: 24px; padding: 14px 5%; background: white; border-bottom: 1px solid #e5e7eb; }
.module-nav a { color: #64748b; text-decoration: none; font-size: 14px; }
.module-nav a.router-link-active { color: #2563eb; font-weight: 600; }
</style>
