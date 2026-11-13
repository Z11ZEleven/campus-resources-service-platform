<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '../api/m2'
import type { Room } from '../api/resources'
import { errorMessage } from '../api/request'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore(), route = useRoute(), router = useRouter()
const reviewingList = computed(() => route.path.startsWith('/m2/approval-tasks'))
const scopes = ref<api.Scope[]>([])
const items = ref<api.Application[]>([]), tasks = ref<api.Task[]>([])
const page = ref(1), total = ref(0), mine = ref(true), status = ref('')
const loading = ref(false), busy = ref(false), loadError = ref('')
const rooms = ref<Room[]>([]), roomsTotal = ref(0), roomsPage = ref(1)
const createOpen = ref(false), detailOpen = ref(false), reviewOpen = ref(false)
const current = ref<api.Detail | null>(null)
const form = reactive({ room_id: '', date: '', start: '09', end: '10', purpose: '' })
const decision = reactive({ passed: true, comment: '' })
const controller = new AbortController()
const waiting = ['PENDING_FIRST_APPROVAL', 'PENDING_ROOM_APPROVAL']
let alive = true, sequence = 0, detailSequence = 0, poll: ReturnType<typeof setInterval> | undefined
let createFingerprint = '', createKey = '', reviewFingerprint = '', reviewKey = ''
const withdrawKeys = new Map<string, string>()
const now = ref(Date.now())
const labels: Record<string, string> = {
  PENDING_FIRST_APPROVAL: '待一级审批', PENDING_ROOM_APPROVAL: '待二级审批', RESOURCE_CONFIRMING: '资源确认中',
  APPROVED: '已批准', REJECTED: '已驳回', WITHDRAWN: '已撤回', AUTO_CANCELLED: '已自动撤销',
  PENDING: '待处理', CONFIRMING: '资源确认中', CANCELLED: '已取消', EXPIRED: '已过期', ENDED: '已结束',
}
const eventLabels: Record<string, string> = {
  SUBMITTED: '提交申请', FIRST_APPROVED: '一级审批通过', CONFIRM_RESOURCE: '二级同意，确认资源',
  APPROVED: '资源确认并批准', REJECTED: '申请驳回', WITHDRAWN: '申请人撤回', AUTO_CANCELLED: '超时自动撤销',
  REMINDER: '临期提醒', RESOURCE_RETRY: '资源结果重试确认', RESOURCE_CANCELLED: '资源清理完成',
}
const roleLabels: Record<string, string> = { DEPT_ADMIN: '院系管理员', CAMPUS_ADMIN: '校级管理员', ROOM_ADMIN: '教室管理员' }
const identityLabels: Record<string, string> = {STUDENT:'学生',TEACHER:'教师',STAFF:'工作人员'}
const hours = Array.from({length:15},(_,i)=>String(i+8).padStart(2,'0'))
const timeFormat = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'})
function formatTime(value: string | null) { return value ? timeFormat.format(new Date(value)) : '—' }
function schoolDate() {
  const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date())
  const p=(name:string)=>parts.find(item=>item.type===name)?.value
  return `${p('year')}-${p('month')}-${p('day')}`
}
function earliestDate() { return new Date(Date.parse(`${schoolDate()}T00:00:00Z`)+2*86400000).toISOString().slice(0,10) }
function disableEarlyDate(date: Date) { return date.getTime() < new Date(`${earliestDate()}T00:00:00`).getTime() }
function stateLabel(app: api.Application) { return app.status==='APPROVED' && Date.parse(app.end_at)<=now.value ? '已结束' : labels[app.status] }
function stateTagType(value: string): 'success' | 'warning' | 'info' | 'danger' | 'primary' {
  if (value === 'APPROVED') return 'success'
  if (value === 'REJECTED' || value === 'AUTO_CANCELLED') return 'danger'
  if (value === 'WITHDRAWN' || value === 'CANCELLED' || value === 'EXPIRED' || value === 'ENDED') return 'info'
  if (value === 'PENDING_FIRST_APPROVAL' || value === 'PENDING_ROOM_APPROVAL' || value === 'RESOURCE_CONFIRMING' || value === 'PENDING' || value === 'CONFIRMING') return 'warning'
  return 'primary'
}
function isExpired(app: api.Application) { return Date.parse(app.start_at)<=now.value || (!!app.step_deadline_at && Date.parse(app.step_deadline_at)<=now.value) }
const currentStep = computed(()=>current.value?.steps.find(step=>step.level===current.value?.current_level))
const canReview = computed(()=>!!current.value && current.value.applicant_id!==auth.user?.id && waiting.includes(current.value.status)
  && !isExpired(current.value) && !!currentStep.value && scopes.value.some(scope=>scope.role===currentStep.value?.required_role && scope.type===currentStep.value?.scope_type && scope.key===currentStep.value?.scope_key))
function report(error: unknown) { if (alive && !axios.isCancel(error)) ElMessage.error(errorMessage(error)) }

async function load(quiet = false) {
  if (!alive) return
  const currentSequence=++sequence
  if (!quiet) loading.value=true
  loadError.value=''
  try {
    if (reviewingList.value) {
      const result=await api.tasks(page.value,controller.signal)
      if (!alive || currentSequence!==sequence) return
      tasks.value=result.items; items.value=[]; total.value=result.total
    } else {
      const result=await api.applications({page:page.value,mine:mine.value,status:status.value||undefined},controller.signal)
      if (!alive || currentSequence!==sequence) return
      items.value=result.items; tasks.value=[]; total.value=result.total
    }
    if (total.value && !(reviewingList.value?tasks.value.length:items.value.length) && page.value>1) {
      page.value=Math.max(1,Math.ceil(total.value/10)); await load(quiet)
    }
  } catch(error) { if (alive && currentSequence===sequence && !axios.isCancel(error)) loadError.value=errorMessage(error) }
  finally { if (alive && currentSequence===sequence) loading.value=false }
}
function filterChanged() { page.value=1; void load() }
async function showDetail(id:string) {
  if (!alive) return
  const ticket=++detailSequence
  try { const value=await api.detail(id,controller.signal); if (alive && ticket===detailSequence) {current.value=value;detailOpen.value=true} }
  catch(error) { report(error) }
}
async function moreRooms(reset=false) {
  const next=reset?1:roomsPage.value+1
  const result=await api.rooms(next,controller.signal)
  if (!alive) return
  rooms.value=reset?result.items:[...rooms.value,...result.items]; roomsTotal.value=result.total; roomsPage.value=next
}
async function openCreate() {
  try {
    await moreRooms(true)
    if (!alive) return
    Object.assign(form,{room_id:rooms.value[0]?.id??'',purpose:'',date:earliestDate(),start:'09',end:'10'})
    createFingerprint='';createKey='';createOpen.value=true
  } catch(error) {report(error)}
}
async function submitCreate() {
  if (busy.value) return
  const body={room_id:form.room_id,purpose:form.purpose.trim(),start_at:`${form.date}T${form.start}:00:00+08:00`,end_at:`${form.date}T${form.end}:00:00+08:00`}
  const duration=Number(form.end)-Number(form.start)
  if (!body.room_id || !body.purpose || !form.date || duration<1 || duration>4) {ElMessage.warning('请选择教室、用途和1至4小时的同日时段');return}
  const fingerprint=JSON.stringify(body)
  if (fingerprint!==createFingerprint) {createFingerprint=fingerprint;createKey=crypto.randomUUID()}
  busy.value=true
  try {
    const result=await api.create(body,createKey,controller.signal)
    if (!alive) return
    createOpen.value=false;ElMessage.success('已提交，等待一级审批');mine.value=true;status.value='';page.value=1
    await router.push('/m2/applications'); await load(); await showDetail(result.id)
  } catch(error) {report(error)} finally {if (alive) busy.value=false}
}
async function openReview(id:string) {
  const ticket=++detailSequence
  try {
    const value=await api.detail(id,controller.signal)
    if (!alive || ticket!==detailSequence) return
    current.value=value;decision.passed=true;decision.comment='';reviewFingerprint='';reviewKey='';reviewOpen.value=true
  } catch(error) {report(error)}
}
async function submitReview() {
  if (!current.value || !currentStep.value || busy.value || !canReview.value) return
  const comment=decision.comment.trim()
  if (!decision.passed && !comment) {ElMessage.warning('驳回必须填写理由');return}
  const fingerprint=JSON.stringify([currentStep.value.id,decision.passed,comment,current.value.version])
  if (fingerprint!==reviewFingerprint) {reviewFingerprint=fingerprint;reviewKey=crypto.randomUUID()}
  busy.value=true
  try {
    const value=await api.review(currentStep.value.id,decision.passed,comment,current.value.version,reviewKey,controller.signal)
    if (!alive) return
    current.value=value;reviewOpen.value=false;detailOpen.value=true
    if(value.status==='RESOURCE_CONFIRMING') ElMessage.info('资源确认中，页面将自动更新')
    else ElMessage.success(`申请状态：${labels[value.status]}`)
    await load()
  } catch(error) {report(error);await load()} finally {if (alive) busy.value=false}
}
async function withdraw(app:api.Application) {
  if (busy.value) return
  try { await ElMessageBox.confirm('只可撤回待一级或待二级审批的本人申请。确认撤回？','撤回申请',{confirmButtonText:'确认撤回',cancelButtonText:'保留申请'}) }
  catch {return}
  const fingerprint=`${app.id}:${app.version}`
  if(!withdrawKeys.has(fingerprint)) withdrawKeys.set(fingerprint,crypto.randomUUID())
  busy.value=true
  try {
    const value=await api.withdraw(app.id,app.version,withdrawKeys.get(fingerprint)!,controller.signal)
    if(!alive)return
    ElMessage.success(`申请状态：${labels[value.status]}`);await load()
    if(current.value?.id===app.id) await showDetail(app.id)
  } catch(error) {report(error);await load()} finally {if(alive)busy.value=false}
}
watch(()=>route.fullPath,()=>{
  ++detailSequence
  page.value=1;items.value=[];tasks.value=[];total.value=0;detailOpen.value=false;reviewOpen.value=false
  void load()
  if(typeof route.params.id==='string')void showDetail(route.params.id)
})
onMounted(async()=>{
  try {const result=await api.me(controller.signal);if(alive)scopes.value=result.scopes} catch(error){report(error)}
  await load()
  if(typeof route.params.id==='string')await showDetail(route.params.id)
  if (!alive) return
  poll=setInterval(()=>{
    now.value=Date.now()
    if(!busy.value && !createOpen.value && !reviewOpen.value) {
      void load(true)
      if(current.value && detailOpen.value) {
        const id=current.value.id
        void api.detail(id,controller.signal).then(value=>{if(alive && detailOpen.value && !reviewOpen.value && current.value?.id===id && value.version>=current.value.version)current.value=value}).catch(report)
      }
    }
  },5000)
})
onBeforeUnmount(()=>{alive=false;++sequence;++detailSequence;controller.abort();if(poll)clearInterval(poll)})
</script>

<template>
  <section class="m2-page">
    <header class="page-heading"><div><p class="section-kicker">校园空间 / 整室预约</p><h1>{{ reviewingList?'整室申请审批待办':'整教室预约申请' }}</h1><p class="page-description">{{ reviewingList?'集中处理当前账号授权范围内的审批任务。':'提交整室使用申请，并跟踪每一级审批与资源确认。' }}</p></div><el-button type="primary" size="large" @click="openCreate"><span aria-hidden="true">＋</span> 提交申请</el-button></header>
    <el-alert title="只可申请整室类型教室；至少提前两个自然日，整点、同日 1 至 4 小时，须在 08:00–22:00 及实际开放时段内。" type="info" show-icon :closable="false" />
    <section class="applications-panel" aria-label="整室申请列表">
    <div class="list-toolbar">
      <div class="query-controls">
        <el-select v-if="!reviewingList" v-model="status" clearable placeholder="全部申请状态" aria-label="申请状态" @change="filterChanged"><el-option v-for="key in ['PENDING_FIRST_APPROVAL','PENDING_ROOM_APPROVAL','RESOURCE_CONFIRMING','APPROVED','REJECTED','WITHDRAWN','AUTO_CANCELLED']" :key="key" :label="labels[key]" :value="key" /></el-select>
        <el-select v-if="scopes.length && !reviewingList" v-model="mine" placeholder="查看范围" aria-label="查看范围" @change="filterChanged"><el-option label="本人申请" :value="true" /><el-option label="本人及授权范围内申请" :value="false" /></el-select>
      </div>
      <div class="toolbar-actions"><el-button v-if="scopes.length" @click="router.push(reviewingList?'/m2/applications':'/m2/approval-tasks')">{{ reviewingList?'查看申请列表':'进入审批待办' }}</el-button><el-button :loading="loading" :disabled="busy" @click="load()">刷新列表</el-button></div>
    </div>
    <div class="result-caption"><span>{{ loading?'正在读取申请…':`共 ${total} 条记录` }}</span><span>时间按北京时间显示</span></div>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" />
    <div v-loading="loading" class="results-area">
      <el-table v-if="reviewingList && tasks.length" :data="tasks" row-key="id"><el-table-column label="待办 / 申请" min-width="180"><template #default="{ row }"><strong>第 {{ row.level }} 级审批 · #{{ row.application_id }}</strong><div class="secondary">{{ roleLabels[row.required_role] }}</div></template></el-table-column><el-table-column label="用途与时段" min-width="280"><template #default="{ row }"><div class="purpose-cell">{{ row.application.purpose }}</div><div class="secondary">{{ formatTime(row.application.start_at) }} 至 {{ formatTime(row.application.end_at) }}</div></template></el-table-column><el-table-column label="当前步骤截止" min-width="180"><template #default="{ row }">{{ formatTime(row.deadline_at) }}</template></el-table-column><el-table-column label="操作" width="120" align="right"><template #default="{ row }"><el-button link type="primary" :disabled="busy" @click="openReview(row.application_id)">查看并审批</el-button></template></el-table-column></el-table>
      <el-table v-else-if="!reviewingList && items.length" :data="items" row-key="id"><el-table-column label="申请" min-width="280"><template #default="{ row }"><button class="application-title" type="button" @click="showDetail(row.id)">#{{ row.id }} · {{ row.purpose }}</button><div class="secondary">教室 #{{ row.room_id }} · 申请人 #{{ row.applicant_id }}</div></template></el-table-column><el-table-column label="使用时段" min-width="220"><template #default="{ row }">{{ formatTime(row.start_at) }}<div class="secondary">至 {{ formatTime(row.end_at) }}</div></template></el-table-column><el-table-column label="状态 / 截止时间" min-width="190"><template #default="{ row }"><el-tag :type="stateTagType(row.status)" round>{{ stateLabel(row) }}</el-tag><div class="secondary">{{ formatTime(row.step_deadline_at) }}</div><div v-if="row.resource_sync_status==='CANCEL_PENDING'" class="warning">资源清理待确认</div></template></el-table-column><el-table-column label="操作" width="140" align="right"><template #default="{ row }"><el-button link type="primary" @click="showDetail(row.id)">详情</el-button><el-button v-if="row.applicant_id===auth.user?.id && waiting.includes(row.status)" link type="danger" :disabled="busy || isExpired(row)" @click="withdraw(row)">撤回</el-button></template></el-table-column></el-table>
      <el-empty v-else-if="!loading && !loadError" :description="reviewingList?'当前没有本人可审批的待办':'暂无符合条件的申请'" />
    </div>
    <el-pagination v-model:current-page="page" :page-size="10" :total="total" layout="total,prev,pager,next" class="pagination" @current-change="load()" />
    </section>

    <el-dialog v-model="createOpen" title="提交整教室申请" width="min(640px,94vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <el-form label-position="top" @submit.prevent="submitCreate"><el-form-item label="整室教室" required><el-select v-model="form.room_id" filterable placeholder="请选择教室"><el-option v-for="room in rooms" :key="room.id" :label="`${room.name} · ${room.code}（${room.department_code || '校级'}，${room.open_time.slice(0,5)}–${room.close_time.slice(0,5)}）`" :value="room.id" /></el-select><el-button v-if="rooms.length<roomsTotal" class="more-rooms" text type="primary" @click="moreRooms()">加载更多教室</el-button></el-form-item>
        <el-form-item label="申请理由" required><el-input v-model="form.purpose" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="请说明课程、活动或会议用途" /></el-form-item>
        <el-form-item label="使用日期（北京时间）" required><el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" :disabled-date="disableEarlyDate" placeholder="选择日期" /></el-form-item>
        <div class="form-grid"><el-form-item label="开始时间" required><el-select v-model="form.start"><el-option v-for="hour in hours.slice(0,-1)" :key="hour" :label="`${hour}:00`" :value="hour" /></el-select></el-form-item><el-form-item label="结束时间" required><el-select v-model="form.end"><el-option v-for="hour in hours.slice(1)" :key="hour" :label="`${hour}:00`" :value="hour" /></el-select></el-form-item></div>
        <p class="muted">待审批不占用教室；每一级在步骤创建后的下一自然日 23:59:59 截止，使用开始更早时以开始时间为界。</p><el-button type="primary" native-type="submit" :loading="busy">提交并进入一级审批</el-button>
      </el-form>
    </el-dialog>

    <el-dialog v-model="reviewOpen" title="处理当前审批步骤" width="min(620px,94vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <template v-if="current"><h2>申请 #{{ current.id }} · {{ current.room.name }}</h2><p class="purpose">{{ current.purpose }}</p><p>{{ formatTime(current.start_at) }} 至 {{ formatTime(current.end_at) }}（北京时间）</p><p>当前第 {{ current.current_level }} 级 · 截止 {{ formatTime(current.step_deadline_at) }}</p>
        <el-alert v-if="!canReview" title="申请已变化、到期或当前账号无权处理，请刷新列表查看。" type="warning" :closable="false" />
        <el-form v-else label-position="top" @submit.prevent="submitReview"><el-form-item label="处理决定"><el-select v-model="decision.passed"><el-option label="通过本级审批" :value="true" /><el-option label="驳回申请" :value="false" /></el-select></el-form-item><el-form-item :label="`审批意见${decision.passed?'（选填）':'（必填）'}`" :required="!decision.passed"><el-input v-model="decision.comment" type="textarea" :rows="3" maxlength="500" show-word-limit /></el-form-item><p class="muted">一级通过只进入二级。二级同意后须通过网络取得资源确认；技术失败会继续确认，资源冲突会自动驳回。</p><el-button type="primary" native-type="submit" :loading="busy">确认处理</el-button></el-form>
      </template>
    </el-dialog>

    <el-dialog v-model="detailOpen" title="申请详情与审批进度" width="min(760px,94vw)">
      <template v-if="current"><div class="detail-heading"><h2>申请 #{{ current.id }} · {{ current.room.name }}</h2><el-tag :type="stateTagType(current.status)" round>{{ stateLabel(current) }}</el-tag></div><p>{{ formatTime(current.start_at) }} 至 {{ formatTime(current.end_at) }}（北京时间）</p><p class="purpose">{{ current.purpose }}</p>
        <p>申请身份：{{ identityLabels[current.identity_type] || current.identity_type }} · {{ current.department_code }}</p><p v-if="current.final_reason" class="purpose">处理原因：{{ current.final_reason }}</p><el-alert v-if="current.resource_message || current.resource_sync_status==='CANCEL_PENDING'" :title="current.resource_sync_status==='CANCEL_PENDING'?'申请已自动撤销，资源清理仍待确认，后台会继续处理。':current.resource_message || ''" type="warning" :closable="false" />
        <h3>审批步骤</h3><ol class="events"><li v-for="step in current.steps" :key="step.id">第 {{ step.level }} 级 · {{ roleLabels[step.required_role] }} · {{ step.status === 'APPROVED' ? '本级通过' : labels[step.status] }}<div class="muted">截止 {{ formatTime(step.deadline_at) }}<template v-if="step.decided_at"> · 处理于 {{ formatTime(step.decided_at) }}</template></div><p v-if="step.decision_reason" class="purpose">{{ step.decision_reason }}</p></li></ol>
        <el-button v-if="canReview" type="primary" :disabled="busy" @click="openReview(current.id)">处理当前审批</el-button><el-button v-if="current.applicant_id===auth.user?.id && waiting.includes(current.status)" :disabled="busy || isExpired(current)" @click="withdraw(current)">撤回申请</el-button>
        <h3>操作历史</h3><ol class="events"><li v-for="event in current.events" :key="event.id">{{ eventLabels[event.event_type] || event.event_type }}<div class="muted">{{ formatTime(event.created_at) }} · {{ event.actor_type==='SYSTEM'?'系统自动处理':`操作者 #${event.actor_id}` }}</div><p v-if="event.reason" class="purpose">{{ event.reason }}</p></li></ol>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.m2-page{display:grid;gap:22px}.page-heading{display:flex;justify-content:space-between;align-items:flex-end;gap:20px}.section-kicker{margin:0 0 7px;color:#2563eb;font-size:12px;font-weight:700;letter-spacing:.12em}.page-heading h1{margin:0;font-size:30px}.page-description{margin:9px 0 0;color:#64748b}.applications-panel{padding:22px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;box-shadow:0 8px 24px rgba(15,23,42,.04)}.list-toolbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:16px}.query-controls,.toolbar-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.query-controls :deep(.el-select){width:230px}.result-caption{display:flex;justify-content:space-between;margin-bottom:10px;color:#64748b;font-size:13px}.secondary{margin-top:5px;color:#64748b;font-size:12px}.application-title{display:block;max-width:100%;padding:0;border:0;background:none;color:#0f172a;font:inherit;font-weight:650;text-align:left;cursor:pointer}.application-title:hover{color:#2563eb}.results-area{min-height:120px}.el-form{display:grid;margin-top:20px}.el-form :deep(.el-select),.el-form :deep(.el-date-editor){width:100%}.more-rooms{margin-left:8px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.detail-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.detail-heading h2{margin:0}
.purpose-cell {white-space:normal;min-width:200px;max-width:400px;overflow-wrap:anywhere;}
.purpose {white-space:pre-wrap;overflow-wrap:anywhere;}
.events {padding-left:22px;}
.events li {margin:18px 0;line-height:1.7;}
.pagination {justify-content:flex-end;margin-top:20px;}
.warning {color:#92400e;font-size:12px;}
@media(max-width:700px){.page-heading{align-items:flex-start}.page-heading h1{font-size:25px}.applications-panel{padding:16px}.list-toolbar{align-items:stretch;flex-direction:column}.query-controls,.toolbar-actions{width:100%}.query-controls :deep(.el-select){width:100%}.toolbar-actions .el-button{flex:1}.form-grid{grid-template-columns:1fr}.result-caption{gap:12px}.detail-heading{align-items:flex-start;flex-direction:column}}
</style>
