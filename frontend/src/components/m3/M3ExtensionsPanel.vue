<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getWorkOrder, rateWorkOrder, type RatingView, type WorkOrderDetail } from '../../api/m3'
import { errorMessage } from '../../api/request'
import { useAuthStore } from '../../stores/auth'
import { canRate, formatTime, statusLabels } from '../../utils/m3'

const props = defineProps<{ order: WorkOrderDetail }>()
const emit = defineEmits<{ updated: [order: WorkOrderDetail]; rated: [rating: RatingView] }>()
const auth = useAuthStore()
const score = ref(0)
const comment = ref('')
const rating = ref<RatingView | null>(props.order.rating)
const submitting = ref(false)
const checking = ref(false)
const uncertain = ref(false)
const feedback = ref('')
const feedbackError = ref(false)
let epoch = 0
let disposed = false
let controller: AbortController | null = null

const allowed = computed(() => canRate(auth.user, { ...props.order, rating: rating.value }))
const escalationHistory = computed(() => [...props.order.escalations].sort((a, b) => {
  const byTime = b.escalated_at.localeCompare(a.escalated_at)
  return byTime || b.id.length - a.id.length || b.id.localeCompare(a.id)
}))
const timeoutLabel = computed(() => props.order.status === 'COMPLETED' ? '已完成计时'
  : props.order.timeout.current_stage_escalated ? '本阶段已升级'
    : props.order.timeout.is_overdue ? '已超时，待升级' : '本阶段未超时')

watch([() => props.order.id, () => auth.user?.id], () => {
  ++epoch
  controller?.abort()
  controller = null
  score.value = 0
  comment.value = ''
  rating.value = props.order.rating
  submitting.value = false
  checking.value = false
  uncertain.value = false
  feedback.value = ''
  feedbackError.value = false
})
watch(() => props.order.rating, value => { rating.value = value })

async function checkLatest() {
  if (checking.value || submitting.value) return
  const target = props.order.id
  const current = ++epoch
  controller?.abort()
  controller = new AbortController()
  checking.value = true
  feedback.value = ''
  try {
    const latest = await getWorkOrder(target, controller.signal)
    if (disposed || current !== epoch || target !== props.order.id) return
    rating.value = latest.rating
    emit('updated', latest)
    uncertain.value = false
    feedbackError.value = false
    feedback.value = latest.rating ? '已确认评价提交成功，评价不可修改。'
      : '已获取最新结果，目前没有评价记录。请核对评分与评语后，手动提交一次。'
  } catch (error) {
    if (disposed || current !== epoch || axios.isCancel(error)) return
    uncertain.value = true
    feedbackError.value = true
    feedback.value = `暂时无法核对评价结果：${errorMessage(error)}。请先重新核对，避免重复提交。`
  } finally {
    if (!disposed && current === epoch) checking.value = false
  }
}

async function submitRating() {
  if (!allowed.value || submitting.value || checking.value || uncertain.value) return
  if (!Number.isInteger(score.value) || score.value < 1 || score.value > 5) {
    feedbackError.value = true
    feedback.value = '请选择 1～5 星的整数评分。'
    return
  }
  const text = comment.value.trim()
  if (text.length > 500) {
    feedbackError.value = true
    feedback.value = '评价内容不能超过 500 字。'
    return
  }
  const target = props.order.id
  const current = ++epoch
  controller?.abort()
  controller = new AbortController()
  submitting.value = true
  feedback.value = ''
  let shouldCheck = false
  try {
    const result = await rateWorkOrder(target, { score: score.value, comment: text || null }, controller.signal)
    if (disposed || current !== epoch || target !== props.order.id) return
    rating.value = result
    // Send only the confirmed mutation result. The parent fetches a new full
    // detail instead of replacing current history with this component's snapshot.
    emit('rated', result)
    score.value = 0
    comment.value = ''
    feedbackError.value = false
    feedback.value = '评价已提交，感谢反馈。评价不可修改。'
    ElMessage.success('评价已提交')
  } catch (error) {
    if (disposed || current !== epoch || axios.isCancel(error)) return
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    feedbackError.value = true
    if (status === 409 || status === 403 || status === 404 || !status || status >= 500) {
      uncertain.value = true
      feedback.value = status === 409 ? '评价或工单状态已变化，正在核对最新结果。'
        : '本次评价结果需要核对，正在获取最新详情；系统不会自动重发评价。'
      shouldCheck = true
    } else feedback.value = errorMessage(error)
  } finally {
    if (!disposed && current === epoch) submitting.value = false
  }
  if (shouldCheck && !disposed && current === epoch) await checkLatest()
}

onBeforeUnmount(() => { disposed = true; ++epoch; controller?.abort() })
</script>

<template>
  <section class="extension-panel timeout-panel" aria-labelledby="timeout-heading">
    <header class="panel-title"><h2 id="timeout-heading">时限与升级</h2><span>累计升级 {{ order.escalation_count }} 次</span></header>
    <div class="timeout-summary" :class="{ overdue: order.timeout.is_overdue }">
      <el-tag :type="order.status === 'COMPLETED' ? 'success' : order.timeout.is_overdue ? 'warning' : 'info'" effect="light">{{ timeoutLabel }}</el-tag>
      <dl>
        <div><dt>本阶段开始</dt><dd><time :datetime="order.stage_started_at">{{ formatTime(order.stage_started_at) }}</time></dd></div>
        <div><dt>本阶段截止</dt><dd><time v-if="order.timeout.due_at" :datetime="order.timeout.due_at">{{ formatTime(order.timeout.due_at) }}</time><span v-else>工单已完成</span></dd></div>
      </dl>
      <p>以上状态以最近一次获取的服务器结果为准。超时升级会通知物业管理员，工单继续保持当前处理状态。</p>
    </div>
    <ol v-if="escalationHistory.length" class="escalation-history" aria-label="超时升级记录">
      <li v-for="event in escalationHistory" :key="event.id">
        <div class="event-heading"><h3>{{ statusLabels[event.stage] }}阶段升级</h3><time :datetime="event.escalated_at">{{ formatTime(event.escalated_at) }}</time></div>
        <p class="event-meta">{{ event.triggered_by === null ? '系统自动扫描' : `物业管理员 #${event.triggered_by} 手动补扫` }} · 完工轮次 {{ event.completion_round }}</p>
        <p class="event-reason">{{ event.reason }}</p>
        <p class="event-meta">阶段起点 {{ formatTime(event.stage_started_at) }}<br>原定截止 {{ formatTime(event.due_at) }}</p>
      </li>
    </ol>
    <p v-else class="helper">暂无超时升级记录。</p>
  </section>

  <section class="extension-panel rating-panel" aria-labelledby="rating-heading">
    <header class="panel-title"><h2 id="rating-heading">报修评价</h2><span>由原报修人提交一次</span></header>
    <el-alert v-if="feedback" :title="feedback" :type="feedbackError ? 'warning' : 'success'" :closable="false" show-icon class="feedback" role="status" />
    <template v-if="rating">
      <div class="submitted-score"><span aria-hidden="true" class="rating-stars">{{ '★'.repeat(rating.score) }}<span class="empty-stars">{{ '☆'.repeat(5 - rating.score) }}</span></span><strong>{{ rating.score }} / 5 星</strong></div>
      <p class="rating-comment">{{ rating.comment || '报修人未填写评语。' }}</p>
      <p class="helper">报修人 #{{ rating.reporter_id }} · {{ formatTime(rating.created_at) }}<br>评价已提交，不可修改。</p>
    </template>
    <el-form v-else-if="allowed" label-position="top" :aria-busy="submitting || checking" @submit.prevent="submitRating">
      <p class="rating-intro">工单已验收完成，请评价这次维修服务。提交后不可修改。</p>
      <el-form-item label="维修评分" required>
        <el-rate v-model="score" :allow-half="false" :disabled="submitting || checking || uncertain" aria-label="维修评分，1 至 5 星" />
        <span class="score-label" aria-live="polite">{{ score ? `${score} 星` : '请选择评分' }}</span>
      </el-form-item>
      <el-form-item label="评价内容（选填）">
        <el-input v-model="comment" aria-label="评价内容（选填）" type="textarea" :rows="4" maxlength="500" show-word-limit :disabled="submitting || checking || uncertain" placeholder="例如维修效果、服务态度与处理效率，最多 500 字。" />
      </el-form-item>
      <div class="rating-actions">
        <el-button v-if="uncertain" type="primary" :loading="checking" :disabled="submitting" @click="checkLatest">重新核对评价结果</el-button>
        <el-button v-else native-type="submit" type="primary" :loading="submitting" :disabled="checking">提交评价</el-button>
      </div>
    </el-form>
    <p v-else class="helper">{{ order.status !== 'COMPLETED' ? '工单验收完成后，由原报修人提交评价。' : '原报修人尚未提交评价。' }}</p>
  </section>
</template>

<style scoped>
.extension-panel { background: #fff; border: 1px solid #e3eaf1; border-radius: 14px; padding: 28px; min-width: 0; color: #173b56; }
.panel-title { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 22px; }
.panel-title h2 { margin: 0; font-size: 17px; line-height: 1.5; font-weight: 650; }
.panel-title > span { font-size: 12px; color: #7b8da1; }
.timeout-summary { background: #f5f8fc; border: 1px solid #e8eef5; border-radius: 9px; padding: 18px; }
.timeout-summary.overdue { background: #fffbf3; border-color: #f2dfb8; }
.timeout-summary dl { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 20px 0 0; }
.timeout-summary dt { font-size: 12px; color: #7b8da1; margin-bottom: 7px; }
.timeout-summary dd { margin: 0; font-size: 13px; color: #526b83; line-height: 1.7; font-variant-numeric: tabular-nums; }
.timeout-summary p, .helper, .rating-intro { font-size: 12px; line-height: 1.9; color: #7b8da1; margin: 16px 0 0; }
.escalation-history { list-style: none; margin: 24px 0 0; padding: 0; }
.escalation-history li + li { margin-top: 20px; padding-top: 20px; border-top: 1px solid #edf2f7; }
.event-heading { display: flex; align-items: center; justify-content: space-between; gap: 6px 15px; flex-wrap: wrap; }
.event-heading h3 { margin: 0; font-size: 13px; line-height: 1.6; }
.event-heading time, .event-meta { font-size: 12px; color: #8191a2; line-height: 1.9; }
.event-meta { margin: 7px 0 0; }
.event-reason, .rating-comment { font-size: 14px; line-height: 1.9; color: #52677c; margin: 12px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.feedback { margin-bottom: 18px; }
.submitted-score { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.rating-stars { color: #d28a2b; font-size: 25px; letter-spacing: 3px; line-height: 1.5; }
.empty-stars { color: #b3bfcc; }
.submitted-score strong { font-size: 14px; color: #52677c; }
.rating-intro { margin: 0 0 20px; }
.score-label { margin-left: 12px; font-size: 12px; color: #7b8da1; }
.rating-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
@media (max-width: 1000px) { .extension-panel { padding: 22px; } }
@media (max-width: 440px) {
  .extension-panel { padding: 20px 17px; border-radius: 10px; }
  .timeout-summary { padding: 15px; }
  .timeout-summary dl { grid-template-columns: 1fr; gap: 14px; }
  .event-heading { align-items: flex-start; flex-direction: column; }
  .rating-actions .el-button { width: 100%; white-space: normal; min-height: 40px; }
}
</style>
