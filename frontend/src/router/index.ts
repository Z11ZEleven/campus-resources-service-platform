import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { loginDestination } from '../utils/navigation'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('../views/LoginView.vue') },
    { path: '/resources', component: () => import('../views/ResourcesView.vue') },
    { path: '/admin/resources', component: () => import('../views/ResourceManagementView.vue') },
    { path: '/m3/work-orders', component: () => import('../views/WorkOrdersView.vue') },
    { path: '/m3/work-orders/:id', component: () => import('../views/WorkOrderDetailView.vue') },
    { path: '/notifications', component: () => import('../views/NotificationsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/resources' },
  ],
})
router.beforeEach(async to => {
  const auth = useAuthStore()
  await auth.restore()
  if (to.path !== '/login' && !auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.path === '/admin/resources' && !auth.user?.permissions.includes('resources:manage')) return '/resources'
  if (to.path === '/login' && auth.user) return loginDestination(to.query.redirect)
})
