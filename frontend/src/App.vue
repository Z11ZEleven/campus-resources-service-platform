<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { useNotificationsStore } from './stores/notifications'

const auth = useAuthStore()
const router = useRouter()
const notifications = useNotificationsStore()
const notificationLabel = computed(() => notifications.countError
  ? '通知中心，未读数量暂时无法更新'
  : notifications.countLoaded ? `通知中心，${notifications.unreadCount} 条未读` : '通知中心')
function logout() { auth.clear(); void router.replace('/login') }
function sessionExpired() {
  const destination = router.currentRoute.value.fullPath
  auth.clear()
  void router.replace({ path: '/login', query: { redirect: destination } })
}
onMounted(() => { window.addEventListener('auth-expired', sessionExpired); notifications.startPolling() })
onUnmounted(() => { window.removeEventListener('auth-expired', sessionExpired); notifications.stopPolling() })
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <router-link class="brand" :to="auth.user ? '/resources' : '/login'"><span class="brand-mark" aria-hidden="true">校</span><strong>校园资源</strong></router-link>
      <nav v-if="auth.user" class="primary-nav" aria-label="主导航">
        <router-link to="/resources">空间资源</router-link>
        <router-link to="/m3/work-orders">报修工单</router-link>
        <router-link to="/notifications" :aria-label="notificationLabel" :title="notificationLabel">
          通知中心<span v-if="notifications.unreadCount > 0" class="notification-badge" aria-hidden="true">{{ notifications.unreadCount > 99 ? '99+' : notifications.unreadCount }}</span>
          <span v-if="notifications.countError" class="notification-warning" aria-hidden="true">!</span>
        </router-link>
      </nav>
      <div v-if="auth.user" class="user-info">
        <span>{{ auth.user.display_name }}<span class="muted user-department"> · {{ auth.user.department_code }}</span></span>
        <el-button size="small" @click="logout">退出</el-button>
      </div>
    </header>
    <main><router-view :key="auth.user?.id ?? 'guest'" /></main>
  </div>
</template>

<style scoped>
.notification-badge { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 18px; margin-left: 6px; padding: 0 5px; border-radius: 9px; background: #eaf1ff; color: #2563eb; font-size: 10px; font-weight: 650; }
.notification-warning { margin-left: 5px; color: #a56d1e; font-size: 11px; }
.primary-nav a { white-space: nowrap; }
@media (min-width: 601px) and (max-width: 940px) {
  .topbar { flex-wrap: wrap; padding-top: 16px; padding-bottom: 12px; gap: 16px; }
  .primary-nav { order: 3; width: 100%; min-height: 32px; }
}
@media (max-width: 600px) {
  .user-info > span { max-width: 125px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .primary-nav { gap: 20px; }
}
</style>
