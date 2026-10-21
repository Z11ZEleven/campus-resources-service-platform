<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { createWorkOrder, type CreateWorkOrderBody, type WorkOrder, type WorkOrderCategory } from '../../api/m3'
import { errorMessage } from '../../api/request'
import type { Room } from '../../api/resources'
import { categoryOptions } from '../../utils/m3'

const props = defineProps<{ modelValue: boolean; rooms: Room[]; roomsLoading: boolean; roomsError: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; created: [order: WorkOrder]; 'reload-rooms': [] }>()
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const formRef = ref<FormInstance>()
const form = reactive({ room_id: '', title: '', description: '', category: '' as WorkOrderCategory | '' })
const submitting = ref(false)
const failure = ref('')
const uncertain = ref(false)
const submittedBody = ref<CreateWorkOrderBody | null>(null)
const activeRooms = computed(() => props.rooms.filter(room => room.is_active))
let alive = true
const rules: FormRules = {
  room_id: [{ required: true, message: '请选择故障所在教室', trigger: 'change' }],
  category: [{ required: true, message: '请选择故障分类', trigger: 'change' }],
  title: [{ validator: (_rule, value: string, callback) => value.trim() && value.trim().length <= 100 ? callback() : callback(new Error('请填写 1～100 字的故障标题')), trigger: 'blur' }],
  description: [{ validator: (_rule, value: string, callback) => value.trim() && value.trim().length <= 2000 ? callback() : callback(new Error('请填写 1～2000 字的故障描述')), trigger: 'blur' }],
}
watch([() => props.modelValue, activeRooms], () => {
  if (props.modelValue && !form.room_id && activeRooms.value.length === 1) form.room_id = activeRooms.value[0].id
})

async function submit() {
  if (submitting.value) return
  submitting.value = true
  try {
    if (!uncertain.value && !(await formRef.value?.validate().catch(() => false))) return
    failure.value = ''
    if (!uncertain.value || !submittedBody.value) {
      submittedBody.value = { room_id: form.room_id, category: form.category as WorkOrderCategory, title: form.title.trim(), description: form.description.trim(), request_key: crypto.randomUUID() }
    }
    const order = await createWorkOrder(submittedBody.value)
    if (!alive) return
    uncertain.value = false
    submittedBody.value = null
    Object.assign(form, { room_id: '', title: '', description: '', category: '' })
    formRef.value?.resetFields()
    ElMessage.success('故障已上报')
    visible.value = false
    emit('created', order)
  } catch (error) {
    if (!alive) return
    uncertain.value = axios.isAxiosError(error) && (!error.response || error.response.status >= 500)
    failure.value = uncertain.value ? '尚未确认上报结果。请保留当前内容，点击“重试本次上报”；重复点击不会新建另一条工单。' : errorMessage(error)
  } finally { if (alive) submitting.value = false }
}
function beforeClose(done: () => void) { if (!submitting.value) done() }
onBeforeUnmount(() => { alive = false })
</script>

<template>
  <el-dialog v-model="visible" title="上报故障" width="600px" class="m3-create-dialog" :close-on-click-modal="false" :close-on-press-escape="!submitting" :show-close="!submitting" :before-close="beforeClose" append-to-body>
    <p class="dialog-intro">请选择故障所在教室，描述具体情况，方便维修人员判断。</p>
    <el-alert v-if="failure" :title="failure" :type="uncertain ? 'warning' : 'error'" :closable="false" class="form-alert" show-icon />
    <div v-if="roomsError" class="room-load-error">教室列表加载失败：{{ roomsError }} <el-button link type="primary" @click="emit('reload-rooms')">重新加载</el-button></div>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="submitting || uncertain" @submit.prevent="submit">
      <el-form-item label="故障教室" prop="room_id">
        <el-select v-model="form.room_id" filterable :loading="roomsLoading" placeholder="选择教室" class="full-width" aria-label="故障教室">
          <el-option v-for="room in activeRooms" :key="room.id" :label="`${room.code} · ${room.name}`" :value="room.id" />
        </el-select>
        <span v-if="!roomsLoading && !roomsError && !activeRooms.length" class="field-note">暂无可上报的教室。</span>
      </el-form-item>
      <el-form-item label="故障分类" prop="category">
        <el-select v-model="form.category" placeholder="选择故障分类" class="full-width" aria-label="故障分类"><el-option v-for="option in categoryOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select>
      </el-form-item>
      <el-form-item label="故障标题" prop="title"><el-input v-model="form.title" placeholder="例如：靠窗空调无法启动" maxlength="100" show-word-limit aria-label="故障标题" /></el-form-item>
      <el-form-item label="故障描述" prop="description"><el-input v-model="form.description" type="textarea" :rows="4" placeholder="故障发生在哪里？有什么现象？请尽量描述清楚。" maxlength="2000" show-word-limit aria-label="故障描述" /></el-form-item>
    </el-form>
    <template #footer><el-button :disabled="submitting" @click="visible = false">取消</el-button><el-button type="primary" :loading="submitting" :disabled="!uncertain && (roomsLoading || !!roomsError || !activeRooms.length)" @click="submit">{{ uncertain ? '重试本次上报' : '提交报修' }}</el-button></template>
  </el-dialog>
</template>

<style scoped>
.dialog-intro { color: #64748b; font-size: 13px; line-height: 1.8; margin: 0 0 22px; }.form-alert { margin-bottom: 18px; }
.field-note { color: #8391a0; font-size: 12px; }.room-load-error { color: #a56d1e; font-size: 13px; margin-bottom: 16px; }
:deep(.el-form-item__label) { color: #334b61; font-weight: 500; }:deep(.el-textarea__inner) { min-height: 120px; }
</style>
