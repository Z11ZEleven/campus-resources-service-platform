import { expect, test } from 'vitest'
import { loginDestination, notificationDestination } from '../src/utils/navigation'

test.each([
  '/m1/reservations', '/m1/reservations/12', '/admin/m1',
  '/m2/applications', '/m2/applications/34', '/m2/approval-tasks',
  '/m3/work-orders', '/m3/work-orders/56', '/notifications',
])('preserves the integrated login destination %s', path => {
  expect(loginDestination(path)).toBe(path)
})

test.each(['/m1/reservations/12', '/m2/applications/34', '/m3/work-orders/56'])(
  'opens notifications from every business module: %s', path => {
    expect(notificationDestination(path)).toBe(path)
  },
)

test.each(['//example.com', '/m2/applications/0', '/m1/reservations/12/other', '/admin/m1'])(
  'rejects invalid notification destinations: %s', path => {
    expect(notificationDestination(path)).toBeNull()
  },
)
