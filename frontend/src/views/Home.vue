<template>
  <div class="home-container">
    <!-- 顶部导航栏 -->
    <nav class="navbar">
      <div class="nav-brand">MIROFISH</div>
      <div class="nav-links">
        <SaldoBadge />
        <LanguageSwitcher />
        <a href="https://github.com/666ghj/MiroFish" target="_blank" class="github-link">
          {{ $t('nav.visitGithub') }} <span class="arrow">↗</span>
        </a>
      </div>
    </nav>

    <div class="main-content">
      <!-- 上半部分：Hero 区域 -->
      <section class="hero-section">
        <div class="hero-left">
          <div class="tag-row">
            <span class="orange-tag">{{ $t('home.tagline') }}</span>
            <span class="version-text">{{ $t('home.version') }}</span>
          </div>
          
          <h1 class="main-title">
            {{ $t('home.heroTitle1') }}<br>
            <span class="gradient-text">{{ $t('home.heroTitle2') }}</span>
          </h1>
          
          <div class="hero-desc">
            <p>
              <i18n-t keypath="home.heroDesc" tag="span">
                <template #brand><span class="highlight-bold">{{ $t('home.heroDescBrand') }}</span></template>
                <template #agentScale><span class="highlight-orange">{{ $t('home.heroDescAgentScale') }}</span></template>
                <template #optimalSolution><span class="highlight-code">{{ $t('home.heroDescOptimalSolution') }}</span></template>
              </i18n-t>
            </p>
            <p class="slogan-text">
              {{ $t('home.slogan') }}<span class="blinking-cursor">_</span>
            </p>
          </div>
           
          <div class="decoration-square"></div>
        </div>
        
        <div class="hero-right">
          <!-- Logo 区域 -->
          <div class="logo-container">
            <img src="../assets/logo/MiroFish_logo_left.jpeg" :alt="$t('home.logoAlt')" class="hero-logo" />
          </div>
          
          <button class="scroll-down-btn" @click="scrollToBottom">
            ↓
          </button>
        </div>
      </section>

      <!-- 下半部分：双栏布局 -->
      <section class="dashboard-section">
        <!-- 左栏：状态与步骤 -->
        <div class="left-panel">
          <div class="panel-header">
            <span class="status-dot">■</span> {{ $t('home.systemStatus') }}
          </div>
          
          <h2 class="section-title">{{ $t('home.systemReady') }}</h2>
          <p class="section-desc">
            {{ $t('home.systemReadyDesc') }}
          </p>
          
          <!-- 数据指标卡片 -->
          <div class="metrics-row">
            <div class="metric-card">
              <div class="metric-value">{{ $t('home.metricLowCost') }}</div>
              <div class="metric-label">{{ $t('home.metricLowCostDesc') }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-value">{{ $t('home.metricHighAvail') }}</div>
              <div class="metric-label">{{ $t('home.metricHighAvailDesc') }}</div>
            </div>
          </div>

          <!-- Qué mejoramos sobre el original (parche MX) -->
          <section class="mejoras" :aria-label="$t('home.mejorasTitulo')">
            <div class="steps-header">
              <span class="diamond-icon">◇</span> {{ $t('home.mejorasTitulo') }}
            </div>
            <ul class="mejoras-list">
              <li v-for="n in 4" :key="n" class="mejora-item">
                <div class="step-title">
                  {{ $t(`home.mejora${n}Titulo`) }}
                  <span v-if="n === 4" class="mejora-tag">{{ $t('home.nuevo') }}</span>
                </div>
                <div class="step-desc">{{ $t(`home.mejora${n}Desc`) }}</div>
              </li>
            </ul>
          </section>

          <!-- 项目模拟步骤介绍 (新增区域) -->
          <div class="steps-container">
            <div class="steps-header">
               <span class="diamond-icon">◇</span> {{ $t('home.workflowSequence') }}
            </div>
            <div class="workflow-list">
              <div class="workflow-item">
                <span class="step-num">01</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step01Title') }}</div>
                  <div class="step-desc">{{ $t('home.step01Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">02</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step02Title') }}</div>
                  <div class="step-desc">{{ $t('home.step02Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">03</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step03Title') }}</div>
                  <div class="step-desc">{{ $t('home.step03Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">04</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step04Title') }}</div>
                  <div class="step-desc">{{ $t('home.step04Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">05</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step05Title') }}</div>
                  <div class="step-desc">{{ $t('home.step05Desc') }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 右栏：交互控制台 -->
        <div class="right-panel">
          <div class="console-box">
            <!-- Chat "Afinar tu idea" (parche MX): se cuenta la idea, la IA pregunta lo que falta, se aprueba y se lanza -->
            <Teleport to="body" :disabled="!esMovil">
            <div class="console-section chat-section" :class="{ abierta: hojaAbierta, 'drag-over': isDragOver }"
                 @dragover.prevent="handleDragOver" @dragleave.prevent="handleDragLeave" @drop.prevent="handleDrop">
              <button type="button" class="hoja-asa" @click="hojaAbierta = false" aria-label="Bajar la conversación"><span></span></button>
              <div class="console-header">
                <span class="console-label">01 / Tu idea</span>
                <span class="console-meta">La afinamos juntos antes de lanzarla</span>
              </div>
              <div class="chat" ref="chatEl" aria-live="polite">
                <template v-if="!chat.length">
                  <p class="chat-vacio">Cuéntame tu idea como se la contarías a alguien: qué ofreces, a quién y a qué precio. Si tienes un documento, adjúntalo con el botón +.</p>
                  <div class="modos" role="radiogroup" aria-label="Qué vas a validar">
                    <button type="button" role="radio" :aria-checked="!modoGuion" :class="{ sel: !modoGuion }" @click="modoGuion = false">Una idea o negocio</button>
                    <button type="button" role="radio" :aria-checked="modoGuion" :class="{ sel: modoGuion }" @click="modoGuion = true">El guion de un video</button>
                  </div>
                </template>
                <div v-for="(m, i) in chat" :key="i" class="msg" :class="m.rol">
                  <p>{{ m.texto }}</p>
                  <div v-if="m.preguntas?.length && i === chat.length - 1 && !pensando" class="preguntas">
                    <div v-for="q in m.preguntas" :key="q.id" class="pregunta">
                      <span>{{ q.pregunta }}</span>
                      <div class="chips">
                        <button v-for="o in q.opciones" :key="o" type="button" :class="{ sel: respuestas[q.id] === o }" @click="responderChip(q, o)">{{ o }}</button>
                      </div>
                    </div>
                    <button v-if="Object.keys(respuestas).length" type="button" class="enviar-chips" @click="enviarChips">Enviar respuestas</button>
                  </div>
                </div>
                <div v-if="pensando" class="msg asistente pensando" aria-label="Pensando"><i></i><i></i><i></i></div>
                <div v-if="ficha" class="ficha">
                  <div class="ficha-titulo">{{ ficha.modo === 'guion' ? 'Así entendimos tu anuncio' : 'Así entendimos tu idea' }}</div>
                  <dl>
                    <template v-for="c in (ficha.modo === 'guion' ? CAMPOS_GUION : CAMPOS_FICHA)" :key="c.k">
                      <template v-if="ficha.ficha?.[c.k]"><dt>{{ c.label }}</dt><dd>{{ ficha.ficha[c.k] }}</dd></template>
                    </template>
                  </dl>
                  <ol v-if="ficha.ficha?.escenas?.length" class="escenas">
                    <li v-for="e in ficha.ficha.escenas" :key="e.n"><b>Escena {{ e.n }}</b> {{ e.texto }}</li>
                  </ol>
                  <p v-if="ficha.ficha?.supuestos?.length" class="supuestos">Supuesto: {{ ficha.ficha.supuestos.join(' · ') }}</p>
                  <p class="ficha-instr"><b>La ciudad va a predecir:</b> {{ ficha.instruccion }}</p>
                  <button type="button" class="aprobar" @click="aprobarYLanzar" :disabled="loading">Aprobar y lanzar →</button>
                  <button type="button" class="ajustar" @click="ficha = null">Ajustar</button>
                </div>
              </div>
              <div v-if="files.length" class="adjuntos">
                <span v-for="(file, index) in files" :key="index" class="adjunto">
                  {{ file.name }}<button type="button" :aria-label="`Quitar ${file.name}`" @click="removeFile(index)">×</button>
                </span>
              </div>
              <input ref="fileInput" type="file" multiple accept=".pdf,.md,.txt" @change="handleFileSelect" style="display: none" :disabled="loading" />
              <div class="chat-input">
                <button type="button" class="adjuntar" :aria-label="files.length ? `${files.length} documento(s) adjunto(s)` : 'Adjuntar documento'" @click="triggerFileInput">
                  +<b v-if="files.length">{{ files.length }}</b>
                </button>
                <textarea
                  @focus="hojaAbierta = true"
                  v-model="formData.simulationRequirement"
                  rows="2"
                  :placeholder="chat.length ? 'Responde o agrega detalles…' : (modoGuion ? 'Pega aquí el guion o la transcripción de tu video' : 'Ej.: Quiero vender cajas de pan de yema a domicilio los fines de semana')"
                  :disabled="pensando || loading"
                  @keydown.enter.exact.prevent="enviar()"
                ></textarea>
                <button type="button" class="enviar" aria-label="Enviar" @click="enviar()" :disabled="(!formData.simulationRequirement.trim() && !Object.keys(respuestas).length) || pensando">→</button>
              </div>
              <button v-if="chat.length && !ficha && !pensando" type="button" class="lanzar-asi" @click="enviar(true)">Lanzar así: completa tú lo que falta</button>
            </div>
            </Teleport>

            <div class="console-section btn-section">
              <button type="button" class="modo-avanzado" @click="startAvanzado" :disabled="!files.length || !ideaTexto || loading">
                Modo avanzado: paso a paso (adjunta un documento con +)
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- 历史项目数据库 -->
      <HistoryDatabase />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import HistoryDatabase from '../components/HistoryDatabase.vue'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import SaldoBadge from '../components/SaldoBadge.vue'

const router = useRouter()

// 表单数据
const formData = ref({
  simulationRequirement: ''
})

// 文件列表
const files = ref([])

// 状态
const loading = ref(false)
const error = ref('')
const isDragOver = ref(false)

// 文件输入引用
const fileInput = ref(null)

// 计算属性:是否可以提交
const canSubmit = computed(() => {
  return formData.value.simulationRequirement.trim() !== '' && files.value.length > 0
})

// 触发文件选择
const triggerFileInput = () => {
  if (!loading.value) {
    fileInput.value?.click()
  }
}

// 处理文件选择
const handleFileSelect = (event) => {
  const selectedFiles = Array.from(event.target.files)
  addFiles(selectedFiles)
}

// 处理拖拽相关
const handleDragOver = (e) => {
  if (!loading.value) {
    isDragOver.value = true
  }
}

const handleDragLeave = (e) => {
  isDragOver.value = false
}

const handleDrop = (e) => {
  isDragOver.value = false
  if (loading.value) return
  
  const droppedFiles = Array.from(e.dataTransfer.files)
  addFiles(droppedFiles)
}

// 添加文件
const addFiles = (newFiles) => {
  const validFiles = newFiles.filter(file => {
    const ext = file.name.split('.').pop().toLowerCase()
    return ['pdf', 'md', 'txt'].includes(ext)
  })
  files.value.push(...validFiles)
}

// 移除文件
const removeFile = (index) => {
  files.value.splice(index, 1)
}

// 滚动到底部
const scrollToBottom = () => {
  window.scrollTo({
    top: document.body.scrollHeight,
    behavior: 'smooth'
  })
}

// 开始模拟 - 立即跳转，API调用在Process页面进行
// ---- Chat "Afinar tu idea" (parche MX) ----
const CAMPOS_FICHA = [
  { k: 'oferta', label: 'Oferta' }, { k: 'precio', label: 'Precio' }, { k: 'publico', label: 'Público' },
  { k: 'canal', label: 'Canal' }, { k: 'competencia', label: 'Competencia' }, { k: 'diferenciador', label: 'Por qué tú' },
]
const CAMPOS_GUION = [
  { k: 'producto', label: 'Producto' }, { k: 'plataforma', label: 'Plataforma' }, { k: 'publico', label: 'Público' },
  { k: 'objetivo', label: 'Objetivo' }, { k: 'duracion', label: 'Duración' },
]
const chat = ref([])
const modoGuion = ref(false)
const pensando = ref(false)
const ficha = ref(null)
const respuestas = ref({})
const chatEl = ref(null)
const ideaTexto = computed(() => chat.value.find((m) => m.rol === 'usuario')?.texto || formData.value.simulationRequirement.trim())
const bajarChat = () => nextTick(() => { if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight })
// Celular: el chat vive en una barra fija abajo que se abre como hoja; sigue al teclado con visualViewport (iOS)
const hojaAbierta = ref(false)
const mqMovil = window.matchMedia('(max-width: 768px)')
const esMovil = ref(mqMovil.matches)
const alCambiarAncho = (e) => { esMovil.value = e.matches }
const ajustarTeclado = () => {
  const vv = window.visualViewport
  if (!vv) return
  const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
  document.documentElement.style.setProperty('--mf-teclado', `${kb}px`)
}
onMounted(() => {
  mqMovil.addEventListener('change', alCambiarAncho)
  window.visualViewport?.addEventListener('resize', ajustarTeclado)
  window.visualViewport?.addEventListener('scroll', ajustarTeclado)
})
onUnmounted(() => {
  mqMovil.removeEventListener('change', alCambiarAncho)
  window.visualViewport?.removeEventListener('resize', ajustarTeclado)
  window.visualViewport?.removeEventListener('scroll', ajustarTeclado)
  document.documentElement.style.removeProperty('--mf-teclado')
})

const pedirTurno = async (forzar) => {
  pensando.value = true
  hojaAbierta.value = true
  ficha.value = null
  bajarChat()
  const fd = new FormData()
  fd.append('historial', JSON.stringify(chat.value.map((m) => ({ rol: m.rol, texto: m.texto }))))
  if (forzar) fd.append('forzar', '1')
  if (modoGuion.value) fd.append('modo', 'guion')
  files.value.forEach((f) => fd.append('files', f))
  try {
    const { afinarIdea } = await import('../api/simulation')
    const d = (await afinarIdea(fd)).data
    chat.value.push({ rol: 'asistente', texto: d.respuesta || 'Listo, ya tengo lo necesario.', preguntas: d.preguntas })
    if (d.modo === 'guion') modoGuion.value = true
    if (d.lista) ficha.value = d
  } catch (e) {
    chat.value.push({ rol: 'asistente', texto: e.message || 'No pude revisar tu idea, intenta de nuevo.' })
  } finally {
    pensando.value = false
    bajarChat()
  }
}

// Respuestas marcadas en las opciones (también cuenta si después se toca "Lanzar así" o se escribe)
const textoChips = () => {
  const ult = chat.value[chat.value.length - 1]
  return (ult?.preguntas || []).filter((q) => respuestas.value[q.id]).map((q) => `${q.pregunta} ${respuestas.value[q.id]}`).join(' · ')
}
const enviar = (forzar = false) => {
  const texto = [textoChips(), formData.value.simulationRequirement.trim()].filter(Boolean).join(' · ')
  if (!forzar && !texto) return
  if (texto) chat.value.push({ rol: 'usuario', texto })
  formData.value.simulationRequirement = ''
  respuestas.value = {}
  pedirTurno(forzar === true)
}
const responderChip = (q, o) => { respuestas.value = { ...respuestas.value, [q.id]: o } }
const enviarChips = () => { if (textoChips()) enviar(false) }

// Al aprobar: sin documento, la semilla de la IA ES el documento; con documento, se agrega la ficha como contexto
const aprobarYLanzar = () => {
  const md = ficha.value?.semilla_md || ''
  const lista = files.value.length
    ? [...files.value, new File([md], 'ficha-de-la-idea.md', { type: 'text/markdown' })]
    : [new File([md], ficha.value?.modo === 'guion' ? 'guion-del-anuncio.md' : 'idea.md', { type: 'text/markdown' })]
  lanzarCon(lista, ficha.value?.instruccion || ideaTexto.value)
}

// Lanzar (parche MX): una sola confirmación -> pantalla en vivo -> informe. La mesa de trabajo de 5 pasos
// queda como "modo avanzado".
const lanzarCon = async (lista, requisito) => {
  if (loading.value) return
  const { getTarifas } = await import('../api/simulation')
  const { confirmarValidacion } = await import('../api/confirmar')
  let info = {}
  try { info = (await getTarifas()).data } catch (e) { return }  // sin sesión o sin saldo: el aviso ya se mostró
  const bytes = lista.reduce((s, f) => s + (f.size || 0), 0)
  const r = await confirmarValidacion({ ...info, fragmentos: Math.ceil(bytes / 600) })
  if (!r.ok) return
  try { sessionStorage.setItem('mf:validar', JSON.stringify({ n_ciudadanos: r.n_ciudadanos, max_agentes: r.max_agentes, rondas: r.rondas })) } catch (e) { /* sin almacenamiento */ }
  const { setPendingUpload } = await import('../store/pendingUpload.js')
  setPendingUpload(lista, requisito)
  router.push({ name: 'Live', params: { taskId: 'new' } })
}

const startAvanzado = () => {
  if (!files.value.length || !ideaTexto.value || loading.value) return
  formData.value.simulationRequirement = ficha.value?.instruccion || ideaTexto.value
  
  // 存储待上传的数据
  import('../store/pendingUpload.js').then(({ setPendingUpload }) => {
    setPendingUpload(files.value, formData.value.simulationRequirement)
    
    // 立即跳转到Process页面（使用特殊标识表示新建项目）
    router.push({
      name: 'Process',
      params: { projectId: 'new' }
    })
  })
}
</script>

<style scoped>
/* 全局变量与重置 */
:root {
  --black: #000000;
  --white: #FFFFFF;
  --orange: #FF4500;
  --gray-light: #F5F5F5;
  --gray-text: #666666;
  --border: #E5E5E5;
  /* 
    使用 Space Grotesk 作为主要标题字体，JetBrains Mono 作为代码/标签字体
    确保已在 index.html 引入这些 Google Fonts 
  */
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  --font-cn: 'Noto Sans SC', system-ui, sans-serif;
}

.home-container {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--black);
}

/* 顶部导航 */
.navbar {
  height: 60px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
}

.nav-brand {
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 1.2rem;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 16px;
}

.github-link {
  color: var(--white);
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: opacity 0.2s;
}

.github-link:hover {
  opacity: 0.8;
}

.arrow {
  font-family: sans-serif;
}

/* 主要内容区 */
.main-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 60px 40px;
}

/* Hero 区域 */
.hero-section {
  display: flex;
  justify-content: space-between;
  margin-bottom: 80px;
  position: relative;
}

.hero-left {
  flex: 1;
  padding-right: 60px;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 25px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
}

.orange-tag {
  background: var(--orange);
  color: var(--white);
  padding: 4px 10px;
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 0.75rem;
}

.version-text {
  color: #999;
  font-weight: 500;
  letter-spacing: 0.5px;
}

.main-title {
  font-size: 4.5rem;
  line-height: 1.2;
  font-weight: 500;
  margin: 0 0 40px 0;
  letter-spacing: -2px;
  color: var(--black);
}

.gradient-text {
  background: linear-gradient(90deg, #000000 0%, #444444 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  display: inline-block;
}

.hero-desc {
  font-size: 1.05rem;
  line-height: 1.8;
  color: var(--gray-text);
  max-width: 640px;
  margin-bottom: 50px;
  font-weight: 400;
  text-align: justify;
}

.hero-desc p {
  margin-bottom: 1.5rem;
}

.highlight-bold {
  color: var(--black);
  font-weight: 700;
}

.highlight-orange {
  color: var(--orange);
  font-weight: 700;
  font-family: var(--font-mono);
}

.highlight-code {
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 2px;
  font-family: var(--font-mono);
  font-size: 0.9em;
  color: var(--black);
  font-weight: 600;
}

.slogan-text {
  font-size: 1.2rem;
  font-weight: 520;
  color: var(--black);
  letter-spacing: 1px;
  border-left: 3px solid var(--orange);
  padding-left: 15px;
  margin-top: 20px;
}

.blinking-cursor {
  color: var(--orange);
  animation: blink 1s step-end infinite;
  font-weight: 700;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.decoration-square {
  width: 16px;
  height: 16px;
  background: var(--orange);
}

.hero-right {
  flex: 0.8;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: flex-end;
}

.logo-container {
  width: 100%;
  display: flex;
  justify-content: flex-end;
  padding-right: 40px;
}

.hero-logo {
  max-width: 500px; /* 调整logo大小 */
  width: 100%;
}

.scroll-down-btn {
  width: 40px;
  height: 40px;
  border: 1px solid var(--border);
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--orange);
  font-size: 1.2rem;
  transition: all 0.2s;
}

.scroll-down-btn:hover {
  border-color: var(--orange);
}

/* Dashboard 双栏布局 */
.dashboard-section {
  display: flex;
  gap: 60px;
  border-top: 1px solid var(--border);
  padding-top: 60px;
  align-items: flex-start;
}

.dashboard-section .left-panel,
.dashboard-section .right-panel {
  display: flex;
  flex-direction: column;
}

/* 左侧面板 */
.left-panel {
  flex: 0.8;
}

.panel-header {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
}

.status-dot {
  color: var(--orange);
  font-size: 0.8rem;
}

.section-title {
  font-size: 2rem;
  font-weight: 520;
  margin: 0 0 15px 0;
}

.section-desc {
  color: var(--gray-text);
  margin-bottom: 25px;
  line-height: 1.6;
}

.metrics-row {
  display: flex;
  gap: 20px;
  margin-bottom: 15px;
}

.metric-card {
  border: 1px solid var(--border);
  padding: 20px 30px;
  min-width: 150px;
}

.metric-value {
  font-family: var(--font-mono);
  font-size: 1.8rem;
  font-weight: 520;
  margin-bottom: 5px;
}

.metric-label {
  font-size: 0.85rem;
  color: #999;
}

/* Qué mejoramos (parche MX) */
.mejoras {
  border: 1px solid var(--border);
  padding: 30px;
  margin-bottom: 20px;
}

.mejoras-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 22px 28px;
}

.mejora-item {
  border-left: 2px solid var(--black);
  padding-left: 14px;
}

.mejora-tag {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 7px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  font-weight: 500;
  border: 1px solid currentColor;
  opacity: 0.6;
  vertical-align: 2px;
}

@media (max-width: 768px) {
  .mejoras { padding: 20px; }
  .mejoras-list { grid-template-columns: 1fr; gap: 16px; }
}

/* 项目模拟步骤介绍 */
.steps-container {
  border: 1px solid var(--border);
  padding: 30px;
  position: relative;
}

.steps-header {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
  margin-bottom: 25px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.diamond-icon {
  font-size: 1.2rem;
  line-height: 1;
}

.workflow-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.workflow-item {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.step-num {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--black);
  opacity: 0.3;
}

.step-info {
  flex: 1;
}

.step-title {
  font-weight: 520;
  font-size: 1rem;
  margin-bottom: 4px;
}

.step-desc {
  font-size: 0.85rem;
  color: var(--gray-text);
}

/* 右侧交互控制台 */
.right-panel {
  flex: 1.2;
}

.console-box {
  border: 1px solid #CCC; /* 外部实线 */
  padding: 8px; /* 内边距形成双重边框感 */
}

.console-section {
  padding: 20px;
}

.console-section.btn-section {
  padding-top: 0;
}

.console-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 15px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #666;
}

.upload-zone {
  border: 1px dashed #CCC;
  height: 200px;
  overflow-y: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.3s;
  background: #FAFAFA;
}

.upload-zone.has-files {
  align-items: flex-start;
}

.upload-zone:hover {
  background: #F0F0F0;
  border-color: #999;
}

.upload-placeholder {
  text-align: center;
}

.upload-icon {
  width: 40px;
  height: 40px;
  border: 1px solid #DDD;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 15px;
  color: #999;
}

.upload-title {
  font-weight: 500;
  font-size: 0.9rem;
  margin-bottom: 5px;
}

.upload-hint {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #999;
}

.file-list {
  width: 100%;
  padding: 15px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.file-item {
  display: flex;
  align-items: center;
  background: var(--white);
  padding: 8px 12px;
  border: 1px solid #EEE;
  font-family: var(--font-mono);
  font-size: 0.85rem;
}

.file-name {
  flex: 1;
  margin: 0 10px;
}

.remove-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.2rem;
  color: #999;
}

.console-divider {
  display: flex;
  align-items: center;
  margin: 10px 0;
}

.console-divider::before,
.console-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: #EEE;
}

.console-divider span {
  padding: 0 15px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: #BBB;
  letter-spacing: 1px;
}

.input-wrapper {
  position: relative;
  border: 1px solid #DDD;
  background: #FAFAFA;
}

.code-input {
  width: 100%;
  border: none;
  background: transparent;
  padding: 20px;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  min-height: 150px;
}

.model-badge {
  position: absolute;
  bottom: 10px;
  right: 15px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: #AAA;
}

/* ---- Chat "Afinar tu idea" ---- */
.chat-section { display: flex; flex-direction: column; gap: 10px;
  /* Las variables del :root de este archivo no aplican (style scoped); se fijan aquí para el chat */
  --black: #000000; --white: #FFFFFF; --orange: #FF4500; --gray-light: #F5F5F5; --gray-text: #666666; --border: #E5E5E5;
  /* En celular vive en <body> (Teleport), fuera de #app: se fija la misma fuente de la app */
  font-family: 'JetBrains Mono', 'Space Grotesk', 'Noto Sans SC', monospace; color: #000; }
.chat { max-height: 380px; min-height: 120px; overflow-y: auto; overflow-x: hidden; display: flex; flex-direction: column;
        gap: 10px; padding: 4px 2px; scroll-behavior: smooth; }
.chat-vacio { color: var(--gray-text); font-size: 0.9rem; line-height: 1.6; margin: 0; }
.msg { max-width: 88%; font-size: 0.9rem; line-height: 1.5; }
.msg p { margin: 0; padding: 10px 12px; overflow-wrap: anywhere; }
.msg.usuario { align-self: flex-end; }
.msg.usuario p { background: var(--black); color: var(--white); }
.msg.asistente { align-self: flex-start; }
.msg.asistente p { background: var(--gray-light); border-left: 2px solid var(--orange); }
.msg.pensando { display: flex; gap: 5px; padding: 14px 12px; background: var(--gray-light); }
.msg.pensando i { width: 6px; height: 6px; border-radius: 50%; background: #999; animation: punto 1.2s infinite ease-in-out; }
.msg.pensando i:nth-child(2) { animation-delay: 0.15s; }
.msg.pensando i:nth-child(3) { animation-delay: 0.3s; }
@keyframes punto { 0%, 80%, 100% { opacity: 0.25; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
.preguntas { margin-top: 8px; display: flex; flex-direction: column; gap: 10px; }
.pregunta > span { display: block; font-size: 0.82rem; color: #333; margin-bottom: 6px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button { padding: 6px 12px; border: 1px solid var(--border); background: var(--white); font: inherit; font-size: 0.82rem; cursor: pointer; }
.chips button:hover { border-color: #999; }
.chips button.sel { background: var(--black); color: var(--white); border-color: var(--black); }
.enviar-chips { align-self: flex-start; padding: 7px 14px; border: 0; background: var(--orange); color: var(--white); font: inherit; font-size: 0.82rem; cursor: pointer; }
.ficha { border: 1px solid var(--black); padding: 14px 16px; margin-top: 4px; }
.ficha-titulo { font-weight: 600; margin-bottom: 8px; }
.ficha dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0 0 8px; font-size: 0.85rem; }
.ficha dt { color: var(--gray-text); }
.ficha dd { margin: 0; overflow-wrap: anywhere; }
.supuestos { font-size: 0.8rem; color: #8A5A00; background: #FFF7E6; padding: 6px 8px; margin: 0 0 8px; }
.escenas { margin: 0 0 10px; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; font-size: 0.83rem; line-height: 1.45; }
.escenas li { padding: 6px 8px; background: var(--gray-light); overflow-wrap: anywhere; }
.escenas b { display: block; font-size: 0.75rem; color: var(--gray-text); font-weight: 600; }
.ficha-instr { font-size: 0.85rem; margin: 0 0 12px; line-height: 1.5; }
.aprobar { width: 100%; padding: 14px; border: 0; background: var(--black); color: var(--white); font: inherit; font-weight: 600; cursor: pointer; }
.aprobar:disabled { opacity: 0.6; }
.ajustar { width: 100%; margin-top: 6px; padding: 9px; border: 1px solid var(--border); background: transparent; font: inherit; font-size: 0.85rem; cursor: pointer; }
.chat-input { display: flex; gap: 8px; align-items: stretch; }
.chat-input textarea { flex: 1; resize: none; padding: 10px 12px; border: 1px solid var(--border); font: inherit; font-size: 0.9rem; line-height: 1.4; min-width: 0; }
.chat-input textarea:focus { outline: 2px solid var(--black); outline-offset: -1px; }
.enviar { width: 48px; border: 0; background: var(--black); color: var(--white); font-size: 1.2rem; cursor: pointer; }
.enviar:disabled { opacity: 0.35; cursor: not-allowed; }
.lanzar-asi { align-self: flex-start; background: none; border: 0; padding: 0; color: var(--gray-text); font: inherit; font-size: 0.82rem; text-decoration: underline; cursor: pointer; }
@media (prefers-reduced-motion: reduce) { .msg.pensando i { animation: none; } .chat { scroll-behavior: auto; } }
.adjuntar { position: relative; width: 44px; flex: none; border: 1px solid var(--border); background: var(--white); font-size: 1.3rem; cursor: pointer; }
.adjuntar b { position: absolute; top: -6px; right: -6px; min-width: 18px; height: 18px; border-radius: 9px; background: var(--orange);
  color: var(--white); font-size: 0.7rem; line-height: 18px; text-align: center; }
.hoja-asa { display: none; }
.modos { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.modos button { padding: 7px 12px; border: 1px solid var(--border); background: var(--white); font: inherit; font-size: 0.82rem; cursor: pointer; }
.modos button.sel { background: var(--black); color: var(--white); border-color: var(--black); }
.chat-section.drag-over { outline: 2px dashed var(--orange); outline-offset: 4px; }
.adjuntos { display: flex; flex-wrap: wrap; gap: 6px; }
.adjunto { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 4px 4px 4px 10px; background: var(--gray-light);
  font-size: 0.8rem; overflow-wrap: anywhere; }
.adjunto button { border: 0; background: none; font-size: 1rem; line-height: 1; padding: 2px 6px; cursor: pointer; color: var(--gray-text); }
/* Celular: barra fija abajo; al conversar se abre como hoja (hasta 62 % de la pantalla) */
@media (max-width: 768px) {
  .home-container { padding-bottom: calc(96px + env(safe-area-inset-bottom)); }
  .chat-section { position: fixed; left: 0; right: 0; bottom: var(--mf-teclado, 0px); z-index: 1500; margin: 0;
    background: var(--white); border-top: 1px solid var(--black); box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.08);
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom)); gap: 8px; transition: bottom 0.15s ease-out; }
  .chat-section:not(.abierta) .console-header,
  .chat-section:not(.abierta) .chat,
  .chat-section:not(.abierta) .lanzar-asi { display: none; }
  .chat-section.abierta .chat { max-height: calc(62dvh - var(--mf-teclado, 0px)); min-height: 80px; }
  .chat-section.abierta .hoja-asa { display: flex; justify-content: center; width: 100%; padding: 2px 0 6px; border: 0; background: none; cursor: pointer; }
  .hoja-asa span { width: 40px; height: 4px; border-radius: 2px; background: #C8C8C8; }
  .chat-input textarea { font-size: 16px; height: 52px; }  /* 16px: iOS no hace zoom al enfocar */
}

.modo-avanzado {
  width: 100%;
  margin-top: 10px;
  padding: 10px;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--gray-text);
  font-family: inherit;
  font-size: 0.85rem;
  cursor: pointer;
}
.modo-avanzado:hover:not(:disabled) { border-color: #999; color: #333; }
.modo-avanzado:disabled { opacity: 0.5; cursor: not-allowed; }

.start-engine-btn {
  width: 100%;
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 20px;
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 1.1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  transition: all 0.3s ease;
  letter-spacing: 1px;
  position: relative;
  overflow: hidden;
}

/* 可点击状态（非禁用） */
.start-engine-btn:not(:disabled) {
  background: var(--black);
  border: 1px solid var(--black);
  animation: pulse-border 2s infinite;
}

.start-engine-btn:hover:not(:disabled) {
  background: var(--orange);
  border-color: var(--orange);
  transform: translateY(-2px);
}

.start-engine-btn:active:not(:disabled) {
  transform: translateY(0);
}

.start-engine-btn:disabled {
  background: #E5E5E5;
  color: #999;
  cursor: not-allowed;
  transform: none;
  border: 1px solid #E5E5E5;
}

/* 引导动画：微妙的边框脉冲 */
@keyframes pulse-border {
  0% { box-shadow: 0 0 0 0 rgba(0, 0, 0, 0.2); }
  70% { box-shadow: 0 0 0 6px rgba(0, 0, 0, 0); }
  100% { box-shadow: 0 0 0 0 rgba(0, 0, 0, 0); }
}

/* 响应式适配 */
@media (max-width: 1024px) {
  .dashboard-section {
    flex-direction: column;
  }
  
  .hero-section {
    flex-direction: column;
  }
  
  .hero-left {
    padding-right: 0;
    margin-bottom: 40px;
  }
  
  .hero-logo {
    max-width: 200px;
    margin-bottom: 20px;
  }
}
</style>

<style>
/* English locale adjustments (unscoped to target html[lang]) */
html[lang="en"] .main-title {
  font-size: 3.5rem;
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  letter-spacing: -1px;
}

html[lang="en"] .hero-desc {
  text-align: left;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  letter-spacing: 0;
}

html[lang="en"] .slogan-text {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  letter-spacing: 0;
}

html[lang="en"] .tag-row {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

html[lang="en"] .navbar .nav-links {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Left pane: system status + workflow */
html[lang="en"] .status-section {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

html[lang="en"] .status-section .status-ready {
  font-size: 1.6rem;
}

html[lang="en"] .status-section .metric-value {
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 1.4rem;
}

html[lang="en"] .workflow-list .step-title {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

html[lang="en"] .workflow-list .step-desc {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
  font-size: 0.72rem !important;
  line-height: 1.4 !important;
}

html[lang="en"] .workflow-list {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
</style>
