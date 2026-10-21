import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('../views/LoginView.vue') },
    { path: '/resources', component: () => import('../views/ResourcesView.vue') },
    { path: '/m2/applications', component: () => import('../views/M2ApplicationsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/resources' },
  ],
})
router.beforeEach(to => {
  const auth = useAuthStore()
  if (to.path !== '/login' && !auth.user) return '/login'
  if (to.path === '/login' && auth.user) return '/resources'
})
