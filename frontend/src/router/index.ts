import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { loginDestination } from '../utils/navigation'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('../views/LoginView.vue') },
    { path: '/resources', component: () => import('../views/ResourcesView.vue') },
    { path: '/m2/applications', component: () => import('../views/M2ApplicationsView.vue') },
    { path: '/m3/work-orders', component: () => import('../views/WorkOrdersView.vue') },
    { path: '/m3/work-orders/:id', component: () => import('../views/WorkOrderDetailView.vue') },
    { path: '/notifications', component: () => import('../views/NotificationsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/resources' },
  ],
})
router.beforeEach(to => {
  const auth = useAuthStore()
  if (to.path !== '/login' && !auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.path === '/login' && auth.user) return loginDestination(to.query.redirect)
})
