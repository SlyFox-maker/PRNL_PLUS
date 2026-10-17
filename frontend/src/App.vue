<script setup lang="ts">
import { vChanged } from './telemetry'
import { vBoot, vTerminal } from './atmosphere'
import BrandWordmark from './components/BrandWordmark.vue'
import ThemedSelect from './components/ThemedSelect.vue'
import QRCode from 'qrcode'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Activity, ArrowDownLeft, ArrowUpRight, CircleHelp, Copy, ImageDown, Globe2, Info, KeyRound, Layers3, LogOut, Plus, Radio, Search, Settings2, ShieldCheck, Square, Terminal, Trash2, X, ArrowUpRight as External, RefreshCw, ChevronRight, Check, Play, AlertTriangle, Github, Monitor, QrCode, Pencil, BookOpenText, Download, AlertCircle, UserRound, RotateCcw } from 'lucide-vue-next'
type Account = { id: string; name: string; platform: string; portal: string; has_cookies: boolean; has_credentials: boolean; updated_at: string; browser_open: boolean; desktop_creator_open: boolean }
type Session = { id: string; name: string; account_id: string; state: string; resources: string; has_link: boolean; telemetry: string; peer: string; rx_bytes: number|null; tx_bytes: number|null; rx_rate: number|null; tx_rate: number|null; active_connections: number|null; recipient_type:string; recipient_label:string; recipient_value:string; vpn_enabled:boolean; vpn_default_enabled:boolean; vpn_configured:boolean; vpn_effective:boolean; matrix_user_id:string; matrix_display_name:string; matrix_has_avatar:boolean; matrix_retry_exhausted:boolean }
type Log = { id: number; level: string; message: string; at: string }
const accounts = ref<Account[]>([]), sessions = ref<Session[]>([]), platforms = ref<{id:string;label:string}[]>([])
function savedAdminUsername() { try { return localStorage.getItem('wlp-admin-username')||'admin' } catch { return 'admin' } }
const tab = ref('sessions'), authed = ref(false), loginUsername=ref(savedAdminUsername()), password = ref(''), error = ref(''), notice = ref(''), busy = ref(false), connected = ref(false), updated = ref(''), search = ref('')
const refreshIntervals = [{value:'60',label:'60 секунд'},{value:'30',label:'30 секунд'},{value:'15',label:'15 секунд'},{value:'5',label:'5 секунд'},{value:'1',label:'Почти live · 1 сек'},{value:'0.5',label:'Почти live · 0,5 сек'}]
function savedRefreshInterval() { try { const saved=localStorage.getItem('wlp-refresh-interval');return refreshIntervals.some(option=>option.value===saved)?saved!:'1' } catch { return '1' } }
const refreshInterval = ref(savedRefreshInterval())
type StorageStats = {retention_days:number;session_logs_enabled:boolean;domain_history_enabled:boolean;logs:{records:number;bytes:number;live_bytes:number};destinations:{records:number;bytes:number;live_bytes:number};database_bytes:number}
const storageStats = ref<StorageStats|null>(null), retentionDays = ref('30'), storageBusy = ref(false), storageLoading = ref(false)
const retentionOptions = [{value:'7',label:'7 дней'},{value:'14',label:'14 дней'},{value:'30',label:'30 дней'},{value:'60',label:'60 дней'},{value:'90',label:'90 дней'},{value:'180',label:'180 дней'},{value:'365',label:'365 дней'}]
const storageError = ref('')
type VpnStatus = {configured:boolean;enabled:boolean;running:boolean;host:string;port:number;transport:string;name:string;active_sessions:number;error:string}
const settingsPane = ref('logs'), vpnStatus = ref<VpnStatus|null>(null), vpnUri = ref(''), vpnBusy = ref(false), vpnError = ref('')
type MatrixStatus = {configured:boolean;user_id:string;homeserver:string;sync_running:boolean;error:string;greeting:string}
type PanelStatus = {configured:boolean;ip:string;bind_ip:string;http_port:number;https_port:number;url:string;local_url:string;access_path:string;ready:boolean;auto_renew:boolean;expires_at:string;fingerprint:string;ca_fingerprint:string;error:string}
type MatrixUser = {user_id:string;display_name:string;has_avatar:boolean;unverified?:boolean}
const matrixStatus = ref<MatrixStatus|null>(null), matrixHomeserver=ref(''), matrixUsername=ref(''), matrixPassword=ref(''), matrixGreeting=ref(''), matrixBusy=ref(false), matrixLoading=ref(false), matrixError=ref('')
const panelStatus=ref<PanelStatus|null>(null), panelIp=ref(''), panelBusy=ref(false), panelError=ref('')
const panelPathBusy=ref(false), panelCertificateBusy=ref(false)
const initialSetupRequired=ref(false)
const adminUsername=ref('admin'), adminCurrentPassword=ref(''), adminNewPassword=ref(''), adminConfirmPassword=ref(''), adminBusy=ref(false), adminError=ref('')
let panelPollTimer:ReturnType<typeof setTimeout>|null=null
const matrixSearch=ref(''), matrixUsers=ref<MatrixUser[]>([]), matrixSearching=ref(false), matrixSearchDone=ref(false), matrixSearchError=ref('')
const matrixAvatarUnavailable=ref<Record<string,boolean>>({})
let matrixSearchTimer:ReturnType<typeof setTimeout>|null=null
let matrixSearchRequest=0
type VpnTestResult = {ok:boolean;target:string;http_status:number;latency_ms:number}
const vpnTestBusy = ref(false), vpnTestResult = ref<VpnTestResult|null>(null), vpnTestError = ref('')
const refreshIntervalLabel = computed(() => refreshInterval.value==='0.5'?'0,5 СЕК':`${refreshInterval.value} СЕК`)
const streamState = ref<'connecting'|'connected'|'reconnecting'|'disconnected'>('connecting')
const streamLabel = computed(() => ({ connecting: 'Получаем состояние', connected: 'Обновление активно', reconnecting: 'Повтор запроса состояния', disconnected: 'Нет связи с сервером' })[streamState.value])
const modal = ref(''), selected = ref<Account|null>(null)
const renameTarget = ref<Session|null>(null), renameValue = ref('')
const recipientTarget = ref<Session|null>(null)
const selectedLogSession = ref<Session|null>(null), logs = ref<Log[]>([]), logLevel = ref('all'), logSince = ref(''), logUntil = ref(''), logHasMore = ref(false), logBusy = ref(false), logError = ref('')
type Destination = {id:number; domain:string; port:number; at:string}
const selectedDestinationSession = ref<Session|null>(null), destinations = ref<Destination[]>([]), destinationDomain = ref(''), destinationSince = ref(''), destinationUntil = ref(''), destinationHasMore = ref(false), destinationBusy = ref(false), destinationError = ref('')
const selectedLinkSession = ref<Session|null>(null)
const joinLink = ref(''), qrDataUrl = ref(''), qrImageBlob = ref<Blob|null>(null), qrLoading = ref(false), copyBusy = ref(false), copyStatus = ref('')
const dnsCopied = ref(false)
const accountForm = ref({name:'',platform:'telemost',portal:''})
const credentialForm = ref({email:'',password:''})
const sessionForm = ref({name:'',account_id:'',resources:'default',room:'',recipient_type:'',recipient_label:'',recipient_value:'',matrix_user_id:'',matrix_display_name:'',matrix_has_avatar:false})
const recipientOptions = [{value:'',label:'Не указан'},{value:'telegram',label:'Telegram'},{value:'whatsapp',label:'WhatsApp'},{value:'other',label:'Другое'}]
const cookieText = ref(''), cookieZipBase64 = ref(''), cookieZipName = ref(''), remoteText = ref(''), frame = ref(''), frameError = ref(''), desktopCookieStatus = ref(''), remoteImage = ref<HTMLImageElement|null>(null)
let stateTimer: ReturnType<typeof setInterval>|null = null, statePollInFlight=false, frameTimer: ReturnType<typeof setTimeout> | null = null, logTimer: ReturnType<typeof setTimeout>|null = null
let logRequest = 0
let destinationRequest = 0
let previousFocus: HTMLElement|null = null
watch(modal, async (value, old) => {
 if(value && !old) previousFocus=document.activeElement as HTMLElement
 await nextTick()
 if(value) document.querySelector<HTMLElement>('.dialog input, .dialog button')?.focus()
 else previousFocus?.focus()
})
const filtered = computed(() => sessions.value.filter(s => s.name.toLowerCase().includes(search.value.toLowerCase())))
const sessionGroups = computed(() => {
 const groups = new Map<string,{id:string;name:string;platform:string;sessions:Session[]}>()
 for (const s of filtered.value) {
  const a = account(s.account_id), id = s.account_id || 'unknown'
  if (!groups.has(id)) groups.set(id,{id,name:a?.name||'Аккаунт недоступен',platform:a?.platform||'',sessions:[]})
  groups.get(id)!.sessions.push(s)
 }
 return [...groups.values()]
})
const collapsedAccounts = ref<Record<string,boolean>>({})
const workingSessions = computed(() => sessions.value.filter(s => ['running','starting'].includes(s.state)).length)
const interruptedSessions = computed(() => sessions.value.filter(s => s.state === 'interrupted'))
const matrixRetryExhaustedSessions = computed(() => sessions.value.filter(s => s.matrix_retry_exhausted))
const connectedJoiners = computed(() => sessions.value.filter(s => s.peer === 'recent').length)
const totalTraffic = computed(() => sessions.value.reduce((sum,s) => sum + (s.rx_bytes||0) + (s.tx_bytes||0), 0))
const averageSpeed = computed(() => {
 const live = sessions.value.filter(s => s.state === 'running' && s.telemetry === 'live' && s.rx_rate !== null && s.tx_rate !== null)
 return live.length ? live.reduce((sum,s) => sum + (s.rx_rate||0) + (s.tx_rate||0), 0) / live.length : 0
})
const configured = computed(() => accounts.value.filter(a => a.has_cookies || a.has_credentials).length)
const restartTargets = ref<string[]>([]), restartLinks = ref<Record<string,string>>({}), restartFailures = ref<Record<string,string>>({}), restartBusy = ref(false)
const restartSessions = computed(() => restartTargets.value.map(id => sessions.value.find(s => s.id===id)).filter((s):s is Session => Boolean(s)))
const restartReadyCount = computed(() => restartTargets.value.filter(id => Boolean(restartLinks.value[id])).length)
function toggleAccountGroup(id:string) { collapsedAccounts.value[id]=!collapsedAccounts.value[id] }
function setAccountGroups(collapsed:boolean) { for(const group of sessionGroups.value) collapsedAccounts.value[group.id]=collapsed }
function account(id:string) { return accounts.value.find(a=>a.id===id) }
function platform(id:string) { return platforms.value.find(p=>p.id===id)?.label || id }
function date(value:string) { return new Date(value).toLocaleTimeString('ru-RU') }
function dateTime(value:string) { return new Date(value).toLocaleString('ru-RU') }
function formatBytes(value:number|null|undefined) {
 const bytes=Math.max(0,value||0)
 if(bytes<1024) return `${Math.round(bytes)} Б`
 const units=['КБ','МБ','ГБ','ТБ']; let amount=bytes/1024, unit=0
 while(amount>=1024&&unit<units.length-1){amount/=1024;unit++}
 return `${amount.toFixed(amount<10?2:1)} ${units[unit]}`
}
async function loadStorage() {
 if(storageLoading.value)return
 storageLoading.value=true;storageError.value=''
 try {const data=await api('/settings/storage') as StorageStats;storageStats.value=data;retentionDays.value=String(data.retention_days)}
 catch(e) {storageError.value=(e as Error).message}
 finally {storageLoading.value=false}
}
async function saveRetention(value:string) {
 retentionDays.value=value;storageBusy.value=true;storageError.value=''
 try {storageStats.value=await api('/settings/storage','PUT',{retention_days:Number(value)}) as StorageStats;notice.value=`Срок хранения журналов установлен: ${value} дн.`}
 catch(e) {storageError.value=(e as Error).message}
 finally {storageBusy.value=false}
}
async function saveStorageFlag(key:'session_logs_enabled'|'domain_history_enabled', value:boolean) {
 if(storageBusy.value)return
 storageBusy.value=true;storageError.value=''
 try {storageStats.value=await api('/settings/storage','PUT',{[key]:value}) as StorageStats;notice.value=key==='session_logs_enabled'?(value?'Журнал Creator включён.':'Новые записи журнала Creator отключены.'):(value?'История доменов включена.':'Запись новых переходов по доменам отключена.')}
 catch(e) {storageError.value=(e as Error).message}
 finally {storageBusy.value=false}
}
async function cleanStorage() {
 if(storageBusy.value)return
 storageBusy.value=true;storageError.value=''
 try {const result=await api('/settings/storage/purge','POST') as {logs:number;destinations:number;has_more:boolean;storage:StorageStats};storageStats.value=result.storage;retentionDays.value=String(result.storage.retention_days);notice.value=`Удалено записей: журнал ${result.logs}, переходы ${result.destinations}.${result.has_more?' Очистка продолжится автоматически.':''}`}
 catch(e) {storageError.value=(e as Error).message}
 finally {storageBusy.value=false}
}
async function loadVpn() {
 vpnError.value=''
 try { vpnStatus.value=await api('/settings/vpn') as VpnStatus }
 catch(e) { vpnError.value=(e as Error).message }
}
async function loadMatrix() {
 matrixLoading.value=true;matrixError.value=''
 try {matrixStatus.value=await api('/settings/matrix') as MatrixStatus;if(matrixStatus.value.homeserver)matrixHomeserver.value=matrixStatus.value.homeserver;matrixGreeting.value=matrixStatus.value.greeting||''}
 catch(e) {matrixError.value=(e as Error).message}
 finally {matrixLoading.value=false}
}
async function loadPanel() {
 panelError.value=''
 try {panelStatus.value=await api('/settings/panel') as PanelStatus;if(panelStatus.value.ip&&!panelIp.value)panelIp.value=panelStatus.value.ip}
 catch(e) {panelError.value=(e as Error).message}
}
async function loadAdminSettings() {
 adminError.value=''
 try {const data=await api('/settings/admin') as {username:string;initial_setup_required?:boolean};adminUsername.value=data.username;initialSetupRequired.value=Boolean(data.initial_setup_required)}
 catch(e) {adminError.value=(e as Error).message}
}
async function saveAdminSettings() {
 if(adminBusy.value)return
 adminError.value=''
 if(adminNewPassword.value&&adminNewPassword.value.length<16){adminError.value='Новый пароль должен содержать не менее 16 символов';return}
 if(adminNewPassword.value!==adminConfirmPassword.value){adminError.value='Новые пароли не совпадают';return}
 adminBusy.value=true
 try {
  const result=await api('/settings/admin','PUT',{current_password:adminCurrentPassword.value,username:adminUsername.value.trim(),new_password:adminNewPassword.value}) as {username:string;initial_setup_required?:boolean}
  initialSetupRequired.value=Boolean(result.initial_setup_required)
  adminUsername.value=result.username;loginUsername.value=result.username;try{localStorage.setItem('wlp-admin-username',result.username)}catch{};adminCurrentPassword.value='';adminNewPassword.value='';adminConfirmPassword.value=''
  notice.value='Логин и пароль панели обновлены. Остальные активные входы сброшены.'
 } catch(e) {adminError.value=(e as Error).message}
 finally {adminBusy.value=false}
}
async function savePanel() {
 if(panelBusy.value||!panelIp.value.trim())return
 panelBusy.value=true;panelError.value=''
 try {
  panelStatus.value=await api('/settings/panel','PUT',{ip:panelIp.value.trim()}) as PanelStatus
  panelIp.value=panelStatus.value.ip
  notice.value='Адрес сохранён. HTTPS-сертификат обновляется; это может занять несколько секунд.'
  if(panelPollTimer)clearTimeout(panelPollTimer)
  let remaining=12
  const poll=async()=>{await loadPanel();if(!panelStatus.value?.ready&&remaining-->0)panelPollTimer=setTimeout(poll,2500)}
  panelPollTimer=setTimeout(poll,2500)
 } catch(e) {panelError.value=(e as Error).message}
 finally {panelBusy.value=false}
}
async function rotatePanelPath() {
 if(panelPathBusy.value||!confirm('Сменить секретный адрес панели? Старый адрес сразу перестанет открываться.'))return
 panelPathBusy.value=true;panelError.value=''
 try {
  panelStatus.value=await api('/settings/panel/path','POST',{}) as PanelStatus
  notice.value='Секретный адрес сменён. Открываем панель по новому адресу…'
  const path=panelStatus.value.access_path
  stopStatePolling()
  for(let attempt=0;attempt<40;attempt++){
   try {const ready=await fetch(`${window.location.origin}/${path}/api/health`,{cache:'no-store'});if(ready.ok){window.location.assign(`${window.location.origin}/${path}/`);return}}catch{}
   await new Promise(resolve=>setTimeout(resolve,250))
  }
  panelError.value='Новый путь сохранён, но прокси ещё не ответил. Открой адрес панели вручную после обновления прокси.'
  panelPathBusy.value=false
 } catch(e) {panelError.value=(e as Error).message;panelPathBusy.value=false}
}
async function copyPanelAddress() {
 if(!panelStatus.value)return
 try {await navigator.clipboard.writeText(panelStatus.value.url);notice.value='Адрес панели скопирован.'}
 catch {panelError.value='Не удалось скопировать адрес. Скопируй его вручную из поля выше.'}
}
async function renewPanelCertificate() {
 if(panelCertificateBusy.value||!panelStatus.value)return
 panelCertificateBusy.value=true;panelError.value=''
 try {
  const previousFingerprint=panelStatus.value.fingerprint
  await api('/settings/panel/certificate','POST',{})
  let renewed=false
  for(let attempt=0;attempt<40;attempt++){
   await new Promise(resolve=>setTimeout(resolve,500))
   try {
    const next=await api('/settings/panel') as PanelStatus
    panelStatus.value=next
    if(next.ready&&next.fingerprint&&next.fingerprint!==previousFingerprint){renewed=true;break}
   } catch {}
  }
  if(!renewed)throw new Error('Перевыпуск не завершился за 20 секунд. Обнови статус позже.')
  notice.value='Сертификат перевыпущен и подписан локальным центром PRNL.'
 } catch(e) {panelError.value=(e as Error).message}
 finally {panelCertificateBusy.value=false}
}
async function downloadPanelCa() {
 try {
  const response=await fetch(apiUrl('/api/settings/panel/ca'))
  if(!response.ok)throw new Error('Не удалось скачать сертификат центра подписи')
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a')
  link.href=url;link.download='prnl-panel-ca.crt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  notice.value='Корневой сертификат скачан. Сверь отпечаток перед установкой в доверенные.'
 } catch(e) {panelError.value=(e as Error).message}
}
async function loginMatrix() {
 if(matrixBusy.value)return
 matrixBusy.value=true;matrixError.value=''
 try {matrixStatus.value=await api('/settings/matrix/login','POST',{homeserver:matrixHomeserver.value.trim(),username:matrixUsername.value.trim(),password:matrixPassword.value}) as MatrixStatus;matrixPassword.value='';matrixUsername.value='';notice.value=`Matrix подключён: ${matrixStatus.value.user_id}`}
 catch(e) {matrixError.value=(e as Error).message}
 finally {matrixBusy.value=false}
}
async function logoutMatrix() {
 if(matrixBusy.value)return
 matrixBusy.value=true;matrixError.value=''
 try {matrixStatus.value=await api('/settings/matrix','DELETE') as MatrixStatus;matrixUsers.value=[];notice.value='Matrix интеграция отключена. Привязанные Matrix ID сохранены у сессий.'}
 catch(e) {matrixError.value=(e as Error).message}
 finally {matrixBusy.value=false}
}
async function saveMatrixGreeting() {
 if(matrixBusy.value)return
 matrixBusy.value=true;matrixError.value=''
 try {const saved=await api('/settings/matrix/greeting','PUT',{greeting:matrixGreeting.value}) as {greeting:string};matrixGreeting.value=saved.greeting;notice.value='Приветствие Matrix сохранено. Оно отправится при первом создании комнаты для сессии.'}
 catch(e) {matrixError.value=(e as Error).message}
 finally {matrixBusy.value=false}
}
function searchMatrixDirectory() {
 if(matrixSearchTimer)clearTimeout(matrixSearchTimer)
 const request=++matrixSearchRequest
 matrixSearchError.value='';matrixUsers.value=[];matrixSearchDone.value=false
 const query=matrixSearch.value.trim()
 if(!matrixStatus.value?.configured||query.length<2){matrixSearching.value=false;return}
 matrixSearchTimer=setTimeout(async()=>{
  matrixSearching.value=true
  try {const result=await api(`/settings/matrix/users?q=${encodeURIComponent(query)}`) as {results:MatrixUser[]};if(request===matrixSearchRequest)matrixUsers.value=result.results.length?result.results:/^@[^:\s]+:[^\s]+$/.test(query)?[{user_id:query,display_name:query,has_avatar:false,unverified:true}]:[]}
  catch(e) {if(request===matrixSearchRequest)matrixSearchError.value=(e as Error).message}
  finally {if(request===matrixSearchRequest){matrixSearching.value=false;matrixSearchDone.value=true}}
 },350)
}
function selectMatrixUser(user:MatrixUser) {
 matrixSearchRequest++
 delete matrixAvatarUnavailable.value[user.user_id]
 sessionForm.value.matrix_user_id=user.user_id;sessionForm.value.matrix_display_name=user.display_name;sessionForm.value.matrix_has_avatar=user.has_avatar
 matrixSearch.value=user.display_name;matrixUsers.value=[];matrixSearchError.value='';matrixSearchDone.value=false
}
function clearMatrixUser() {
 matrixSearchRequest++
 sessionForm.value.matrix_user_id='';sessionForm.value.matrix_display_name='';sessionForm.value.matrix_has_avatar=false
 matrixSearch.value='';matrixUsers.value=[];matrixSearchDone.value=false
}
function panelPrefix() {const first=window.location.pathname.split('/').filter(Boolean)[0];return first?`/${first}`:''}
function apiUrl(path:string) {return `${panelPrefix()}${path}`}
function assetUrl(path:string) {return `${panelPrefix()}/${path}`}
function matrixAvatarUrl(userId:string) {return apiUrl(`/api/settings/matrix/avatar?user_id=${encodeURIComponent(userId)}`)}
function sessionMatrixAvatarUrl(sessionId:string) {return apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/matrix-avatar`)}
function matrixAvatarLoaded(userId:string) {
 if(sessionForm.value.matrix_user_id===userId)sessionForm.value.matrix_has_avatar=true
}
function matrixAvatarFailed(userId:string) {
 matrixAvatarUnavailable.value[userId]=true
 if(sessionForm.value.matrix_user_id===userId)sessionForm.value.matrix_has_avatar=false
}
async function testVpnConnection() {
 if(vpnTestBusy.value||!vpnStatus.value?.configured)return
 vpnTestBusy.value=true;vpnTestError.value='';vpnTestResult.value=null
 try {vpnTestResult.value=await api('/settings/vpn/test','POST',{}) as VpnTestResult}
 catch(e) {vpnTestError.value=(e as Error).message}
 finally {vpnTestBusy.value=false}
}
async function saveVpnConfig() {
 if(vpnBusy.value||!vpnUri.value.trim())return
 if(vpnStatus.value?.active_sessions&&!confirm('Применение нового узла перезапустит Xray и прервёт текущие TCP-соединения. Звонки останутся запущенными. Продолжить?'))return
 vpnBusy.value=true;vpnError.value=''
 try {vpnStatus.value=await api('/settings/vpn/config','PUT',{uri:vpnUri.value.trim()}) as VpnStatus;vpnUri.value='';notice.value='VLESS-конфигурация сохранена и проверена Xray.'}
 catch(e) {vpnError.value=(e as Error).message}
 finally {vpnBusy.value=false}
}
async function toggleVpn() {
 if(!vpnStatus.value||vpnBusy.value)return
 if(vpnStatus.value.active_sessions&&!confirm('Смена маршрута перезапустит Xray и прервёт текущие TCP-соединения. Звонки останутся запущенными. Продолжить?'))return
 vpnBusy.value=true;vpnError.value=''
 try {vpnStatus.value=await api('/settings/vpn/enabled','PUT',{enabled:!vpnStatus.value.enabled}) as VpnStatus;notice.value=vpnStatus.value.enabled?'Маршрут сессий переключён на VPN.':'VPN-маршрут выключен: новые соединения пойдут напрямую.'}
 catch(e) {vpnError.value=(e as Error).message}
 finally {vpnBusy.value=false}
}
async function removeVpnConfig() {
 if(!confirm('Удалить сохранённую конфигурацию VLESS?'))return
 vpnBusy.value=true;vpnError.value=''
 try {vpnStatus.value=await api('/settings/vpn/config','DELETE') as VpnStatus;vpnUri.value='';notice.value='Конфигурация VLESS удалена.'}
 catch(e) {vpnError.value=(e as Error).message}
 finally {vpnBusy.value=false}
}
function stateName(s:string) { return ({stopped:'Остановлена',starting:'Запускается',running:'Работает',error:'Ошибка',interrupted:'Прервана · рестарт',exited:'Завершена'} as Record<string,string>)[s]||s }
function sessionCanShare(s:Session) { return s.state==='running'&&s.has_link }
function selectedLinkCanShare() { const current=sessions.value.find(s=>s.id===selectedLinkSession.value?.id);return Boolean(current&&sessionCanShare(current)) }
async function api(path:string, method='GET', body?:unknown) {
 const response=await fetch(apiUrl('/api'+path),{method,headers:method==='GET'?{}:{'Content-Type':'application/json'},body:method==='GET'?undefined:JSON.stringify(body??{})})
 if(response.status===401 && path!=='/login') { authed.value=false; stopStatePolling(); connected.value=false; streamState.value='disconnected' }
 const data=await response.json()
 if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Ошибка запроса')
 return data
}
async function act(fn:()=>Promise<unknown>) { if(busy.value) return; busy.value=true; error.value=''; try { await fn() } catch(e) { error.value=(e as Error).message } finally { busy.value=false } }
function apply(data:any) { accounts.value=data.accounts; sessions.value=data.sessions; platforms.value=data.platforms; updated.value=data.at }
async function refresh() { apply(await api('/state')) }
async function pollState() {
 if(!authed.value||statePollInFlight)return
 statePollInFlight=true
 try {await refresh();connected.value=true;streamState.value='connected'}
 catch {connected.value=false;streamState.value=authed.value?'reconnecting':'disconnected'}
 finally {statePollInFlight=false}
}
function stopStatePolling() { if(stateTimer)clearInterval(stateTimer);stateTimer=null }
function subscribe() { stopStatePolling();if(!authed.value)return;streamState.value='connecting';void pollState();stateTimer=setInterval(()=>void pollState(),Math.max(500,Number(refreshInterval.value)*1000)) }
function setRefreshInterval(value:string) {
 if(!refreshIntervals.some(option=>option.value===value))return
 refreshInterval.value=value
 try {localStorage.setItem('wlp-refresh-interval',value)} catch {}
 subscribe()
}
async function login() { await act(async()=>{await api('/login','POST',{username:loginUsername.value.trim(),password:password.value});try{localStorage.setItem('wlp-admin-username',loginUsername.value.trim())}catch{};password.value='';authed.value=true;await refresh();await loadAdminSettings();subscribe();void loadMatrix()}) }
async function logout() { await act(async()=>{await api('/logout','POST');authed.value=false;stopStatePolling();connected.value=false;streamState.value='disconnected';closeModal()}) }
function openAccount() { accountForm.value={name:'',platform:'telemost',portal:''};modal.value='account';nextTick(()=>document.querySelector<HTMLInputElement>('.dialog input')?.focus()) }
function openSession() { sessionForm.value={name:'',account_id:accounts.value.find(a=>a.has_cookies||a.has_credentials)?.id||'',resources:'default',room:'',recipient_type:'',recipient_label:'',recipient_value:'',matrix_user_id:'',matrix_display_name:'',matrix_has_avatar:false};modal.value='session' }
function recipientTypeChanged() { sessionForm.value.recipient_label='';sessionForm.value.recipient_value='' }
async function createAccount() { await act(async()=>{ const a=await api('/accounts','POST',accountForm.value); await refresh();selected.value=a;modal.value='account-options' }) }
async function createSession() { await act(async()=>{await api('/sessions','POST',sessionForm.value);closeModal();await refresh()}) }
function openRenameSession(s:Session) {renameTarget.value=s;renameValue.value=s.name;error.value='';modal.value='rename-session';nextTick(()=>document.querySelector<HTMLInputElement>('.dialog input')?.select())}
async function saveSessionName() {
 const target=renameTarget.value
 if(!target) return
 await act(async()=>{await api(`/sessions/${target.id}`,'PUT',{name:renameValue.value});closeModal();await refresh();notice.value='Название сессии обновлено.'})
}
function openRecipient(s:Session) { recipientTarget.value=s;sessionForm.value.recipient_type=s.recipient_type||'';sessionForm.value.recipient_label=s.recipient_label||'';sessionForm.value.recipient_value=s.recipient_value||'';sessionForm.value.matrix_user_id=s.matrix_user_id||'';sessionForm.value.matrix_display_name=s.matrix_display_name||'';sessionForm.value.matrix_has_avatar=s.matrix_has_avatar||false;matrixSearch.value=s.matrix_display_name||s.matrix_user_id||'';matrixUsers.value=[];matrixSearchError.value='';matrixSearchDone.value=false;error.value='';modal.value='session-recipient' }
async function saveRecipient() { const target=recipientTarget.value;if(!target)return;await act(async()=>{await api(`/sessions/${target.id}/recipient`,'PUT',{recipient_type:sessionForm.value.recipient_type,recipient_label:sessionForm.value.recipient_label,recipient_value:sessionForm.value.recipient_value,matrix_user_id:sessionForm.value.matrix_user_id,matrix_display_name:sessionForm.value.matrix_display_name,matrix_has_avatar:sessionForm.value.matrix_has_avatar});closeModal();await refresh();notice.value='Контакт сессии обновлён.'}) }
function recipientHref(s:Session) {
 const raw=(s.recipient_value||'').trim()
 if(!raw)return ''
 if(s.recipient_type==='telegram') { const id=raw.replace(/^@/,'');return /^\d{5,20}$/.test(id)?`tg://user?id=${id}`:`https://t.me/${id}` }
 if(s.recipient_type==='whatsapp') { const digits=raw.replace(/@s\.whatsapp\.net$/i,'').replace(/\D/g,'');return digits.length>=8&&digits.length<=15?`https://wa.me/${digits}`:'' }
 if(s.recipient_type==='other') { try { const url=new URL(raw);return ['http:','https:'].includes(url.protocol)?url.href:'' } catch { return '' } }
 return ''
}
function recipientDisplay(s:Session) { const kind=s.recipient_type==='telegram'?'Telegram':s.recipient_type==='whatsapp'?'WhatsApp':s.recipient_label||'Контакт';return `${kind}${s.recipient_value?` · ${s.recipient_value}`:''}` }
function restartMessage(s:Session) { const link=restartLinks.value[s.id];return link?`Привет! Звонок перезапущен. Подключайся по новой ссылке: ${link}`:'' }
async function copyRestartMessage(s:Session) { const message=restartMessage(s);if(!message)return;try{await navigator.clipboard.writeText(message);notice.value=`Сообщение для «${s.name}» скопировано.`}catch{error.value='Не удалось скопировать сообщение. Выдели его в модалке и скопируй вручную.'} }
async function copyAllRestartMessages() { const messages=restartSessions.value.filter(s=>restartMessage(s)).map(s=>`Кому: ${s.recipient_type||s.recipient_label?recipientDisplay(s):'получатель не указан'}\n${restartMessage(s)}`);if(!messages.length)return;try{await navigator.clipboard.writeText(messages.join('\n\n'));notice.value='Все готовые сообщения скопированы.'}catch{error.value='Не удалось скопировать сообщения. Скопируй их по одному из списка.'} }
async function collectRestartLinks() {
 for(const id of restartTargets.value) {
  if(restartLinks.value[id]||restartFailures.value[id])continue
  const s=sessions.value.find(item=>item.id===id)
  if(!s) {restartFailures.value[id]='Сессия не найдена';continue}
  if(s.has_link) {
   try {const result:{link:string|null}=await api(`/sessions/${id}/link`);if(result.link)restartLinks.value[id]=result.link}
   catch(e) {restartFailures.value[id]=(e as Error).message}
  } else if(['error','exited'].includes(s.state)) restartFailures.value[id]=`Creator завершился: ${stateName(s.state).toLowerCase()}`
 }
}
async function restartAllInterrupted() {
 if(restartBusy.value||!interruptedSessions.value.length)return
 restartTargets.value=interruptedSessions.value.map(s=>s.id);restartLinks.value={};restartFailures.value={};restartBusy.value=true;modal.value='restart-links';error.value=''
 for(const s of interruptedSessions.value) {
  try {await api(`/sessions/${s.id}/start`,'POST')}
  catch(e) {restartFailures.value[s.id]=(e as Error).message}
 }
 const deadline=Date.now()+90000
 while(Date.now()<deadline) {
  try {await refresh()} catch {}
  await collectRestartLinks()
  const complete=restartTargets.value.every(id=>restartLinks.value[id]||restartFailures.value[id])
  if(complete)break
  await new Promise(resolve=>setTimeout(resolve,1500))
 }
 if(Date.now()>=deadline) for(const id of restartTargets.value) if(!restartLinks.value[id]&&!restartFailures.value[id])restartFailures.value[id]='Ссылка не появилась за 90 секунд. Проверь журнал сессии.'
 restartBusy.value=false
}
async function sessionAction(s:Session,action:string) { await act(async()=>{await api(`/sessions/${s.id}/${action}`,'POST');await refresh()}) }
async function toggleSessionVpn(s:Session) {
 if(s.vpn_default_enabled) return
 if(['starting','running'].includes(s.state)) { error.value='Остановите сессию перед сменой VPN-маршрута, затем запустите её снова.';return }
 await act(async()=>{const result=await api(`/sessions/${s.id}/vpn`,'PUT',{enabled:!s.vpn_enabled}) as {vpn_enabled:boolean};await refresh();notice.value=result.vpn_enabled?`Для сессии «${s.name}» включён VPN.`:`Для сессии «${s.name}» VPN выключен; будет прямое подключение.`})
}
async function openDesktopCreator() { await act(async()=>{await api(`/accounts/${selected.value!.id}/desktop-creator`,'POST');modal.value='desktop-creator';remoteText.value='';frameError.value='';await refresh();await loadDesktopCreatorFrame()}) }
async function loadDesktopCreatorFrame() {
 if(modal.value!=='desktop-creator'||!selected.value) return
 try { const r=await fetch(apiUrl(`/api/accounts/${selected.value.id}/desktop-creator/frame`));if(!r.ok) {const data=await r.json().catch(()=>({}));throw new Error(data.detail||'Не удалось получить экран Desktop Creator')} const blob=await r.blob();if(modal.value!=='desktop-creator') return;const old=frame.value;frame.value=URL.createObjectURL(blob);if(old) URL.revokeObjectURL(old);frameError.value='' } catch(e) {frameError.value=(e as Error).message}
 if(modal.value==='desktop-creator') frameTimer=setTimeout(loadDesktopCreatorFrame,1200)
}
async function desktopCreatorAction(body:unknown) { if(busy.value||!selected.value) return;busy.value=true;try {await api(`/accounts/${selected.value.id}/desktop-creator/action`,'POST',body)} catch(e) {frameError.value=(e as Error).message} finally {busy.value=false} }
async function clickDesktopCreator(e:MouseEvent) { const image=remoteImage.value!,rect=image.getBoundingClientRect();await desktopCreatorAction({action:'click',x:(e.clientX-rect.left)/rect.width*image.naturalWidth,y:(e.clientY-rect.top)/rect.height*image.naturalHeight}) }
async function typeDesktopCreator() { const text=remoteText.value;remoteText.value='';await desktopCreatorAction({action:'type',text}) }
async function importDesktopCreatorCookies() { await act(async()=>{const result=await api(`/accounts/${selected.value!.id}/desktop-creator/save`,'POST');await refresh();notice.value=`Для upstream Creator сохранено ${result.cookie_count} cookies.`}) }
async function checkDesktopCreatorCookies() { await act(async()=>{const result=await api(`/accounts/${selected.value!.id}/desktop-creator/check`,'POST');const messages:Record<string,string>={accepted:`Passport видит вход. Cookies: ${result.cookie_count}. Создание звонка проверит upstream Creator.`,invalid:'Яндекс перенаправил на вход: сессия не авторизована или истекла.',inconclusive:'Яндекс не дал однозначного ответа. Cookies найдены, но валидность подтвердить не удалось.',missing:'Для этой платформы cookies не найдены.',missing_auth_cookie:'Cookies найдены, но обязательная cookie авторизации отсутствует.',presence_only:`Обязательная auth-cookie найдена (${result.cookie_count} cookies). Проверка входа для этой платформы не настроена.`};desktopCookieStatus.value=messages[result.status]||'Не удалось определить статус сессии.'}) }
async function downloadDesktopCreatorCookies() {
 try { const response=await fetch(apiUrl(`/api/accounts/${selected.value!.id}/desktop-creator/cookies.json`));if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'Не удалось скачать cookies')};const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'cookies.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000) }
 catch(e) { desktopCookieStatus.value=(e as Error).message }
}
async function downloadDesktopCreatorExport() {
 try { const response=await fetch(apiUrl(`/api/accounts/${selected.value!.id}/desktop-creator/cookies.zip`));if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'Не удалось скачать экспорт Creator')};const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='cookies.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);desktopCookieStatus.value='Скачан официальный cookies.zip из Desktop Creator.' }
 catch(e) { desktopCookieStatus.value=(e as Error).message }
}
async function stopDesktopCreator() { await act(async()=>{await api(`/accounts/${selected.value!.id}/desktop-creator`,'DELETE');closeModal();await refresh()}) }
async function showQr(s:Session) {
 if(!sessionCanShare(s))return
 selectedLinkSession.value=s;joinLink.value='';qrDataUrl.value='';qrImageBlob.value=null;qrLoading.value=true;copyStatus.value='';error.value='';modal.value='link-qr'
 try {
  const {link}=await api(`/sessions/${s.id}/link`)
  if(modal.value!=='link-qr'||selectedLinkSession.value?.id!==s.id||!selectedLinkCanShare()) return
  if(!link) throw new Error('Ссылка звонка ещё не создана')
  const image=await QRCode.toDataURL(link,{width:320,margin:3,errorCorrectionLevel:'M'})
  const base64=image.slice(image.indexOf(',')+1),binary=atob(base64),bytes=new Uint8Array(binary.length)
  for(let i=0;i<binary.length;i++) bytes[i]=binary.charCodeAt(i)
  const blob=new Blob([bytes],{type:'image/png'})
  if(modal.value!=='link-qr'||selectedLinkSession.value?.id!==s.id||!selectedLinkCanShare()) return
  joinLink.value=link;qrDataUrl.value=image;qrImageBlob.value=blob
 } catch(e) {if(modal.value==='link-qr') error.value=(e as Error).message} finally {qrLoading.value=false}
}
async function copySessionLink() {
 if(!joinLink.value||copyBusy.value||!selectedLinkCanShare()) return
 copyBusy.value=true;error.value=''
 try {await navigator.clipboard.writeText(joinLink.value);copyStatus.value='link'}
 catch(e) {error.value='Не удалось скопировать ссылку: '+(e as Error).message}
 finally {copyBusy.value=false}
}
async function copyDns() {try {await navigator.clipboard.writeText('1.1.1.1,8.8.8.8');dnsCopied.value=true;setTimeout(()=>dnsCopied.value=false,1800)}catch{error.value='Не удалось скопировать DNS. Выдели адрес и скопируй вручную.'}}
async function copyQrImage() {
 if(!qrImageBlob.value||copyBusy.value||!selectedLinkCanShare()) return
 copyBusy.value=true;error.value=''
 try {
  await navigator.clipboard.write([new ClipboardItem({'image/png':qrImageBlob.value})])
  copyStatus.value='qr'
 } catch(e) {error.value='Не удалось скопировать QR-код: '+(e as Error).message}
 finally {copyBusy.value=false}
}
async function removeSession(s:Session) { if(!confirm(`Удалить сессию «${s.name}» и её журнал?`)) return;await act(async()=>{await api(`/sessions/${s.id}`,'DELETE');await refresh()}) }
async function removeAccount(a:Account) { if(!confirm(`Удалить аккаунт «${a.name}» и сохранённые cookies?`)) return;await act(async()=>{await api(`/accounts/${a.id}`,'DELETE');await refresh()}) }
function accountOptions(a:Account) { selected.value=a;modal.value='account-options';cookieText.value='';cookieZipBase64.value='';cookieZipName.value='';credentialForm.value={email:'',password:''} }
async function openBrowser() { await act(async()=>{await api(`/accounts/${selected.value!.id}/browser`,'POST');modal.value='browser';await refresh();await loadFrame()}) }
async function freshBrowser() { await act(async()=>{await api(`/accounts/${selected.value!.id}/browser/fresh`,'POST');modal.value='browser';await refresh();await loadFrame()}) }
async function loadFrame() {
 if(modal.value!=='browser'||!selected.value) return
 try { const r=await fetch(apiUrl(`/api/accounts/${selected.value.id}/browser/frame`));if(!r.ok) throw new Error('Не удалось получить экран браузера'); const blob=await r.blob();if(modal.value!=='browser') return;const old=frame.value;frame.value=URL.createObjectURL(blob);if(old) URL.revokeObjectURL(old);frameError.value='' } catch(e) {frameError.value=(e as Error).message}
 if(modal.value==='browser') frameTimer=setTimeout(loadFrame,1500)
}
async function browserAction(body:unknown) { await act(async()=>{await api(`/accounts/${selected.value!.id}/browser/action`,'POST',body)}) }
async function clickFrame(e:MouseEvent) { const rect=remoteImage.value!.getBoundingClientRect();await browserAction({action:'click',x:(e.clientX-rect.left)/rect.width*1100,y:(e.clientY-rect.top)/rect.height*760}) }
async function typeRemote() { const text=remoteText.value;remoteText.value='';await browserAction({action:'type',text}) }
async function saveBrowser() { await act(async()=>{await api(`/accounts/${selected.value!.id}/browser/save`,'POST');closeModal();await refresh();notice.value='Cookies сохранены для Creator. Авторизация проверится при запуске звонка.'}) }
async function downloadBrowserCookies() {
 try {
  const response=await fetch(apiUrl(`/api/accounts/${selected.value!.id}/browser/cookies.json`))
  if(!response.ok) throw new Error('Не удалось скачать cookies')
  const blob=await response.blob(), url=URL.createObjectURL(blob), link=document.createElement('a')
  link.href=url;link.download='telemost-cookies.json';link.click();URL.revokeObjectURL(url)
 } catch(e) { error.value=(e as Error).message }
}
async function saveCookies() { await act(async()=>{const data=cookieZipBase64.value?{export_zip_base64:cookieZipBase64.value}:JSON.parse(cookieText.value);await api(`/accounts/${selected.value!.id}/cookies`,'PUT',data);closeModal();await refresh();notice.value='Cookies сохранены для upstream Creator.'}) }
async function saveCredentials() { await act(async()=>{await api(`/accounts/${selected.value!.id}/credentials`,'PUT',credentialForm.value);credentialForm.value={email:'',password:''};await refresh();selected.value=accounts.value.find(a=>a.id===selected.value?.id)||selected.value;notice.value='Данные для повторного входа сохранены в зашифрованном виде.'}) }
async function deleteCredentials() { if(!confirm('Удалить сохранённые данные для повторного входа? Cookies останутся.')) return;await act(async()=>{await api(`/accounts/${selected.value!.id}/credentials`,'DELETE');await refresh();selected.value=accounts.value.find(a=>a.id===selected.value?.id)||selected.value;credentialForm.value={email:'',password:''};notice.value='Данные для повторного входа удалены.'}) }
async function upload(e:Event) { const file=(e.target as HTMLInputElement).files?.[0];if(!file) return;if(file.size>700_000){error.value='Файл должен быть меньше 700 КБ';return}if(file.name.toLowerCase().endsWith('.zip')){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768) binary+=String.fromCharCode(...bytes.subarray(i,i+32768));cookieZipBase64.value=btoa(binary);cookieZipName.value=file.name;cookieText.value=''}else{cookieZipBase64.value='';cookieZipName.value='';cookieText.value=await file.text()} }
function closeModal() { matrixSearchRequest++;if(matrixSearchTimer)clearTimeout(matrixSearchTimer);if(modal.value==='restart-links'&&restartBusy.value)return;if(frameTimer) clearTimeout(frameTimer);frameTimer=null;if(frame.value) URL.revokeObjectURL(frame.value);frame.value='';remoteText.value='';cookieText.value='';cookieZipBase64.value='';cookieZipName.value='';desktopCookieStatus.value='';credentialForm.value={email:'',password:''};renameTarget.value=null;renameValue.value='';recipientTarget.value=null;logRequest++;destinationRequest++;selectedLogSession.value=null;selectedDestinationSession.value=null;selectedLinkSession.value=null;joinLink.value='';qrDataUrl.value='';qrImageBlob.value=null;qrLoading.value=false;copyStatus.value='';modal.value='' }
async function cancelBrowser() { await act(async()=>{await api(`/accounts/${selected.value!.id}/browser`,'DELETE');closeModal();await refresh()}) }
async function fetchLogs(mode:'reset'|'older'|'new'='reset') {
 const sid=selectedLogSession.value?.id
 if(!sid || mode!=='reset'&&logBusy.value) return
 if(mode==='reset') {logRequest++;logs.value=[];logHasMore.value=false}
 const requestVersion=logRequest
 const params=new URLSearchParams({level:logLevel.value})
 if(logSince.value) params.set('since',new Date(logSince.value).toISOString())
 if(logUntil.value) params.set('until',new Date(logUntil.value).toISOString())
 if(mode==='older'&&logs.value.length) params.set('before',String(logs.value[logs.value.length-1].id))
 if(mode==='new'&&logs.value.length) params.set('after',String(logs.value[0].id))
 const query=params.toString()
 logBusy.value=true;logError.value=''
 try {
  const result:{items:Log[];has_more:boolean}=await api(`/sessions/${sid}/logs?${query}`)
  if(selectedLogSession.value?.id!==sid || requestVersion!==logRequest) return
  if(mode==='older') {logs.value=[...logs.value,...result.items];logHasMore.value=result.has_more}
  else if(mode==='new') {logs.value=[...result.items,...logs.value]}
  else {logs.value=result.items;logHasMore.value=result.has_more}
 } catch(e) {if(requestVersion===logRequest) logError.value=(e as Error).message} finally {if(requestVersion===logRequest) logBusy.value=false}
}
async function showLogs(s:Session) {
 selectedLogSession.value=s;logLevel.value='all';logSince.value='';logUntil.value='';logs.value=[];logHasMore.value=false;logError.value='';modal.value='session-logs'
 await fetchLogs()
}
function displayLogMessage(message:string) {
 return message.replace(/^\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2}\s*/, '').replace(/^\[([^\]]+)\]\s*/, '$1 › ').replace(/^relay(?:\[([^\]]+)\])?:\s*/, (_, channel:string|undefined) => `${channel||'relay'} › `)
}
async function fetchDestinations(mode:'reset'|'older'|'new'='reset') {
 const sid=selectedDestinationSession.value?.id
 if(!sid || mode!=='reset'&&destinationBusy.value) return
 if(mode==='reset') {destinationRequest++;destinations.value=[];destinationHasMore.value=false}
 const requestVersion=destinationRequest, params=new URLSearchParams()
 if(destinationDomain.value.trim()) params.set('domain',destinationDomain.value.trim())
 if(destinationSince.value) params.set('since',new Date(destinationSince.value).toISOString())
 if(destinationUntil.value) params.set('until',new Date(destinationUntil.value).toISOString())
 if(mode==='older'&&destinations.value.length) params.set('before',String(destinations.value[destinations.value.length-1].id))
 if(mode==='new'&&destinations.value.length) params.set('after',String(destinations.value[0].id))
 destinationBusy.value=true;destinationError.value=''
 try {const result:{items:Destination[];has_more:boolean}=await api(`/sessions/${sid}/destinations?${params}`);if(selectedDestinationSession.value?.id!==sid||requestVersion!==destinationRequest)return;if(mode==='older'){destinations.value=[...destinations.value,...result.items];destinationHasMore.value=result.has_more}else if(mode==='new')destinations.value=[...result.items,...destinations.value];else{destinations.value=result.items;destinationHasMore.value=result.has_more}}
 catch(e){if(requestVersion===destinationRequest)destinationError.value=(e as Error).message}finally{if(requestVersion===destinationRequest)destinationBusy.value=false}
}
async function showDestinations(s:Session) {selectedDestinationSession.value=s;destinationDomain.value='';destinationSince.value='';destinationUntil.value='';destinations.value=[];destinationHasMore.value=false;destinationError.value='';modal.value='session-destinations';await fetchDestinations()}
function enableEntering(element: Element) {
 element.removeAttribute('inert')
 element.removeAttribute('aria-hidden')
}
function disableLeaving(element: Element) {
 element.setAttribute('inert', '')
 element.setAttribute('aria-hidden', 'true')
}
function beforeSectionLeave(element: Element) {
 disableLeaving(element)
 const stage = element.parentElement
 if(stage) stage.style.minHeight = `${element.getBoundingClientRect().height}px`
}
function afterSectionEnter(element: Element) { element.parentElement?.style.removeProperty('min-height') }
function changeTab(t:string) { tab.value=t;if(t==='settings'){void loadStorage();void loadVpn();void loadMatrix();void loadPanel();if(settingsPane.value==='panel')void loadAdminSettings()} }
async function pollLogs() {if(authed.value&&modal.value==='session-logs'&&selectedLogSession.value) await fetchLogs('new');if(authed.value&&modal.value==='session-destinations'&&selectedDestinationSession.value) await fetchDestinations('new');logTimer=setTimeout(pollLogs,2500)}
function escapeModal(e:KeyboardEvent) {
 if(!modal.value) return
 if(e.key==='Escape'&&modal.value!=='browser'&&!(modal.value==='restart-links'&&restartBusy.value)) closeModal()
 if(e.key==='Tab') {
  const items=Array.from(document.querySelectorAll<HTMLElement>('.dialog button:not(:disabled), .dialog input, .dialog select, .dialog textarea, .dialog summary')).filter(el=>el.getClientRects().length>0)
  const first=items[0], last=items[items.length-1]
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
 }
}
onMounted(async()=>{document.addEventListener('keydown',escapeModal);try {await refresh();authed.value=true;void loadAdminSettings();subscribe();void loadMatrix()}catch{};pollLogs()})
onUnmounted(()=>{stopStatePolling();if(frameTimer)clearTimeout(frameTimer);if(logTimer)clearTimeout(logTimer);if(panelPollTimer)clearTimeout(panelPollTimer);document.removeEventListener('keydown',escapeModal)})
</script>

<template>
 <main v-if="!authed" class="login-page">
  <form class="login-card" @submit.prevent="login"><div class="login-brand"><div class="brand-symbol"><img :src="assetUrl('brand-mark.png')" alt="" aria-hidden="true"></div><span class="login-wordmark">PRNL<span>+</span></span></div><span v-terminal class="eyebrow terminal-signature">PRNL / CONTROL PLANE</span><h1>Всё под контролем.</h1><p>Войдите, чтобы управлять платформами, звонками и туннелями.</p><label>Логин администратора<input v-model="loginUsername" required maxlength="100" autocomplete="username" autofocus placeholder="admin"></label><label>Пароль администратора<input v-model="password" type="password" required autocomplete="current-password" placeholder="Введите пароль администратора"></label><p v-if="error" role="alert" class="error-text">{{ error }}</p><button class="primary" :disabled="busy">{{ busy?'Входим…':'Войти в панель' }}<ChevronRight :size="17"/></button><small>Локальная панель · Доступ только для администратора</small></form>
 </main>
 <div v-else v-boot class="shell" :inert="modal ? true : undefined"><a class="skip-link" href="#main-content">К содержимому</a>
  <aside class="sidebar"><a class="brand" href="#" @click.prevent="changeTab('sessions')"><span class="brand-symbol"><img :src="assetUrl('brand-mark.png')" alt="" aria-hidden="true"></span><BrandWordmark/></a><div class="workspace-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
   <nav aria-label="Основная навигация"><button :class="{active:tab==='sessions'}" :aria-current="tab==='sessions'?'page':undefined" @click="changeTab('sessions')"><Layers3 :size="18"/>Сессии<span class="nav-count">{{ sessions.length }}</span></button><button :class="{active:tab==='accounts'}" :aria-current="tab==='accounts'?'page':undefined" @click="changeTab('accounts')"><KeyRound :size="18"/>Аккаунты<span class="nav-count">{{ accounts.length }}</span></button><button :class="{active:tab==='guide'}" :aria-current="tab==='guide'?'page':undefined" @click="changeTab('guide')"><BookOpenText :size="18"/>Инструкция</button><button :class="{active:tab==='settings'}" :aria-current="tab==='settings'?'page':undefined" @click="changeTab('settings')"><Settings2 :size="18"/>Настройки</button><button :class="{active:tab==='about'}" :aria-current="tab==='about'?'page':undefined" @click="changeTab('about')"><Info :size="18"/>О проекте</button></nav>
   <div class="sidebar-bottom"><div class="system-card" :data-connection="streamState" v-changed="streamState"><span :class="['dot',connected?'green':'amber']"></span><strong>{{ streamLabel }}</strong><p>Один Creator → один Joiner</p></div><button class="logout" @click="logout"><LogOut :size="16"/>Выйти</button></div>
  </aside>
  <div class="main-column"><header class="topbar"><div><span class="muted">Рабочее пространство</span><ChevronRight :size="14"/><span>{{ tab==='sessions'?'Сессии':tab==='accounts'?'Аккаунты':tab==='guide'?'Инструкция':tab==='settings'?'Настройки':'О проекте' }}</span></div></header>
  <div class="content" id="main-content" role="main" tabindex="-1"><div class="page-heading"><div><span class="eyebrow">{{ tab==='about'?'PRNL + / ABOUT':tab==='guide'?'JOINER / QUICK START':tab==='settings'?(settingsPane==='panel'?'SYSTEM / PANEL':settingsPane==='matrix'?'MATRIX / INTEGRATION':settingsPane==='vpn'?'SYSTEM / XRAY':'SYSTEM / STORAGE'):'ОБЗОР / '+(tab==='sessions'?'СЕССИИ':tab==='accounts'?'ИНТЕГРАЦИИ':'О ПРОЕКТЕ') }}</span><h1>{{ tab==='sessions'?'Центр управления':tab==='accounts'?'Аккаунты платформ':tab==='guide'?'Инструкция для подключения':tab==='settings'?(settingsPane==='panel'?'Настройки панели':settingsPane==='matrix'?'Интеграция Matrix':settingsPane==='vpn'?'Настройки VPN':'Хранилище логов'):'О проекте' }}<span class="title-dot">.</span></h1><p>{{ tab==='sessions'?'Аккаунты, сессии и краткий статус подключений.':tab==='accounts'?'Авторизация и cookies для каждого сервиса видеозвонков.':tab==='guide'?'Как скачать Joiner, подключиться к звонку и настроить DNS.':tab==='settings'?(settingsPane==='panel'?'HTTPS-доступ по IP и локальный центр подписи.':settingsPane==='matrix'?'Matrix-уведомления, привязка пользователей и команды для звонков.':settingsPane==='vpn'?'Маршрут трафика Creator через VLESS или напрямую.':'Срок хранения, объём журналов и автоматическая очистка.'):'Панель управления, её автор и проект, на котором она основана.' }}</p></div><button v-if="tab==='sessions'||tab==='accounts'" class="primary" @click="tab==='sessions'?openSession():openAccount()"><Plus :size="17"/>{{ tab==='sessions'?'Новая сессия':'Добавить аккаунт' }}</button></div>
   <div v-if="tab==='sessions'" class="overview-stats" aria-label="Общая статистика сессий"><article><span>ВСЕГО СЕССИЙ</span><strong>{{ sessions.length }}</strong><small>Создано в панели</small></article><article><span>АКТИВНЫЕ СЕССИИ</span><strong>{{ workingSessions }}</strong><small>{{ connectedJoiners }} с Joiner в звонке</small></article><article><span>СРЕДНЯЯ СКОРОСТЬ</span><strong>{{ formatBytes(averageSpeed) }}<small>/с</small></strong><small>По сессиям с живой телеметрией</small></article><article><span>ПЕРЕДАНО ВСЕГО</span><strong>{{ formatBytes(totalTraffic) }}</strong><small>Сумма приёма и отправки</small></article></div>
   <div v-if="tab==='sessions'&&interruptedSessions.length" class="interrupted-notice"><span class="interrupted-icon"><AlertTriangle :size="19"/></span><div class="interrupted-copy"><strong>{{ interruptedSessions.length }} {{ interruptedSessions.length===1?'сессия прервана':'сессий прервано' }}</strong><p>Docker или сервер перезапустился, пока Creator работал. Перезапусти сессии, чтобы восстановить звонки и получить новые ссылки.</p></div><button class="primary" :disabled="restartBusy" @click="restartAllInterrupted"><RotateCcw :size="16"/>Перезапустить все</button></div>
   <div v-if="tab==='sessions'&&matrixRetryExhaustedSessions.length" class="matrix-recovery-notice"><span class="interrupted-icon"><AlertTriangle :size="19"/></span><div class="interrupted-copy"><strong>Matrix не смог восстановить {{ matrixRetryExhaustedSessions.length }} {{ matrixRetryExhaustedSessions.length===1?'сессию':'сессии' }}</strong><p>Автоперезапуск исчерпал 3 попытки. Проверь журнал и запусти сессию вручную.</p></div><button class="secondary" @click="showLogs(matrixRetryExhaustedSessions[0])"><Terminal :size="15"/>Открыть журнал</button></div>
   <div v-if="initialSetupRequired" class="first-run-notice" role="alert"><AlertTriangle :size="19"/><div><strong>Первый запуск · защити панель</strong><p>Задай новый пароль в «Настройки → Панель». Сохрани резервную копию <code>.env</code> и не меняй <code>ENCRYPTION_KEY</code> после запуска: от неё зависят сохранённые cookies, ссылки и VPN-конфигурация.</p><p>Для продакшена проверь внешний HTTPS-адрес, открой выбранные порты на firewall и установи Root CA на своих устройствах.</p></div><button class="secondary" @click="tab='settings';settingsPane='panel';loadPanel();loadAdminSettings()">Настроить <ChevronRight :size="15"/></button></div>
   <Transition @before-enter="enableEntering" name="notice" @before-leave="disableLeaving"><div v-if="!connected" role="status" class="alert error">Нет связи с сервером. Показано последнее полученное состояние; данные могут быть устаревшими.</div></Transition><Transition @before-enter="enableEntering" name="notice" @before-leave="disableLeaving"><div v-if="error" role="alert" class="alert error"><AlertTriangle :size="18"/><span>{{ error }}</span><button aria-label="Закрыть ошибку" @click="error=''">×</button></div></Transition><Transition @before-enter="enableEntering" name="notice" @before-leave="disableLeaving"><div v-if="notice" role="status" class="alert success"><Check :size="17"/>{{ notice }}<button aria-label="Закрыть уведомление" @click="notice=''">×</button></div></Transition>
   <div class="section-stage"><Transition @before-enter="enableEntering" name="section" mode="out-in" @before-leave="beforeSectionLeave" @after-enter="afterSectionEnter"><div :key="tab" class="section-view">
   <section v-if="tab==='sessions'" class="session-manager" aria-label="Управление сессиями">
    <div class="manager-toolbar"><div class="manager-summary"><span class="manager-lamp"></span><div><strong>{{ workingSessions }} / {{ sessions.length }} работают</strong><small>{{ connectedJoiners }} подключены · {{ sessionGroups.length }} групп аккаунтов</small></div></div><div class="manager-controls"><div class="refresh-setting"><span>ОБНОВЛЯТЬ</span><ThemedSelect v-model="refreshInterval" label="Интервал обновления сессий" :options="refreshIntervals" @update:model-value="setRefreshInterval" /></div><button class="group-control" :disabled="!sessionGroups.length" @click="setAccountGroups(false)">Развернуть</button><button class="group-control" :disabled="!sessionGroups.length" @click="setAccountGroups(true)">Свернуть</button><label class="search"><Search :size="16"/><input v-model="search" aria-label="Поиск сессий" placeholder="Найти сессию…"></label></div></div>
    <div v-if="!sessions.length" class="empty"><div class="empty-visual"><Radio :size="32"/><span></span></div><h2>Первый звонок начинается здесь</h2><p>Добавьте аккаунт платформы, войдите в него<br>и создайте Creator-сессию.</p><div class="steps"><span><b>01</b> Аккаунт</span><ChevronRight :size="14"/><span><b>02</b> Авторизация</span><ChevronRight :size="14"/><span><b>03</b> Сессия</span></div><button class="primary" @click="accounts.length?openSession():openAccount()"><Plus :size="17"/>{{ accounts.length?'Создать сессию':'Подключить платформу' }}</button></div>
    <div v-else-if="!sessionGroups.length" class="group-empty">Нет сессий с таким названием.</div>
    <div v-else class="session-groups"><section v-for="group in sessionGroups" :key="group.id" class="account-group" :class="{collapsed:collapsedAccounts[group.id]}">
     <button class="account-group-head" :aria-expanded="!collapsedAccounts[group.id]" @click="toggleAccountGroup(group.id)"><span class="group-chevron"><ChevronRight :size="16"/></span><span :class="['platform-icon',group.platform]">{{ (group.platform||'?').slice(0,2).toUpperCase() }}</span><span class="group-identity"><small>АККАУНТ / {{ platform(group.platform) }}</small><strong>{{ group.name }}</strong></span><span :class="['group-live',{active:group.sessions.some(s=>['running','starting'].includes(s.state))}]"><i></i>{{ group.sessions.filter(s=>['running','starting'].includes(s.state)).length }} работают</span><span class="group-count">{{ String(group.sessions.length).padStart(2,'0') }} СЕСС.</span></button>
     <div v-if="!collapsedAccounts[group.id]" class="account-sessions"><article v-for="s in group.sessions" :key="s.id" class="session-card"><div class="session-card-main"><span :class="['session-pulse',s.state]"><i></i></span><div class="session-profile"><span v-if="s.matrix_user_id" class="session-matrix-avatar" :title="`Matrix · ${s.matrix_display_name||s.matrix_user_id}`"><img v-if="!matrixAvatarUnavailable[s.matrix_user_id]" :src="sessionMatrixAvatarUrl(s.id)" alt="" @error="matrixAvatarFailed(s.matrix_user_id)"/><UserRound v-else :size="16"/><i aria-hidden="true"></i></span><div class="session-identity"><strong>{{ s.name }}</strong><small>{{ s.recipient_type||s.recipient_label?recipientDisplay(s):`SESSION / ${s.id.slice(0,8).toUpperCase()}` }}</small></div></div><span :class="['session-state',s.state]"><i></i>{{ stateName(s.state) }}</span><span :class="['joiner-state',s.peer==='recent'?'online':s.state==='running'?'waiting':'offline']"><i></i>{{ s.peer==='recent'?'Кто-то в звонке':s.state==='running'?'Ожидаем Joiner':'Нет в звонке' }}</span><span class="session-traffic" :title="`Приём: ${formatBytes(s.rx_bytes)} · Отправка: ${formatBytes(s.tx_bytes)}`"><strong>{{ formatBytes((s.rx_bytes||0)+(s.tx_bytes||0)) }}</strong><small>{{ s.rx_rate!==null&&s.tx_rate!==null?`${formatBytes((s.rx_rate||0)+(s.tx_rate||0))}/с · ${s.active_connections??0} соед.`:'— Б/с' }}</small></span></div><div class="row-actions session-actions"><button v-if="!['starting','running'].includes(s.state)" title="Запустить сессию" :aria-label="`Запустить сессию ${s.name}`" :disabled="busy" @click="sessionAction(s,'start')"><Play :size="15"/></button><button v-else title="Остановить сессию" :aria-label="`Остановить сессию ${s.name}`" :disabled="busy" @click="sessionAction(s,'stop')"><Square :size="14"/></button><button title="Переименовать сессию" :aria-label="`Переименовать сессию ${s.name}`" @click="openRenameSession(s)"><Pencil :size="15"/></button><button title="Настроить контакт сессии" :aria-label="`Настроить контакт сессии ${s.name}`" @click="openRecipient(s)"><UserRound :size="15"/></button><a v-if="recipientHref(s)" class="recipient-link" :href="recipientHref(s)" target="_blank" rel="noopener noreferrer" :title="`Открыть чат: ${recipientDisplay(s)}`" :aria-label="`Открыть чат: ${recipientDisplay(s)}`"><External :size="15"/></a><button :class="['session-vpn',{enabled:s.vpn_effective,global:s.vpn_default_enabled}]" :title="s.vpn_default_enabled?'VPN включён общим правилом для всех сессий':s.vpn_enabled?'VPN включён для этой сессии':'Прямой маршрут; нажмите, чтобы включить VPN только для этой сессии'" :disabled="s.vpn_default_enabled||['starting','running'].includes(s.state)" @click="toggleSessionVpn(s)"><ShieldCheck :size="13"/><span>{{ s.vpn_default_enabled?'VPN · ОБЩИЙ':s.vpn_enabled?'VPN · СЕССИЯ':'DIRECT' }}</span></button><button title="Показать QR-код и ссылку" :aria-label="`QR-код и ссылка сессии ${s.name}`" :disabled="!sessionCanShare(s)" @click="showQr(s)"><QrCode :size="15"/></button><button title="Переходы по доменам" :aria-label="`Переходы по доменам сессии ${s.name}`" @click="showDestinations(s)"><Globe2 :size="15"/></button><button title="Открыть журнал" :aria-label="`Журнал сессии ${s.name}`" @click="showLogs(s)"><Terminal :size="15"/></button><button title="Удалить сессию" :aria-label="`Удалить сессию ${s.name}`" :disabled="['running','starting'].includes(s.state)" @click="removeSession(s)"><Trash2 :size="15"/></button></div></article></div>
    </section></div>
    <footer class="manager-footer"><span><i :class="connected?'online':'offline'"></i>{{ connected?'ОПРОС СОСТОЯНИЯ · '+refreshIntervalLabel:'ПОСЛЕДНЕЕ СОСТОЯНИЕ' }}</span><span>{{ updated?'ОБНОВЛЕНО '+date(updated):'ОЖИДАНИЕ ДАННЫХ' }}</span></footer>
   </section>
   <section v-if="tab==='accounts'" class="accounts-grid"><article v-for="a in accounts" :key="a.id" class="account-card"><div class="account-top"><span :class="['platform-icon large',a.platform]">{{ a.platform.slice(0,2).toUpperCase() }}</span><span v-changed="`${a.has_cookies}/${a.has_credentials}`" :class="['badge',(a.has_cookies||a.has_credentials)?'running':'stopped']">{{ a.has_credentials?'Автовход настроен':a.has_cookies?'Cookies сохранены':'Нужен вход' }}</span></div><h2>{{ a.name }}</h2><p>{{ platform(a.platform) }}</p><small>{{ a.has_cookies?'Данные обновлены '+new Date(a.updated_at).toLocaleString('ru-RU'):a.has_credentials?'Creator войдёт автоматически при запуске':'Авторизуйтесь через браузер в панели' }}</small><p class="card-note">{{ a.has_credentials?'При завершении срока действия сессии Creator попробует войти повторно.':'Наличие cookies не подтверждает действительность сессии платформы.' }}</p><div class="account-buttons"><button class="secondary" @click="accountOptions(a)"><Settings2 :size="15"/>Настроить</button><button class="icon-button" aria-label="Удалить аккаунт" @click="removeAccount(a)"><Trash2 :size="16"/></button></div></article><button class="add-account" @click="openAccount()"><Plus :size="27"/><strong>Подключить платформу</strong><span>VK, Телемост, WB Stream, DION, Битрикс24</span></button></section>
   <section v-if="tab==='guide'" class="guide-page" aria-label="Инструкция по подключению Joiner">
    <article class="guide-banner"><span class="guide-banner-icon"><AlertCircle :size="21"/></span><div><strong>Перед запуском проверь DNS в Joiner</strong><p>Если в логах Creator повторяется `UDP blocked 192.168.x.x:53`, Joiner отправляет DNS-запросы в локальную сеть клиента. Они блокируются и сайты могут зависать на таймауте.</p></div></article>
    <article class="guide-card guide-download"><div class="guide-card-heading"><span class="about-icon"><Download :size="18"/></span><div><span class="guide-step">ШАГ 01</span><h2>Скачай Joiner из официального GitHub</h2></div></div><p>Это клиент для устройства, которое подключается к звонку. Для Windows выбери файл <code>WhitelistBypass.Joiner-…-x64.exe</code> (для 64-разрядной системы). Он portable, устанавливать его не нужно. Для macOS или Linux выбери сборку своей системы.</p><div class="guide-links"><a class="primary" href="https://github.com/kulikov0/whitelist-bypass/releases" target="_blank" rel="noopener noreferrer"><Download :size="16"/>Открыть GitHub Releases<External :size="14"/></a><a class="secondary" href="https://github.com/kulikov0/whitelist-bypass/blob/main/docs/SETUP.md" target="_blank" rel="noopener noreferrer">Полная инструкция upstream<External :size="14"/></a></div></article>
    <article class="guide-card"><div class="guide-card-heading"><span class="about-icon"><KeyRound :size="18"/></span><div><span class="guide-step">ШАГ 02</span><h2>Подключись к звонку</h2></div></div><ol class="guide-steps"><li>В панели открой «Сессии» и нажми кнопку QR-кода. Скопируй ссылку звонка или отсканируй QR с устройства Joiner.</li><li>Запусти Joiner и вставь ссылку звонка в поле ссылки.</li><li>Оставь режим TUN включённым, если нужен VPN для всего устройства. На Windows подтверди запрос прав администратора.</li></ol></article>
    <article class="guide-card guide-dns"><div class="guide-card-heading"><span class="about-icon"><Globe2 :size="18"/></span><div><span class="guide-step">ШАГ 03</span><h2>Укажи публичный DNS до старта</h2></div></div><p>В настройках Joiner найди поле <strong>DNS</strong> и укажи:</p><div class="dns-value"><code>1.1.1.1,8.8.8.8</code><button class="secondary" @click="copyDns"><Copy :size="15"/>{{ dnsCopied?'Скопировано':'Копировать' }}</button></div><p>После этого нажми Start и дождись <code>TUNNEL CONNECTED</code>. Если пользуешься Android Joiner, задай DNS в настройках приложения, а системный Private DNS поставь в «Автоматически».</p><div class="guide-warning"><ShieldCheck :size="16"/><span>Не включай <code>--allow-private-dst</code> на Creator ради DNS: он разрешает Joiner обращаться и к другим адресам локальной сети Creator.</span></div></article>
    <article class="guide-card guide-note"><div class="guide-card-heading"><span class="about-icon"><Info :size="18"/></span><div><span class="guide-step">ВАЖНО</span><h2>Одна ссылка — одно устройство</h2></div></div><p>Каждая Creator-сессия обслуживает одного Joiner. Для второго устройства создай отдельную сессию и передай ему её ссылку.</p></article>
   </section>
   <section v-if="tab==='settings'" class="settings-page" aria-label="Настройки системы">
    <div class="settings-tabs" role="tablist" aria-label="Раздел настроек"><button role="tab" :aria-selected="settingsPane==='logs'" :class="{active:settingsPane==='logs'}" @click="settingsPane='logs';loadStorage()"><Terminal :size="15"/>Хранение логов</button><button role="tab" :aria-selected="settingsPane==='vpn'" :class="{active:settingsPane==='vpn'}" @click="settingsPane='vpn';loadVpn()"><ShieldCheck :size="15"/>Xray / VPN</button><button role="tab" :aria-selected="settingsPane==='matrix'" :class="{active:settingsPane==='matrix'}" @click="settingsPane='matrix';loadMatrix()"><Layers3 :size="15"/>Интеграция Matrix</button><button role="tab" :aria-selected="settingsPane==='panel'" :class="{active:settingsPane==='panel'}" @click="settingsPane='panel';loadPanel();loadAdminSettings()"><ShieldCheck :size="15"/>Панель</button></div>
    <div v-if="settingsPane==='logs'" class="storage-page" aria-label="Настройки хранения журналов">
    <div class="storage-toolbar"><div><span class="storage-kicker"><Activity :size="14"/> LOG RETENTION / SYSTEM</span><p>Журнал Creator и история доменов очищаются по сроку. Очистка запускается автоматически раз в час.</p></div></div>
    <div v-if="storageError" class="storage-error" role="alert"><AlertTriangle :size="16"/>{{ storageError }}</div>
    <div class="storage-metrics"><article><span><Terminal :size="15"/> ЖУРНАЛ СОЕДИНЕНИЙ</span><strong>{{ storageStats?formatBytes(storageStats.logs.bytes):'—' }}</strong><small>{{ storageStats?`${storageStats.logs.records.toLocaleString('ru-RU')} записей · ${formatBytes(storageStats.logs.live_bytes)} полезных данных`:'Получаем статистику…' }}</small></article><article><span><Globe2 :size="15"/> ПЕРЕХОДЫ ПО ДОМЕНАМ</span><strong>{{ storageStats?formatBytes(storageStats.destinations.bytes):'—' }}</strong><small>{{ storageStats?`${storageStats.destinations.records.toLocaleString('ru-RU')} записей · ${formatBytes(storageStats.destinations.live_bytes)} полезных данных`:'Получаем статистику…' }}</small></article><article><span><Layers3 :size="15"/> ВСЯ БАЗА ДАННЫХ</span><strong>{{ storageStats?formatBytes(storageStats.database_bytes):'—' }}</strong><small>PostgreSQL: размер выделенного пространства</small></article></div>
    <article class="storage-config storage-switches"><div class="storage-config-copy"><span class="storage-kicker">01 / CAPTURE CONTROL</span><h2>Что сохранять</h2><p>Настройки действуют для новых событий во всех сессиях. Уже записанные данные останутся до автоматической очистки по сроку хранения.</p></div><div class="storage-toggle-list"><button class="storage-toggle-row" role="switch" :aria-checked="storageStats?.session_logs_enabled||false" :disabled="storageBusy||!storageStats" @click="saveStorageFlag('session_logs_enabled',!storageStats?.session_logs_enabled)"><span><Terminal :size="16"/><span><strong>Журнал Creator</strong><small>Информация, предупреждения и ошибки по сессиям</small></span></span><i :class="{on:storageStats?.session_logs_enabled}"><b></b></i></button><button class="storage-toggle-row" role="switch" :aria-checked="storageStats?.domain_history_enabled||false" :disabled="storageBusy||!storageStats" @click="saveStorageFlag('domain_history_enabled',!storageStats?.domain_history_enabled)"><span><Globe2 :size="16"/><span><strong>Переходы по доменам</strong><small>SNI и адреса назначений в истории сессии</small></span></span><i :class="{on:storageStats?.domain_history_enabled}"><b></b></i></button></div></article>
    <article class="storage-config"><div class="storage-config-copy"><span class="storage-kicker">02 / RETENTION POLICY</span><h2>Срок хранения записей</h2><p>Записи старше выбранного срока удаляются автоматически. Это включает сообщения Creator, ошибки, информационные события и историю доменов.</p></div><div class="storage-config-controls"><label>ХРАНИТЬ ЗАПИСИ<ThemedSelect v-model="retentionDays" label="Срок хранения журналов" :options="retentionOptions" @update:model-value="saveRetention" /></label><button class="primary" :disabled="storageBusy" @click="cleanStorage"><Trash2 :size="15"/>{{ storageBusy?'ОЧИСТКА…':'ОЧИСТИТЬ СЕЙЧАС' }}</button></div></article>
    <article class="storage-config container-rotation"><div class="rotation-icon"><ShieldCheck :size="18"/></div><div><span class="storage-kicker">03 / CONTAINER ROTATION</span><h2>Системные логи Docker</h2><p>Вывод PostgreSQL, backend и web ротируется отдельно: до 3 файлов по 10 МБ на контейнер. Удаление записей из базы не затрагивает эти журналы.</p></div><span class="rotation-limit">≈ 30 МБ <small>/ контейнер</small></span></article>
    <p class="storage-footnote"><Info :size="14"/> После удаления PostgreSQL может оставить место зарезервированным для повторного использования; показатель размера таблиц не всегда уменьшается сразу.</p>
    </div>
    <div v-else-if="settingsPane==='vpn'" class="vpn-page" aria-label="Маршрутизация трафика через Xray">
     <div class="storage-toolbar"><div><span class="storage-kicker"><ShieldCheck :size="14"/> XRAY / SESSION EGRESS</span><p>Трафик Joiner, который проходит через Creator relay, можно отправлять через VLESS или напрямую.</p></div></div>
     <div v-if="vpnError" class="storage-error" role="alert"><AlertTriangle :size="16"/>{{ vpnError }}</div>
     <article class="vpn-status-card"><span :class="['vpn-state-light',{on:vpnStatus?.running&&vpnStatus?.enabled}]"/><div class="vpn-status-copy"><span class="storage-kicker">МАРШРУТ СЕССИЙ</span><h2>{{ !vpnStatus?.configured?'Конфигурация не добавлена':vpnStatus?.enabled?'VPN включён':'VPN выключен' }}</h2><p v-if="vpnStatus?.configured">{{ vpnStatus.host }}:{{ vpnStatus.port }} · VLESS {{ vpnStatus.transport }}<span v-if="vpnStatus.name"> · {{ vpnStatus.name }}</span></p><p v-else>Добавь VLESS-ссылку, чтобы выбрать маршрут для трафика клиентов.</p><small v-if="vpnStatus?.active_sessions">{{ vpnStatus.active_sessions }} активных сессий · переключение закроет текущие TCP-соединения, звонки останутся запущенными</small><small v-else-if="vpnStatus?.configured&&!vpnStatus?.running" class="vpn-status-error">Xray не запущен. Проверь конфигурацию или пересоздай её.</small><small v-else-if="vpnStatus?.configured">Xray SOCKS5 доступен локально только Creator relay</small></div><div class="vpn-status-actions"><button class="secondary vpn-test-button" :disabled="vpnTestBusy||!vpnStatus?.configured" @click="testVpnConnection"><Activity :size="15" :class="{spinning:vpnTestBusy}"/>{{ vpnTestBusy?'ПРОВЕРЯЕМ…':'ПРОВЕРИТЬ СВЯЗЬ' }}</button><button class="vpn-switch" role="switch" :aria-checked="vpnStatus?.enabled||false" :disabled="vpnBusy||!vpnStatus?.configured" @click="toggleVpn"><i><b/></i><span>{{ vpnStatus?.enabled?'ВКЛ':'ВЫКЛ' }}</span></button></div></article>
     <div class="vpn-test-output" aria-live="polite"><p v-if="vpnTestResult" :class="vpnTestResult.ok?'success':'failure'"><Check v-if="vpnTestResult.ok" :size="15"/><AlertTriangle v-else :size="15"/><span>{{ vpnTestResult.ok?`Прокси отвечает: Google вернул HTTP ${vpnTestResult.http_status} за ${vpnTestResult.latency_ms} мс.`:`Google ответил HTTP ${vpnTestResult.http_status}; ожидался HTTP 204.` }} <small>{{ vpnTestResult.target }} · проверка идёт через временный VPN-маршрут и не меняет режим сессий</small></span></p><p v-else-if="vpnTestError" class="failure"><AlertTriangle :size="15"/><span>{{ vpnTestError }}<small>Проверка шла через временный VPN-маршрут; текущий режим не менялся.</small></span></p><p v-else><Info :size="15"/><span>Проверка отправит запрос на Google через отдельный временный Xray процесс, даже если VPN для сессий выключен.</span></p></div>
     <article class="storage-config vpn-config"><div class="storage-config-copy"><span class="storage-kicker">01 / VLESS PROFILE</span><h2>{{ vpnStatus?.configured?'Заменить VLESS-конфигурацию':'Добавить VLESS-конфигурацию' }}</h2><p>Вставь ссылку целиком. Поддерживается VLESS с XHTTP и REALITY, включая `encryption=mlkem768x25519plus`, `pbk`, `sid`, `spx`, `pqv` и XHTTP `extra`. Ссылка хранится зашифрованной; в интерфейс секреты не возвращаются. Xray проверит структуру, а доступность узла станет понятна при первом трафике.</p><p class="vpn-flow-note"><strong>Что маршрутизируется:</strong> только запросы и соединения, переданные Joiner в Creator relay. Панель, API управления звонком и сигнализация Creator продолжают использовать обычное подключение сервера.</p></div><label class="vpn-uri-label">VLESS URI<textarea v-model="vpnUri" rows="5" spellcheck="false" autocomplete="off" placeholder="vless://UUID@server:port?type=xhttp&amp;security=reality&amp;pbk=…&amp;sni=…"></textarea></label><div class="vpn-actions"><button class="primary" :disabled="vpnBusy||!vpnUri.trim()" @click="saveVpnConfig"><ShieldCheck :size="16"/>{{ vpnBusy?'ПРИМЕНЯЕМ…':'СОХРАНИТЬ И ПРОВЕРИТЬ' }}</button><button v-if="vpnStatus?.configured" class="secondary danger-outline" :disabled="vpnBusy||!!vpnStatus.active_sessions" @click="removeVpnConfig"><Trash2 :size="15"/>Удалить конфигурацию</button><small v-if="vpnStatus?.configured&&vpnStatus.active_sessions">Для удаления сначала останови все сессии.</small></div></article>
     <p class="storage-footnote"><Info :size="14"/> При включённом VPN новые подключения к сайтам идут через VLESS. Выключение возвращает прямой выход. Уже открытые TCP-соединения разрываются при смене режима и приложения переподключатся автоматически, если поддерживают это.</p>
    </div>
    <div v-else-if="settingsPane==='matrix'" class="matrix-page" aria-label="Интеграция Matrix">
     <div class="storage-toolbar"><div><span class="storage-kicker"><Layers3 :size="14"/> MATRIX / SESSION NOTIFICATIONS</span><p>Бот отправляет новые ссылки и QR, сообщает об ошибках восстановления и принимает команды только для привязанных сессий.</p></div></div>
     <div v-if="matrixError" class="storage-error" role="alert"><AlertTriangle :size="16"/>{{ matrixError }}</div>
     <article class="matrix-status-card"><span :class="['vpn-state-light',{on:matrixStatus?.configured&&matrixStatus?.sync_running}]"/><div class="vpn-status-copy"><span class="storage-kicker">MATRIX ACCOUNT</span><h2>{{ matrixStatus?.configured?'Интеграция подключена':'Аккаунт не подключён' }}</h2><p v-if="matrixStatus?.configured">{{ matrixStatus.user_id }} · {{ matrixStatus.homeserver }}</p><p v-else>Укажи homeserver и войди Matrix-аккаунтом для отправки уведомлений.</p><small v-if="matrixStatus?.error" class="vpn-status-error">{{ matrixStatus.error }}</small><small v-else-if="matrixStatus?.configured">{{ matrixStatus.sync_running?'Синхронизация команд активна':'Синхронизация запускается' }}</small></div><button v-if="matrixStatus?.configured" class="secondary danger-outline" :disabled="matrixBusy" @click="logoutMatrix"><LogOut :size="15"/>Отключить</button></article>
     <article class="storage-config matrix-greeting-config"><div class="storage-config-copy"><span class="storage-kicker">01 / FIRST MESSAGE</span><h2>Приветствие нового чата</h2><p>После привязки Matrix-пользователя бот создаст личную комнату, отправит приглашение и первым напишет это сообщение. Вставь <code>{session}</code>, чтобы подставить название сессии. Пустое поле отключает приветствие.</p></div><label class="matrix-greeting-label">ТЕКСТ ПРИВЕТСТВИЯ<textarea v-model="matrixGreeting" maxlength="500" rows="4" placeholder="Привет! Я бот PRNL. Буду присылать ссылку и QR звонка для сессии «{session}». Напишите !help для списка команд."></textarea></label><div class="vpn-actions"><button class="primary" :disabled="matrixBusy" @click="saveMatrixGreeting"><Check :size="15"/>СОХРАНИТЬ ПРИВЕТСТВИЕ</button></div></article>
     <article class="storage-config matrix-config"><div class="storage-config-copy"><span class="storage-kicker">02 / HOMESERVER & LOGIN</span><h2>{{ matrixStatus?.configured?'Подключить другой аккаунт':'Подключение Matrix' }}</h2><p>Поддерживаются http/https адреса, локальные серверы и IP с портом. Пароль не сохраняется: access token шифруется на сервере.</p></div><form class="matrix-login-form" @submit.prevent="loginMatrix"><label>Адрес homeserver<input v-model="matrixHomeserver" required maxlength="500" placeholder="https://matrix.example.org или http://192.168.1.10:8008"></label><label>Логин Matrix<input v-model="matrixUsername" required maxlength="255" autocomplete="username" placeholder="@bot:example.org или имя пользователя"></label><label>Пароль<input v-model="matrixPassword" type="password" required maxlength="1000" autocomplete="current-password" placeholder="Пароль Matrix"></label><button class="primary" :disabled="matrixBusy||!matrixHomeserver.trim()||!matrixUsername.trim()||!matrixPassword">{{ matrixBusy?'ПОДКЛЮЧЕНИЕ…':'ВОЙТИ В MATRIX' }}<ArrowUpRight :size="15"/></button></form></article>
     <article class="storage-config matrix-help"><div class="storage-config-copy"><span class="storage-kicker">03 / SESSION BOT</span><h2>Привязка и команды</h2><p>В карточке сессии открой настройки контакта и выбери Matrix-пользователя. Для локального homeserver внутри Docker используй адрес контейнера/сервиса или <code>host.docker.internal</code>, а не localhost.</p><div class="matrix-commands"><code>!help</code><span>справка по командам</span><code>!refresh ID</code><span>создать новую ссылку для привязанной сессии</span></div><p class="storage-footnote"><Info :size="14"/> Matrix E2EE пока не поддерживается. Бот сам создаст незашифрованную личную комнату и отправит приглашение; адресату нужно принять его, чтобы переписка появилась в клиенте.</p></div></article>
    </div>
    <div v-else class="panel-page" aria-label="Настройки доступа к панели">
     <div class="storage-toolbar"><div><span class="storage-kicker"><ShieldCheck :size="14"/> PANEL / HTTPS ACCESS</span><p>HTTPS для localhost и IP; серверный сертификат подписывает локальный центр PRNL. Его приватный ключ хранится только в volume на сервере.</p></div></div>
     <div v-if="panelError" class="storage-error" role="alert"><AlertTriangle :size="16"/>{{ panelError }}</div>
     <article class="panel-status-card"><span :class="['vpn-state-light',{on:panelStatus?.ready}]"/><div><span class="storage-kicker">СТАТУС HTTPS</span><h2>{{ panelStatus?.ready?'HTTPS готов':panelStatus?'Выпускаем сертификат':'Загружаем статус' }}</h2><p v-if="panelStatus?.ready" class="panel-links"><a :href="panelStatus.local_url" target="_blank" rel="noopener noreferrer">{{ panelStatus.local_url }} <External :size="13"/></a><a v-if="panelStatus.ip!=='localhost'" :href="panelStatus.url" target="_blank" rel="noopener noreferrer">{{ panelStatus.url }} <External :size="13"/></a></p><p v-else>{{ panelStatus?.error||'Ожидаем запуска HTTPS-прокси…' }}</p></div><span class="panel-auto"><Check :size="14"/> Автопродление включено</span></article>
     <article class="storage-config panel-config"><div class="storage-config-copy"><span class="storage-kicker">01 / CERTIFICATE TARGET</span><h2>Адрес панели</h2><p>Локально работает https://localhost:8080. Для доступа извне укажи IP сервера: сертификат будет действовать и для localhost, и для этого IP. Перевыпуск автоматический за 30 дней до окончания срока.</p></div><form class="panel-ip-form" @submit.prevent="savePanel"><label>LOCALHOST ИЛИ IP СЕРВЕРА<input v-model="panelIp" required maxlength="64" spellcheck="false" autocomplete="off" placeholder="localhost или 203.0.113.10"></label><button class="primary" :disabled="panelBusy||!panelIp.trim()" type="submit"><ShieldCheck :size="15"/>{{ panelBusy?'СОХРАНЯЕМ…':'СОХРАНИТЬ АДРЕС' }}</button></form></article>
     <article class="storage-config panel-admin-config"><div class="storage-config-copy"><span class="storage-kicker">02 / ADMIN CREDENTIALS</span><h2>Логин и пароль панели</h2><p>Текущий логин: <code>{{ adminUsername }}</code>. Для сохранения введи действующий пароль. {{ initialSetupRequired?'При первой настройке задай новый пароль не короче 16 символов.':'Новый пароль можно оставить пустым при смене только логина; заданный пароль должен содержать не менее 16 символов.' }}</p></div><form class="panel-admin-form" @submit.prevent="saveAdminSettings"><label>ТЕКУЩИЙ ПАРОЛЬ<input v-model="adminCurrentPassword" type="password" required autocomplete="current-password" placeholder="Подтверди текущий пароль"></label><label>НОВЫЙ ЛОГИН<input v-model="adminUsername" required maxlength="100" autocomplete="username" placeholder="Логин без пробелов"></label><label>НОВЫЙ ПАРОЛЬ{{ initialSetupRequired?"":" · НЕОБЯЗАТЕЛЬНО" }}<input v-model="adminNewPassword" type="password" minlength="16" maxlength="1000" autocomplete="new-password" :required="initialSetupRequired" placeholder="Не менее 16 символов"></label><label>ПОВТОРИ НОВЫЙ ПАРОЛЬ<input v-model="adminConfirmPassword" type="password" minlength="16" maxlength="1000" autocomplete="new-password" placeholder="Повтори новый пароль"></label><p v-if="adminError" class="error-text" role="alert">{{ adminError }}</p><button class="primary" type="submit" :disabled="adminBusy||!adminCurrentPassword||!adminUsername.trim()||(initialSetupRequired&&!adminNewPassword)"><KeyRound :size="15"/>{{ adminBusy?'СОХРАНЯЕМ…':'СОХРАНИТЬ ДОСТУП' }}</button></form></article>
     <article v-if="panelStatus?.fingerprint" class="panel-certificate"><div><span class="storage-kicker">02 / CERTIFICATE AUTHORITY</span><h2>Сертификат панели</h2><p>Серверный сертификат подписан локальным центром PRNL. Можно перевыпустить его кнопкой или скачать корневой сертификат и добавить его в доверенные на своём устройстве. Перед установкой сверь SHA-256 центра на сервере командой <code>docker compose exec panel-tls openssl x509 -in /panel-tls/ca.crt -noout -fingerprint -sha256</code>; сравни без двоеточий.</p><div class="panel-cert-actions"><button class="secondary" :disabled="panelCertificateBusy" @click="renewPanelCertificate"><RefreshCw :size="14" :class="{spinning:panelCertificateBusy}"/>{{ panelCertificateBusy?'ПОДПИСЫВАЕМ…':'ПЕРЕВЫПУСТИТЬ СЕРТИФИКАТ' }}</button><button class="secondary" @click="downloadPanelCa"><Download :size="14"/>СКАЧАТЬ ROOT CA</button></div><p class="panel-ca-note">Установи скачанный CA как доверенный корневой сертификат, чтобы убрать предупреждение браузера. Перед ручным перевыпуском сначала установи CA: новый серверный сертификат может вызвать повторный запрос доверия.</p></div><dl><div><dt>ДЕЙСТВУЕТ ДО</dt><dd>{{ new Date(panelStatus.expires_at).toLocaleString('ru-RU') }}</dd></div><div><dt>SHA-256 СЕРВЕРНОГО СЕРТИФИКАТА</dt><dd class="panel-fingerprint">{{ panelStatus.fingerprint }}</dd></div><div><dt>SHA-256 ROOT CA</dt><dd class="panel-fingerprint">{{ panelStatus.ca_fingerprint||'Выпускается…' }}</dd></div></dl></article>
     <article class="panel-access-card"><div><span class="storage-kicker">03 / PRIVATE ENTRY PATH</span><h2>Секретный адрес</h2><p>Панель открывается только по этому случайному пути. Корневой адрес и любые другие пути возвращают 404. Смена адреса сразу отключит старую ссылку и переоткроет эту вкладку.</p><code>{{ panelStatus?.url||'Загружаем адрес…' }}</code></div><div class="panel-access-actions"><button class="secondary" :disabled="!panelStatus" @click="copyPanelAddress"><Copy :size="15"/>Скопировать адрес</button><button class="secondary danger-outline" :disabled="panelPathBusy" @click="rotatePanelPath"><RotateCcw :size="15"/>{{ panelPathBusy?'МЕНЯЕМ…':'СМЕНИТЬ АДРЕС' }}</button></div></article>
     <p class="storage-footnote"><Info :size="14"/> Браузер изначально не доверяет локальному центру подписи. Чтобы убрать предупреждение, скачай CA, сверь отпечаток с тем, что показывает сервер, добавь его в доверенные корневые сертификаты и перезапусти браузер. HTTP на порту {{ panelStatus?.http_port||8080 }} перенаправляется на HTTPS; bind {{ panelStatus?.bind_ip||'127.0.0.1' }}, HTTPS-порт {{ panelStatus?.https_port||443 }}.</p>
    </div>
   </section>
   <section v-if="tab==='about'" class="about-grid" aria-label="Информация о проекте">
    <article class="about-card about-primary"><div class="about-kicker"><span class="about-icon"><Info :size="18"/></span><span>PRNL + / CONTROL PANEL</span></div><h2>Панель управления туннельными сессиями</h2><p>prnl + — локальная административная панель для настройки платформенных аккаунтов, запуска Creator-сессий и наблюдения за их состоянием.</p><div class="about-tags"><span>Vue 3</span><span>Python</span><span>PostgreSQL</span><span>Docker</span></div></article>
    <article class="about-card"><div class="about-kicker"><span class="about-icon"><Github :size="18"/></span><span>АВТОР ПАНЕЛИ</span></div><h2>SlyFox-maker</h2><p>Панель создана <a href="https://github.com/SlyFox-maker" target="_blank" rel="noopener noreferrer">SlyFox-maker</a>.</p><a class="about-link" href="https://github.com/SlyFox-maker" target="_blank" rel="noopener noreferrer">Профиль на GitHub <External :size="14"/></a></article>
    <article class="about-card"><div class="about-kicker"><span class="about-icon"><Globe2 :size="18"/></span><span>ИСПОЛЬЗУЕМЫЙ ПРОЕКТ</span></div><h2>whitelist-bypass</h2><p>Панель использует Creator-компоненты проекта <a href="https://github.com/kulikov0/whitelist-bypass" target="_blank" rel="noopener noreferrer">whitelist-bypass</a>, созданного <a href="https://github.com/kulikov0" target="_blank" rel="noopener noreferrer">kulikov0</a>.</p><a class="about-link" href="https://github.com/kulikov0/whitelist-bypass" target="_blank" rel="noopener noreferrer">Открыть репозиторий <External :size="14"/></a></article>
   </section>
   </div></Transition></div>
   <footer class="page-footer"><span>PRNL <span class="footer-plus">+</span> <span class="muted">/ Панель управления туннелями</span></span><span>v0.1.0 <span class="footer-separator">·</span> Headless engine</span></footer>
  </div></div>
 </div>
 <Transition @before-enter="enableEntering" name="dialog" @before-leave="disableLeaving"><div v-if="modal&&authed" class="modal-backdrop" @click.self="modal==='desktop-creator'?stopDesktopCreator():modal!=='browser'&&closeModal()"><section :class="['dialog',{wide:['browser','desktop-creator','session-logs','session-destinations','restart-links'].includes(modal)}]" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div class="dialog-heading"><h2 id="dialog-title">{{ modal==='account'?'Подключить платформу':modal==='session'?'Новая сессия':modal==='session-recipient'?'Контакт сессии':modal==='restart-links'?'Новые ссылки звонков':modal==='rename-session'?'Переименовать сессию':modal==='browser'?'Вход в аккаунт':modal==='desktop-creator'?`Desktop Creator · ${selected?.name||''}`:modal==='session-logs'?`Журнал · ${selectedLogSession?.name||''}`:modal==='session-destinations'?`Переходы · ${selectedDestinationSession?.name||''}`:modal==='link-qr'?`QR-код · ${selectedLinkSession?.name||''}`:selected?.name }}</h2><button class="icon-button" :disabled="modal==='restart-links'&&restartBusy" aria-label="Закрыть" @click="modal==='browser'?cancelBrowser():modal==='desktop-creator'?stopDesktopCreator():closeModal()"><X :size="20"/></button></div>
 <Transition @before-enter="enableEntering" name="notice" @before-leave="disableLeaving"><div v-if="error" role="alert" class="alert error">{{ error }}</div></Transition>
 <form v-if="modal==='account'" @submit.prevent="createAccount"><label>Название аккаунта<input v-model="accountForm.name" required maxlength="100" placeholder="Например, домашний Телемост"></label><label>Платформа<select v-model="accountForm.platform"><option v-for="p in platforms" :value="p.id">{{ p.label }}</option></select></label><label v-if="accountForm.platform==='bitrix'">Адрес портала<input v-model="accountForm.portal" type="url" required placeholder="https://name.bitrix24.ru"></label><p class="hint">После добавления откройте Desktop Creator и войдите в аккаунт платформы.</p><button class="primary full" :disabled="busy">Добавить аккаунт<ChevronRight :size="17"/></button></form>
 <div v-if="modal==='account-options'"><section v-if="selected&&['dion','bitrix'].includes(selected.platform)" class="credential-box"><div class="credential-heading"><ShieldCheck :size="17"/><strong>{{ selected.has_credentials?'Повторный вход настроен':'Автоматический повторный вход' }}</strong><span v-if="selected.has_credentials" class="credential-state"><span class="dot green"></span>Зашифровано</span></div><p>Creator DION/Битрикс24 использует эти данные, если сохранённая сессия истекла. Обновлённые cookies сохраняются обратно в панели. Пароль не показывается и не включается в журналы.</p><form @submit.prevent="saveCredentials"><label>Email или логин<input v-model="credentialForm.email" type="email" required maxlength="320" autocomplete="username" placeholder="name@example.com"></label><label>Пароль<input v-model="credentialForm.password" type="password" required maxlength="1000" autocomplete="new-password" :placeholder="selected.has_credentials?'Введите новый пароль для замены':'Введите пароль аккаунта'"></label><button class="primary full" :disabled="busy">{{ selected.has_credentials?'Обновить данные':'Сохранить для автовхода' }}<ShieldCheck :size="16"/></button></form><button v-if="selected.has_credentials" class="credential-remove" :disabled="busy" @click="deleteCredentials">Удалить сохранённые данные</button></section><p v-else class="hint credential-note">Автоматический повторный вход по паролю доступен Creator для DION и Битрикс24. Для этой платформы откройте Desktop Creator и войдите в профиль Chromium.</p><p class="hint">Вход выполняется в профиле Chromium программы Desktop Creator. Сохранённые данные аккаунта хранятся в зашифрованном виде.</p><button class="primary full" :disabled="busy" @click="openDesktopCreator"><Monitor :size="17"/>{{ selected?.desktop_creator_open?'Открыть экран Desktop Creator':'Запустить Desktop Creator' }}</button></div>
 <form v-if="modal==='rename-session'" @submit.prevent="saveSessionName"><label>Название сессии<input v-model="renameValue" required minlength="1" maxlength="100" autofocus placeholder="Название"></label><p class="hint">Журналы, ссылка и настройки сессии сохранятся.</p><button class="primary full" :disabled="busy||!renameValue.trim()">Сохранить название</button></form>
 <form v-if="modal==='session'" @submit.prevent="createSession"><label>Название сессии<input v-model="sessionForm.name" required maxlength="100" placeholder="Например, мой телефон"></label><label>Аккаунт<select v-model="sessionForm.account_id" required><option value="" disabled>Выберите настроенный аккаунт</option><option v-for="a in accounts.filter(a=>a.has_cookies||a.has_credentials)" :value="a.id">{{ a.name }} · {{ platform(a.platform) }}</option></select></label><p v-if="!configured" class="hint">Сначала добавьте аккаунт и войдите через Desktop Creator либо настройте поддерживаемый автовход.</p><p v-if="accounts.find(a=>a.id===sessionForm.account_id)?.platform==='telemost'" class="hint">Телемост запускает upstream headless Creator. Панель передаёт ему сохранённые cookies и показывает его состояние и журнал.</p><label>Профиль ресурсов<select v-model="sessionForm.resources"><option value="moderate">Экономный</option><option value="default">Стандартный</option><option value="unlimited">Повышенный</option></select></label><label>Существующая комната <span class="muted">необязательно</span><input v-model="sessionForm.room" placeholder="Оставьте пустым для нового звонка"></label><fieldset class="recipient-fields"><legend>Для кого сессия <span class="muted">необязательно</span></legend><label>Способ связи<ThemedSelect v-model="sessionForm.recipient_type" label="Способ связи" :options="recipientOptions" @update:model-value="recipientTypeChanged" /></label><label v-if="sessionForm.recipient_type==='other'">Название сервиса<input v-model="sessionForm.recipient_label" maxlength="100" placeholder="Например, Signal"></label><label v-if="sessionForm.recipient_type==='telegram'">Username или Telegram ID<input v-model="sessionForm.recipient_value" maxlength="500" placeholder="@username или числовой ID"></label><label v-else-if="sessionForm.recipient_type==='whatsapp'">Номер или WhatsApp ID<input v-model="sessionForm.recipient_value" maxlength="500" placeholder="Международный номер, например +14155550123"></label><label v-else-if="sessionForm.recipient_type==='other'">Ссылка или контакт<input v-model="sessionForm.recipient_value" maxlength="500" placeholder="https://… или имя пользователя"></label><p v-if="sessionForm.recipient_type==='telegram'" class="hint">По @username откроется t.me, по числовому ID — Telegram deep link.</p><p v-else-if="sessionForm.recipient_type==='whatsapp'" class="hint">Для прямого чата нужен номер в международном формате. WhatsApp ID без номера не всегда позволяет построить публичную ссылку.</p><p v-else-if="sessionForm.recipient_type==='other'" class="hint">Ссылка появится рядом с QR, если указать адрес http:// или https://.</p></fieldset><p class="hint">Одна сессия обслуживает одно устройство. Режим DC / Video выбирается на стороне Joiner.</p><button class="primary full" :disabled="busy||!sessionForm.account_id">Создать сессию</button></form>
 <form v-if="modal==='session-recipient'" @submit.prevent="saveRecipient"><fieldset class="recipient-fields"><legend>Контакт для «{{ recipientTarget?.name }}»</legend><label>Способ связи<ThemedSelect v-model="sessionForm.recipient_type" label="Способ связи" :options="recipientOptions" @update:model-value="recipientTypeChanged" /></label><label v-if="sessionForm.recipient_type==='other'">Название сервиса<input v-model="sessionForm.recipient_label" maxlength="100" placeholder="Например, Signal"></label><label v-if="sessionForm.recipient_type==='telegram'">Username или Telegram ID<input v-model="sessionForm.recipient_value" maxlength="500" placeholder="@username или числовой ID"></label><label v-else-if="sessionForm.recipient_type==='whatsapp'">Номер или WhatsApp ID<input v-model="sessionForm.recipient_value" maxlength="500" placeholder="Международный номер, например +14155550123"></label><label v-else-if="sessionForm.recipient_type==='other'">Ссылка или контакт<input v-model="sessionForm.recipient_value" maxlength="500" placeholder="https://… или имя пользователя"></label><p v-if="sessionForm.recipient_type==='telegram'" class="hint">По @username откроется t.me, по числовому ID — Telegram deep link.</p><p v-else-if="sessionForm.recipient_type==='whatsapp'" class="hint">Для прямого чата нужен номер в международном формате. ID без телефонного номера не всегда даёт прямую ссылку.</p><p v-else-if="sessionForm.recipient_type==='other'" class="hint">Ссылка появится рядом с QR, если указать адрес http:// или https://.</p></fieldset><fieldset class="recipient-fields matrix-recipient"><legend>Получатель Matrix <span class="muted">необязательно</span></legend><template v-if="matrixStatus?.configured"><label>Поиск пользователя<input v-model="matrixSearch" maxlength="255" placeholder="@user:server.org" autocomplete="off" @input="searchMatrixDirectory"></label><div v-if="matrixSearching||matrixSearchDone||matrixSearchError||matrixUsers.length" class="matrix-user-results"><p v-if="matrixSearching">Ищем в каталоге Matrix…</p><p v-if="matrixSearchError" class="matrix-search-error">{{ matrixSearchError }}</p><button v-for="user in matrixUsers" :key="user.user_id" type="button" class="matrix-user-option" @click="selectMatrixUser(user)"><img v-if="(user.has_avatar||user.unverified)&&!matrixAvatarUnavailable[user.user_id]" :src="matrixAvatarUrl(user.user_id)" alt="" @error="matrixAvatarFailed(user.user_id)"/><span v-else class="matrix-avatar-placeholder"><UserRound :size="15"/></span><span><strong>{{ user.display_name }}</strong><small>{{ user.unverified?"Полный ID · проверим при привязке":user.user_id }}</small></span></button><p v-if="matrixSearchDone&&!matrixSearching&&!matrixSearchError&&!matrixUsers.length&&!sessionForm.matrix_user_id">Ничего не найдено. Введи полный Matrix ID вида @user:server — он проверяется через профиль даже при закрытом каталоге.</p></div><div v-if="sessionForm.matrix_user_id" class="matrix-selected-user"><img v-if="!matrixAvatarUnavailable[sessionForm.matrix_user_id]" :src="matrixAvatarUrl(sessionForm.matrix_user_id)" alt="" @load="matrixAvatarLoaded(sessionForm.matrix_user_id)" @error="matrixAvatarFailed(sessionForm.matrix_user_id)"/><span v-else class="matrix-avatar-placeholder"><UserRound :size="15"/></span><span><strong>{{ sessionForm.matrix_display_name||sessionForm.matrix_user_id }}</strong><small>{{ sessionForm.matrix_user_id }}</small></span><button type="button" class="icon-button" aria-label="Отвязать Matrix пользователя" @click="clearMatrixUser"><X :size="15"/></button></div></template><p v-else-if="matrixLoading" class="hint">Проверяем подключение Matrix…</p><p v-else-if="matrixError" class="hint">Не удалось проверить Matrix: {{ matrixError }} <button type="button" class="inline-link" @click="loadMatrix">Повторить</button></p><p v-else class="hint">Сначала подключи Matrix в настройках. <button type="button" class="inline-link" @click="closeModal();tab='settings';settingsPane='matrix';loadMatrix()">Открыть настройки Matrix</button></p><p class="hint">Новые ссылки и QR будут отправляться этому пользователю. Команды <code>!help</code> и <code>!refresh ID</code> обновляют только привязанный звонок.</p></fieldset><button class="primary full" :disabled="busy" type="submit">Сохранить контакт</button></form>
 <div v-if="modal==='restart-links'" class="restart-dialog"><div class="restart-dialog-summary"><span class="interrupted-icon"><RotateCcw :size="18"/></span><p>{{ restartBusy?`Перезапускаем сессии и ждём новые ссылки · ${restartReadyCount}/${restartTargets.length} готово`:`Готово ссылок: ${restartReadyCount} из ${restartTargets.length}. Проверь адресатов и отправь каждому его сообщение.` }}</p><button v-if="restartReadyCount" class="secondary" :disabled="restartReadyCount===0" @click="copyAllRestartMessages"><Copy :size="15"/>Скопировать готовые сообщения</button></div><article v-for="s in restartSessions" :key="s.id" class="restart-message-card"><div class="restart-message-heading"><div><span class="eyebrow">КОМУ ОТПРАВИТЬ</span><strong>{{ s.recipient_type||s.recipient_label?recipientDisplay(s):'Получатель не указан' }}</strong></div><a v-if="recipientHref(s)" class="restart-recipient-link" :href="recipientHref(s)" target="_blank" rel="noopener noreferrer"><External :size="14"/>Открыть чат</a><span :class="['restart-status',{ready:restartLinks[s.id],failed:restartFailures[s.id]}]"><i></i>{{ restartFailures[s.id]|| (restartLinks[s.id]?'Новая ссылка готова':'Создаём звонок…') }}</span></div><label>СООБЩЕНИЕ<textarea readonly rows="2" :value="restartMessage(s)|| (restartFailures[s.id]?'Ссылка не создана. Открой журнал сессии, чтобы узнать причину.':'Ожидаем новую ссылку…')"></textarea></label><button v-if="restartMessage(s)" class="secondary restart-copy" @click="copyRestartMessage(s)"><Copy :size="14"/>Скопировать сообщение</button></article><p class="restart-note">Панель не отправляет сообщения автоматически. Новые ссылки появятся только после успешного запуска Creator.</p></div>
 <div v-if="modal==='session-logs' && selectedLogSession" class="session-detail">
  <div class="session-detail-top"><div><span class="eyebrow">{{ platform(account(selectedLogSession.account_id)?.platform||'') }} · {{ account(selectedLogSession.account_id)?.name }}</span><p><span :class="['badge',sessions.find(s=>s.id===selectedLogSession?.id)?.state||selectedLogSession.state]"><span class="dot"></span>{{ stateName(sessions.find(s=>s.id===selectedLogSession?.id)?.state||selectedLogSession.state) }}</span></p></div><p class="hint">События и ошибки этой сессии</p></div>
  <div class="session-log-head"><div><h3>Журнал сессии</h3><p>Новые записи сверху · секреты и ссылки скрыты.</p></div><button class="secondary" :disabled="logBusy" @click="fetchLogs()"><RefreshCw :size="15"/>Обновить</button></div>
  <div class="session-log-filters"><label>Уровень<select v-model="logLevel" @change="fetchLogs()"><option value="all">Все уровни</option><option value="error">Ошибки</option><option value="warning">Предупреждения</option><option value="info">Информация</option></select></label><label>С<input v-model="logSince" type="datetime-local" @change="fetchLogs()"></label><label>По<input v-model="logUntil" type="datetime-local" @change="fetchLogs()"></label><button class="secondary" @click="logSince='';logUntil='';logLevel='all';fetchLogs()">Сбросить</button></div>
  <div v-if="logError" class="alert error" role="alert">{{ logError }}</div>
  <div class="log-console session-log-console" tabindex="0" aria-label="Журнал сессии, новые записи сверху"><div v-if="!logs.length" class="log-empty">{{ logBusy?'Загружаем журнал…':'Для выбранных фильтров записей нет.' }}</div><div v-for="line in logs" :key="line.id" :class="['log-line',line.level]"><span class="log-seq">{{ String(line.id).slice(-5).padStart(5,'0') }}</span><time :datetime="line.at">{{ dateTime(line.at) }}</time><span class="log-level">{{ line.level.toUpperCase() }}</span><code class="log-message">{{ displayLogMessage(line.message) }}</code></div></div>
  <div class="session-log-footer"><button v-if="logHasMore" class="secondary" :disabled="logBusy" @click="fetchLogs('older')">Показать более ранние</button><span>{{ logs.length }} записей · новые сверху · обновление каждые 2,5 с</span></div>
 </div>
 <div v-if="modal==='session-destinations' && selectedDestinationSession" class="session-detail">
  <div class="session-detail-top"><div><span class="eyebrow">{{ platform(account(selectedDestinationSession.account_id)?.platform||'') }} · {{ account(selectedDestinationSession.account_id)?.name }}</span><p><span class="badge running"><Globe2 :size="13"/> Сетевые назначения</span></p></div><p class="hint">Домены, к которым подключался Creator. HTTPS показывает домен, путь ссылки зашифрован.</p></div>
  <div class="session-log-head"><div><h3>Домены и время подключений</h3><p>{{ destinations.length }} событий в выборке · новые сверху · автообновление</p></div><button class="secondary" :disabled="destinationBusy" @click="fetchDestinations()"><RefreshCw :size="15"/>Обновить</button></div>
  <div class="session-log-filters destination-filters"><label class="destination-search">Поиск домена<input v-model="destinationDomain" type="search" placeholder="Например, yandex.ru" @input="fetchDestinations()"></label><label>Дата и время от<input v-model="destinationSince" type="datetime-local" @change="fetchDestinations()"></label><label>Дата и время до<input v-model="destinationUntil" type="datetime-local" @change="fetchDestinations()"></label><button class="secondary" @click="destinationDomain='';destinationSince='';destinationUntil='';fetchDestinations()">Сбросить</button></div>
  <div v-if="destinationError" class="alert error" role="alert">{{ destinationError }}</div>
  <div class="destination-list" tabindex="0" aria-label="История доменных подключений, новые сверху"><div v-if="!destinations.length" class="log-empty">{{ destinationBusy?'Загружаем переходы…':'Для выбранных фильтров подключений нет.' }}</div><div v-else class="destination-list-head"><span>EVENT</span><span></span><span>НАЗНАЧЕНИЕ / DOMAIN</span><span>PORT</span><span>ВРЕМЯ</span></div><article v-for="item in destinations" :key="item.id" class="destination-item"><span class="destination-seq">{{ String(item.id).slice(-5).padStart(5,'0') }}</span><span class="destination-icon"><Globe2 :size="15"/></span><div class="destination-host"><small>TLS / HOST</small><strong>{{ item.domain }}</strong></div><span class="destination-port">TCP :{{ item.port }}</span><time :datetime="item.at">{{ dateTime(item.at) }}</time></article></div>
  <div class="session-log-footer"><button v-if="destinationHasMore" class="secondary" :disabled="destinationBusy" @click="fetchDestinations('older')">Показать более ранние</button><span>{{ destinations.length }} подключений · новые сверху</span></div>
 </div>
 <div v-if="modal==='link-qr'" class="qr-dialog-body"><p v-if="!selectedLinkCanShare()" class="hint">Сессия не активна. Перезапусти её, чтобы получить новую ссылку и QR-код.</p><p v-else-if="qrLoading" class="hint">Создаём QR-код…</p><template v-else-if="joinLink&&qrDataUrl"><div class="qr-card qr-card-large"><img :src="qrDataUrl" alt="QR-код ссылки звонка" width="320" height="320"></div><a :href="joinLink" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">{{ joinLink }}</a><div class="qr-copy-actions"><button class="secondary" :disabled="copyBusy||!selectedLinkCanShare()" @click="copySessionLink"><Copy :size="16"/>{{ copyStatus==='link'?'Ссылка скопирована':'Скопировать ссылку' }}</button><button class="primary" :disabled="copyBusy||!qrImageBlob||!selectedLinkCanShare()" @click="copyQrImage"><ImageDown :size="17"/>{{ copyStatus==='qr'?'QR-код скопирован':'Скопировать QR как PNG' }}</button></div><span v-if="copyStatus" class="qr-copy-status" role="status">{{ copyStatus==='qr'?'Картинка QR-кода в буфере обмена.':'Ссылка в буфере обмена.' }}</span></template></div>
 <div v-if="modal==='browser'" class="browser-panel"><p class="hint">{{ selected?.name }} · Нажмите поле на экране, затем введите текст ниже. Пройдите вход и CAPTCHA на сайте. После успешного входа сохраните cookies.</p><div v-if="frameError" class="alert error">{{ frameError }}</div><div class="browser-screen"><img v-if="frame" ref="remoteImage" :src="frame" width="1100" height="760" alt="Интерактивный экран браузера авторизации" @click="clickFrame"><div v-else class="browser-loading">Загружаем экран…</div></div><form class="remote-input" @submit.prevent="typeRemote"><input v-model="remoteText" type="password" autocomplete="off" aria-label="Текст для выбранного поля браузера" placeholder="Логин, пароль или код — отправить в выбранное поле"><button class="secondary" :disabled="busy||!remoteText">Ввести</button></form><div class="browser-keys"><button v-for="key in ['Tab','Enter','Backspace','Control+A','Escape']" class="key-button" :disabled="busy" @click="browserAction({action:'key',key})">{{ key }}</button><button class="key-button" @click="browserAction({action:'scroll',delta:-500})">↑ Прокрутить</button><button class="key-button" @click="browserAction({action:'scroll',delta:500})">↓ Прокрутить</button></div><div class="browser-footer"><button class="secondary" :disabled="busy" @click="cancelBrowser">Закрыть без сохранения</button><button class="secondary" :disabled="busy" @click="downloadBrowserCookies">Скачать JSON</button><button class="primary" :disabled="busy" @click="saveBrowser"><ShieldCheck :size="17"/>Сохранить cookies для Creator</button></div><small>После сохранения звонок создаёт upstream Creator. Его ответ об авторизации появится в журнале сессии.</small></div>
 <div v-if="modal==='desktop-creator'" class="browser-panel"><p class="hint">Это настоящее окно Desktop Creator на сервере с его профилем Chromium. Войдите в Telemost внутри окна. Штатная кнопка Export Cookies сохраняет ZIP на сервере; скачайте его кнопкой ниже.</p><div v-if="frameError" class="alert error">{{ frameError }}</div><div class="browser-screen"><img v-if="frame" ref="remoteImage" :src="frame" width="1200" height="800" alt="Окно программы Desktop Creator на сервере" @click="clickDesktopCreator"><div v-else class="browser-loading">Подключаем Desktop Creator…</div></div><form class="remote-input" @submit.prevent="typeDesktopCreator"><input v-model="remoteText" type="password" autocomplete="off" aria-label="Текст для выбранного поля Desktop Creator" placeholder="Текст для выбранного поля — отправить в Creator"><button class="secondary" :disabled="busy||!remoteText">Ввести</button></form><div class="browser-keys"><button v-for="key in ['Tab','Enter','Backspace','Control+A','Escape']" class="key-button" :disabled="busy" @click="desktopCreatorAction({action:'key',key})">{{ key }}</button><button class="key-button" @click="desktopCreatorAction({action:'scroll',delta:-500})">↑ Прокрутить</button><button class="key-button" @click="desktopCreatorAction({action:'scroll',delta:500})">↓ Прокрутить</button></div><div v-if="desktopCookieStatus" class="alert" role="status">{{ desktopCookieStatus }}</div><div class="browser-footer"><button class="secondary" :disabled="busy" @click="checkDesktopCreatorCookies">Проверить cookies</button><button class="primary" :disabled="busy" @click="downloadDesktopCreatorExport">Скачать экспорт Creator (.zip)</button><button class="secondary" :disabled="busy" @click="downloadDesktopCreatorCookies">Скачать JSON</button><button class="secondary" :disabled="busy" @click="stopDesktopCreator">Закрыть Desktop Creator</button><button class="secondary" :disabled="busy" @click="importDesktopCreatorCookies"><ShieldCheck :size="17"/>Перенести в аккаунт</button></div><small>ZIP формируется тем же экспортёром, что и штатная Export Cookies, и скачивается прямо в браузер. Файл содержит секреты авторизации.</small></div>
 </section></div></Transition>
</template>
