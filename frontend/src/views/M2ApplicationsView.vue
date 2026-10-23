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
function stateLabel(app: api.Application) { return app.status==='APPROVED' && Date.parse(app.end_at)<=now.value ? '已结束' : labels[app.status] }
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
    <div class="eyebrow">ROOM APPLICATIONS · M2</div>
    <div class="heading-row"><h1>{{ reviewingList?'整室申请审批待办':'整教室预约申请' }}</h1><el-button type="primary" @click="openCreate">提交申请</el-button></div>
    <p class="muted">有效的学生、教师和工作人员均可申请。先由院系或校级管理员审批，再由对应教室管理员审批。</p>
    <el-alert title="只可申请整室类型教室；至少提前两个自然日，整点、同日1至4小时，须在08:00–22:00及实际开放时段内。" type="info" :closable="false" />
    <div class="filters">
      <label v-if="!reviewingList">申请状态<select v-model="status" @change="filterChanged"><option value="">全部状态</option><option v-for="key in ['PENDING_FIRST_APPROVAL','PENDING_ROOM_APPROVAL','RESOURCE_CONFIRMING','APPROVED','REJECTED','WITHDRAWN','AUTO_CANCELLED']" :key="key" :value="key">{{ labels[key] }}</option></select></label>
      <label v-if="scopes.length && !reviewingList">查看范围<select v-model="mine" @change="filterChanged"><option :value="true">本人申请</option><option :value="false">本人及授权范围内申请</option></select></label>
      <el-button :loading="loading" :disabled="busy" @click="load()">刷新列表</el-button>
      <router-link v-if="scopes.length" :to="reviewingList?'/m2/applications':'/m2/approval-tasks'">{{ reviewingList?'查看申请列表':'进入审批待办' }}</router-link>
    </div>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" />
    <div v-loading="loading" class="table-wrap">
      <table v-if="reviewingList && tasks.length"><thead><tr><th>待办 / 申请</th><th>用途与时段（北京时间）</th><th>当前步骤截止</th><th>操作</th></tr></thead><tbody>
        <tr v-for="task in tasks" :key="task.id"><td>第 {{ task.level }} 级审批 · #{{ task.application_id }}<div class="muted">{{ roleLabels[task.required_role] }}</div></td><td class="purpose-cell">{{ task.application.purpose }}<div class="muted">{{ formatTime(task.application.start_at) }} 至 {{ formatTime(task.application.end_at) }}</div></td><td>{{ formatTime(task.deadline_at) }}</td><td><el-button size="small" :disabled="busy" @click="openReview(task.application_id)">查看并审批</el-button></td></tr>
      </tbody></table>
      <table v-else-if="!reviewingList && items.length"><thead><tr><th>申请</th><th>使用时段（北京时间）</th><th>状态 / 截止时间</th><th>操作</th></tr></thead><tbody>
        <tr v-for="app in items" :key="app.id"><td class="purpose-cell"><strong>#{{ app.id }} · {{ app.purpose }}</strong><div class="muted">教室 #{{ app.room_id }} · 申请人 #{{ app.applicant_id }}</div></td><td>{{ formatTime(app.start_at) }}<br />至 {{ formatTime(app.end_at) }}</td><td><span :class="['state',app.status]">{{ stateLabel(app) }}</span><div class="muted">{{ formatTime(app.step_deadline_at) }}</div><div v-if="app.resource_sync_status==='CANCEL_PENDING'" class="warning">已自动撤销，资源清理待确认</div></td><td><el-button size="small" @click="showDetail(app.id)">详情</el-button><el-button v-if="app.applicant_id===auth.user?.id && waiting.includes(app.status)" size="small" :disabled="busy || isExpired(app)" @click="withdraw(app)">撤回</el-button></td></tr>
      </tbody></table>
      <el-empty v-else-if="!loading && !loadError" :description="reviewingList?'当前没有本人可审批的待办':'暂无符合条件的申请'" />
    </div>
    <el-pagination v-model:current-page="page" :page-size="10" :total="total" layout="total,prev,pager,next" class="pagination" @current-change="load()" />

    <el-dialog v-model="createOpen" title="提交整教室申请" width="min(640px,94vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <form @submit.prevent="submitCreate"><label>整室教室<select v-model="form.room_id" required><option value="" disabled>请选择教室</option><option v-for="room in rooms" :key="room.id" :value="room.id">{{ room.name }} · {{ room.code }}（{{ room.department_code || '校级' }}，{{ room.open_time.slice(0,5) }}–{{ room.close_time.slice(0,5) }}）</option></select></label><el-button v-if="rooms.length<roomsTotal" @click="moreRooms()">加载更多教室</el-button>
        <label>申请理由<textarea v-model="form.purpose" rows="4" required maxlength="2000" /></label>
        <label>使用日期（北京时间）<input v-model="form.date" type="date" :min="earliestDate()" required /></label>
        <div class="form-grid"><label>开始时间<select v-model="form.start"><option v-for="hour in hours.slice(0,-1)" :key="hour" :value="hour">{{ hour }}:00</option></select></label><label>结束时间<select v-model="form.end"><option v-for="hour in hours.slice(1)" :key="hour" :value="hour">{{ hour }}:00</option></select></label></div>
        <p class="muted">待审批不占用教室；每一级在步骤创建后的下一自然日23:59:59截止，使用开始更早时以开始时间为界。</p><el-button type="primary" native-type="submit" :loading="busy">提交并进入一级审批</el-button>
      </form>
    </el-dialog>

    <el-dialog v-model="reviewOpen" title="处理当前审批步骤" width="min(620px,94vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <template v-if="current"><h2>申请 #{{ current.id }} · {{ current.room.name }}</h2><p class="purpose">{{ current.purpose }}</p><p>{{ formatTime(current.start_at) }} 至 {{ formatTime(current.end_at) }}（北京时间）</p><p>当前第 {{ current.current_level }} 级 · 截止 {{ formatTime(current.step_deadline_at) }}</p>
        <el-alert v-if="!canReview" title="申请已变化、到期或当前账号无权处理，请刷新列表查看。" type="warning" :closable="false" />
        <form v-else @submit.prevent="submitReview"><label>处理决定<select v-model="decision.passed"><option :value="true">通过本级审批</option><option :value="false">驳回申请</option></select></label><label>审批意见{{ decision.passed?'（选填）':'（必填）' }}<textarea v-model="decision.comment" :required="!decision.passed" rows="3" maxlength="500" /></label><p class="muted">一级通过只进入二级。二级同意后须通过网络取得资源确认；技术失败会继续确认，资源冲突会自动驳回。</p><el-button type="primary" native-type="submit" :loading="busy">确认处理</el-button></form>
      </template>
    </el-dialog>

    <el-dialog v-model="detailOpen" title="申请详情与审批进度" width="min(760px,94vw)">
      <template v-if="current"><h2>申请 #{{ current.id }} · {{ current.room.name }}</h2><span :class="['state',current.status]">{{ stateLabel(current) }}</span><p>{{ formatTime(current.start_at) }} 至 {{ formatTime(current.end_at) }}（北京时间）</p><p class="purpose">{{ current.purpose }}</p>
        <p>申请身份：{{ identityLabels[current.identity_type] || current.identity_type }} · {{ current.department_code }}</p><p v-if="current.final_reason" class="purpose">处理原因：{{ current.final_reason }}</p><el-alert v-if="current.resource_message || current.resource_sync_status==='CANCEL_PENDING'" :title="current.resource_sync_status==='CANCEL_PENDING'?'申请已自动撤销，资源清理仍待确认，后台会继续处理。':current.resource_message || ''" type="warning" :closable="false" />
        <h3>审批步骤</h3><ol class="events"><li v-for="step in current.steps" :key="step.id">第 {{ step.level }} 级 · {{ roleLabels[step.required_role] }} · {{ step.status === 'APPROVED' ? '本级通过' : labels[step.status] }}<div class="muted">截止 {{ formatTime(step.deadline_at) }}<template v-if="step.decided_at"> · 处理于 {{ formatTime(step.decided_at) }}</template></div><p v-if="step.decision_reason" class="purpose">{{ step.decision_reason }}</p></li></ol>
        <el-button v-if="canReview" type="primary" :disabled="busy" @click="openReview(current.id)">处理当前审批</el-button><el-button v-if="current.applicant_id===auth.user?.id && waiting.includes(current.status)" :disabled="busy || isExpired(current)" @click="withdraw(current)">撤回申请</el-button>
        <h3>操作历史</h3><ol class="events"><li v-for="event in current.events" :key="event.id">{{ eventLabels[event.event_type] || event.event_type }}<div class="muted">{{ formatTime(event.created_at) }} · {{ event.actor_type==='SYSTEM'?'系统自动处理':`操作者 #${event.actor_id}` }}</div><p v-if="event.reason" class="purpose">{{ event.reason }}</p></li></ol>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.heading-row { display:flex;justify-content:space-between;align-items:center;gap:16px; }
form { display:grid;gap:18px;margin:24px 0; }
label { display:flex;flex-direction:column;gap:8px;font-size:13px; }
input,select,textarea { font:inherit;color:inherit;padding:10px;min-height:36px;border:1px solid #cbd5e1;border-radius:5px;background:white;max-width:100%; }
input:focus,select:focus,textarea:focus { outline:2px solid #93c5fd; }
.form-grid {display:grid;grid-template-columns:1fr 1fr;gap:16px;}
.purpose-cell {white-space:normal;min-width:200px;max-width:400px;overflow-wrap:anywhere;}
.purpose {white-space:pre-wrap;overflow-wrap:anywhere;}
.events {padding-left:22px;}
.events li {margin:18px 0;line-height:1.7;}
.pagination {margin-top:20px;}
.warning {color:#92400e;font-size:12px;}
.PENDING_FIRST_APPROVAL,.PENDING_ROOM_APPROVAL,.RESOURCE_CONFIRMING {background:#fef3c7;color:#92400e;}
.APPROVED {background:#dcfce7;color:#166534;}
.REJECTED {background:#fee2e2;color:#991b1b;}
@media(max-width:600px) {.form-grid{grid-template-columns:1fr}.heading-row h1{font-size:23px}.heading-row{align-items:flex-start}.filters{gap:12px}}
</style>
