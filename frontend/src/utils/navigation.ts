export function loginDestination(value: unknown): string {
  if (typeof value !== 'string' || value.startsWith('//')) return '/resources'
  return /^\/(resources|m2\/(?:applications|approval-tasks)|m3\/work-orders|notifications)(?:[/?#]|$)/.test(value) ? value : '/resources'
}

export function notificationDestination(value: unknown): string | null {
  if (typeof value !== 'string' || value !== value.trim()) return null
  return /^\/(?:m3\/work-orders|m2\/applications)\/[1-9]\d*$/.test(value) ? value : null
}
