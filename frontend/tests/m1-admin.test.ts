import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, test, vi } from 'vitest'
import ElementPlus from 'element-plus'
import M1AdminView from '../src/views/M1AdminView.vue'

const auth = vi.hoisted(() => ({ user: { permissions: ['m1:statistics:read', 'm1:settings:write'] } }))
const api = vi.hoisted(() => ({
  settings: vi.fn(), updateSettings: vi.fn(), statistics: vi.fn(), adminReservations: vi.fn(), checkinCode: vi.fn(),
}))
vi.mock('../src/api/m1', () => api)
vi.mock('../src/api/resources', () => ({ rooms: async () => ({ items: [{ id: '1', building_name: 'B1', name: '101' }] }) }))
vi.mock('../src/stores/auth', () => ({ useAuthStore: () => auth }))

beforeEach(() => {
  auth.user.permissions = ['m1:statistics:read', 'm1:settings:write']
  api.settings.mockResolvedValue({ max_hours: 4, version: '7' })
  api.statistics.mockResolvedValue({ reservation_count: 2, checked_in_count: 1, completed_count: 1, no_show_count: 0, cancelled_count: 1, interrupted_count: 0, planned_minutes: 120, actual_minutes: 30, fulfillment_rate: 0.25 })
  api.adminReservations.mockResolvedValue({ items: [], total: 0 })
})

test('shows server statistics and omits check-in actions without permission', async () => {
  const wrapper = mount(M1AdminView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(wrapper.text()).toContain('25.0%')
  expect(wrapper.text()).not.toContain('读取签到码')
  expect(api.statistics).toHaveBeenCalledOnce()
  wrapper.unmount()
})

test('submits the settings version and displays the saved server value', async () => {
  api.updateSettings.mockResolvedValue({ max_hours: 3, version: '8' })
  const wrapper = mount(M1AdminView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  const input = wrapper.find('input[role="spinbutton"]')
  await input.setValue('3')
  await input.trigger('change')
  const save = wrapper.findAll('button').find(button => button.text() === '保存参数')!
  await save.trigger('click')
  await flushPromises()
  expect(api.updateSettings).toHaveBeenCalledWith(3, '7')
  expect(wrapper.text()).toContain('当前版本：8')
  wrapper.unmount()
})


test('does not fetch global settings for a statistics-only manager', async () => {
  auth.user.permissions = ['m1:statistics:read']
  const wrapper = mount(M1AdminView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(api.settings).not.toHaveBeenCalled()
  expect(wrapper.text()).not.toContain('保存参数')
  wrapper.unmount()
})

test('loads reservations without statistics permission and refreshes the room filter', async () => {
  auth.user.permissions = ['m1:reservation:read_scoped']
  const wrapper = mount(M1AdminView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(api.statistics).not.toHaveBeenCalled()
  expect(api.adminReservations).toHaveBeenCalledWith(1, undefined)
  expect(wrapper.text()).toContain('当前范围没有预约')
  await wrapper.find('select').setValue('1')
  await wrapper.findAll('button').find(button => button.text() === '查询')!.trigger('click')
  await flushPromises()
  expect(api.adminReservations).toHaveBeenLastCalledWith(1, '1')
  wrapper.unmount()
})

test('loads reservations even while statistics fails', async () => {
  auth.user.permissions = ['m1:statistics:read', 'm1:reservation:read_scoped']
  api.statistics.mockRejectedValueOnce(new Error('statistics unavailable'))
  const wrapper = mount(M1AdminView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(api.adminReservations).toHaveBeenCalledWith(1, undefined)
  expect(wrapper.text()).toContain('当前范围没有预约')
  wrapper.unmount()
})

test('does not request reservations without reservation permission', async () => {
  const wrapper = mount(M1AdminView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(api.adminReservations).not.toHaveBeenCalled()
  wrapper.unmount()
})
