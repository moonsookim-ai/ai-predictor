/**
 * 临时存储待上传的文件和需求
 * 用于首页点击启动引擎后立即跳转，在Process页面再进行API调用
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  simulationRequirement: '',
  businessBrief: null,
  ticket: null,
  isPending: false
})

export function setPendingUpload(files, requirement, businessBrief = null, ticket = null) {
  state.files = files
  state.simulationRequirement = requirement
  state.businessBrief = businessBrief
  state.ticket = ticket
  state.isPending = true
}

export function getPendingUpload() {
  return {
    files: state.files,
    simulationRequirement: state.simulationRequirement,
    businessBrief: state.businessBrief,
    ticket: state.ticket,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.simulationRequirement = ''
  state.businessBrief = null
  state.ticket = null
  state.isPending = false
  // 예측이 만들어지면 실행권은 소진됐다 - 다음 예측에 다시 내지 않게 지운다
  try { sessionStorage.removeItem('predictorTicket') } catch (e) {}
}

export default state
