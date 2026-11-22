<template>
  <!-- Pantalla "En vivo" (parche MX): un solo lugar para ver la ciudad conversar sobre la idea.
       El grafo 3D ocupa toda la pantalla y crece en vivo; encima, la fase, el avance y lo que dice la ciudad. -->
  <div class="live" ref="rootEl">
    <div ref="graphEl" class="live-graph" aria-label="Grafo de la ciudad en vivo"></div>

    <!-- Burbujas: lo que dice cada agente aparece junto a su nodo y se funde en él -->
    <div class="burbujas" aria-live="polite">
      <div v-for="b in burbujas" :key="b.id" class="burbuja" :class="[b.estado, { flotante: !b.nodeId }]"
           :style="{ transform: `translate(${b.x}px, ${b.y}px)` }">
        <div class="burbuja-caja">
          <b>{{ b.quien }}</b>
          <span>{{ b.texto }}</span>
        </div>
      </div>
    </div>

    <header class="live-top">
      <button type="button" class="brand" @click="router.push('/')">MIROFISH</button>
      <div class="top-right">
        <SaldoBadge class="dark" />
        <button v-if="puedeFullscreen" type="button" class="icon-btn" :aria-label="enFullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'" @click="toggleFullscreen">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path v-if="!enFullscreen" d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/><path v-else d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>
        </button>
      </div>
    </header>

    <section class="live-panel" :class="{ abierto: panelAbierto }">
      <button type="button" class="panel-handle" @click="panelAbierto = !panelAbierto" :aria-expanded="panelAbierto">
        <span class="fase-dot" :class="{ ok: fase === 'listo', err: estado === 'failed' }"></span>
        <span class="fase-txt">{{ textoFase }}</span>
        <span class="fase-num">{{ porcentaje }}%</span>
      </button>

      <p v-if="detalle && fase !== 'listo' && fase !== 'simulacion'" class="fase-detalle" aria-live="polite">{{ detalle }}</p>

      <div class="progreso" role="progressbar" :aria-valuenow="porcentaje" aria-valuemin="0" aria-valuemax="100">
        <div class="progreso-barra" :style="{ width: porcentaje + '%' }"></div>
      </div>

      <ol class="pasos">
        <li v-for="p in PASOS" :key="p.id" :class="{ hecho: indiceFase > p.i, actual: indiceFase === p.i }">{{ p.label }}</li>
      </ol>

      <div v-if="sim" class="sim-datos">
        <span>Ronda <b>{{ sim.current_round || 0 }}</b>/{{ sim.total_rounds || '–' }}</span>
        <span>{{ horaSimulada }}</span>
        <span><b>{{ sim.total_actions_count || 0 }}</b> acciones</span>
        <span>{{ nodos.length }} nodos</span>
      </div>

      <div v-if="ultimaVoz" class="ultima" aria-live="off">
        <span class="feed-titulo">Última voz</span>
        <p><b>{{ ultimaVoz.quien }}</b> {{ ultimaVoz.texto }}</p>
        <span class="pista">Toca cualquier punto para leer lo que dijo.</span>
      </div>
      <p v-else-if="fase === 'grafo'" class="nota">Leyendo tu documento: cada nodo que aparece es una persona, organización o idea que encontró.</p>
      <p v-else-if="fase === 'preparacion'" class="nota">Eligiendo ciudadanos de tu ciudad y dándoles su postura ante la idea.</p>

      <button v-if="soloVer && reportId" type="button" class="ver-informe" @click="irAlInforme">Ver el informe</button>

      <div v-if="estado === 'failed'" class="error">
        <p>{{ error || 'La validación no pudo terminar.' }}</p>
        <button type="button" @click="router.push('/')">Volver al inicio</button>
      </div>
    </section>

    <Transition name="listo">
      <div v-if="fase === 'listo' && reportId && !soloVer" class="listo" role="status">
        <img v-if="ilustracion" :src="ilustracion" alt="Infografía del resultado" />
        <h2>Tu informe está listo</h2>
        <p>Te llevamos en {{ cuentaAtras }} s…</p>
        <button type="button" @click="irAlInforme">Ver informe ahora</button>
      </div>
    </Transition>

    <Transition name="tarjeta">
      <aside v-if="nodoSel" class="nodo-sel" role="dialog" :aria-label="nodoSel.name">
        <button type="button" class="cerrar" aria-label="Cerrar" @click="nodoSel = null">×</button>
        <b>{{ nodoSel.name }}</b><span>{{ nodoSel.type }}</span>
        <div v-if="nodoSel.dijo.length" class="dijo">
          <div class="dijo-titulo">Lo que dijo</div>
          <p v-for="(m, i) in nodoSel.dijo" :key="i"><em>{{ m.tipo }}</em> {{ m.texto }}</p>
        </div>
        <p v-else-if="nodoSel.summary" class="resumen">{{ nodoSel.summary }}</p>
      </aside>
    </Transition>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SaldoBadge from '../components/SaldoBadge.vue'
import { getGraphData, generateOntology } from '../api/graph'
import { getRunStatusDetail, getValidacion, lanzarValidacion } from '../api/simulation'
import { illustrationImageUrl } from '../api/report'
import { tiempoSimulado } from '../api/confirmar'

const route = useRoute()
const router = useRouter()

const PASOS = [
  { id: 'documento', i: 0, label: 'Documento' }, { id: 'grafo', i: 1, label: 'Grafo' },
  { id: 'preparacion', i: 2, label: 'Ciudadanos' }, { id: 'simulacion', i: 3, label: 'Conversación' },
  { id: 'informe', i: 4, label: 'Informe' },
]
const ORDEN = { documento: 0, grafo: 1, preparacion: 2, simulacion: 3, informe: 4, ilustracion: 4, listo: 5 }
const TEXTO = { documento: 'Leyendo tu documento', grafo: 'Armando el grafo de tu documento',
  preparacion: 'Preparando a los ciudadanos', simulacion: 'La ciudad está conversando',
  informe: 'Redactando el informe', ilustracion: 'Dibujando la infografía', listo: 'Listo' }

const rootEl = ref(null)
const graphEl = ref(null)
const taskId = ref(route.params.taskId)
const soloVer = route.query.ver === '1'  // ver la ciudad de una validación terminada (sin llevar al informe)
const fase = ref('documento')
const estado = ref('processing')
const progreso = ref(0)
const detalle = ref('')  // avance del paso en curso (p. ej. la sección del informe que se está redactando)
const error = ref('')
const graphId = ref(null)
const simulationId = ref(null)
const reportId = ref(null)
const sim = ref(null)
const feed = ref([])
const nodos = ref([])
const panelAbierto = ref(true)
const nodoSel = ref(null)
const burbujas = ref([])
const ultimaVoz = ref(null)
const ilustracion = ref(null)
const cuentaAtras = ref(5)
const enFullscreen = ref(false)
const puedeFullscreen = typeof document !== 'undefined' && !!document.documentElement.requestFullscreen

const indiceFase = computed(() => ORDEN[fase.value] ?? 0)
const textoFase = computed(() => estado.value === 'failed' ? 'No se pudo terminar' : (TEXTO[fase.value] || 'Trabajando'))
const porcentaje = computed(() => {
  if (fase.value === 'listo') return 100
  if (fase.value === 'simulacion' && sim.value?.total_rounds) {
    return Math.round(50 + 30 * Math.min(1, (sim.value.current_round || 0) / sim.value.total_rounds))
  }
  return Math.max(2, Math.min(99, progreso.value || 0))
})
const horaSimulada = computed(() => {
  const h = sim.value?.current_round || 0
  const dia = Math.floor(h / 24) + 1
  return `Día ${dia}, ${String(h % 24).padStart(2, '0')}:00 · ${tiempoSimulado(sim.value?.total_rounds || 0)}`
})

// ---------------- grafo 3D incremental ----------------
let graph3d = null
let THREE = null
const links = []
const nodeIndex = new Map()
const linkIndex = new Set()
const recientes = new Map()  // id -> instante en que apareció (resalte cempasúchil unos segundos)
const hablando = new Map()   // id -> instante en que su burbuja se fundió (destello breve)
const PALETA = ['#7DD3FC', '#C4B5FD', '#86EFAC', '#FDA4AF', '#FCD34D', '#A5B4FC', '#5EEAD4', '#F9A8D4']
const colorTipo = {}
const colorDe = (tipo) => (colorTipo[tipo] ||= PALETA[Object.keys(colorTipo).length % PALETA.length])
const CEMPASUCHIL = '#F59E0B'
const esMovil = typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches

function etiquetaSprite(texto, nuevo) {
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  const fs = 44
  ctx.font = `600 ${fs}px "Space Grotesk", system-ui, sans-serif`
  const w = Math.ceil(ctx.measureText(texto).width) + 36
  canvas.width = w
  canvas.height = fs + 26
  ctx.font = `600 ${fs}px "Space Grotesk", system-ui, sans-serif`
  ctx.fillStyle = nuevo ? 'rgba(245,158,11,0.92)' : 'rgba(11,16,38,0.78)'
  const r = 16
  ctx.beginPath()
  ctx.moveTo(r, 0); ctx.arcTo(w, 0, w, canvas.height, r); ctx.arcTo(w, canvas.height, 0, canvas.height, r)
  ctx.arcTo(0, canvas.height, 0, 0, r); ctx.arcTo(0, 0, w, 0, r); ctx.closePath(); ctx.fill()
  ctx.fillStyle = nuevo ? '#1F1300' : '#F8FAFC'
  ctx.textBaseline = 'middle'
  ctx.fillText(texto, 18, canvas.height / 2 + 2)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthWrite: false, transparent: true }))
  const escala = esMovil ? 0.16 : 0.12
  sprite.scale.set(canvas.width * escala, canvas.height * escala, 1)
  return sprite
}

function conEtiqueta() {
  // Etiquetas solo para los nodos más conectados y los recién llegados: legible sin saturar
  const top = [...nodeIndex.values()].sort((a, b) => (b.deg + 3 * (b.voces || 0)) - (a.deg + 3 * (a.voces || 0)))
    .slice(0, esMovil ? 8 : 14).map((n) => n.id)
  return new Set([...top, ...recientes.keys()])
}

function objetoNodo(n) {
  if (!etiquetadas.has(n.id)) return null
  const base = n.name.length > 26 ? n.name.slice(0, 25) + '…' : n.name
  const s = etiquetaSprite(n.voces ? `${base} · ${n.voces}` : base, recientes.has(n.id))
  s.position.y = 6 + Math.sqrt(n.deg + 1) * 2
  return s
}
let etiquetadas = new Set()

async function montarGrafo() {
  const [{ default: ForceGraph3D }, three] = await Promise.all([import('3d-force-graph'), import('three')])
  THREE = three
  const el = graphEl.value
  graph3d = ForceGraph3D({ controlType: 'orbit' })(el)
    .width(el.clientWidth).height(el.clientHeight)
    .backgroundColor('rgba(0,0,0,0)')
    .showNavInfo(false)
    .graphData({ nodes: nodos.value, links })
    .nodeId('id')
    .nodeVal((n) => 1 + Math.sqrt(n.deg) + Math.min(6, (n.voces || 0) * 0.8))
    .nodeRelSize(esMovil ? 3.2 : 4)
    .nodeResolution(esMovil ? 8 : 12)
    .nodeOpacity(0.95)
    .nodeColor((n) => (hablando.has(n.id) ? '#FFFFFF' : (recientes.has(n.id) ? CEMPASUCHIL : n.color)))
    .nodeLabel(() => '')
    .nodeThreeObject(objetoNodo)
    .nodeThreeObjectExtend(true)
    .linkColor(() => 'rgba(203,213,225,0.22)')
    .linkWidth(0)
    .linkDirectionalParticles((l) => (esReciente(l) ? 3 : 1))
    .linkDirectionalParticleSpeed((l) => (esReciente(l) ? 0.012 : 0.004))
    .linkDirectionalParticleWidth((l) => (esReciente(l) ? 1.8 : 0.9))
    .linkDirectionalParticleColor((l) => (esReciente(l) ? CEMPASUCHIL : 'rgba(186,230,253,0.55)'))
    .cooldownTicks(esMovil ? 90 : 160)
    .onNodeClick((n) => {
      nodoSel.value = { name: n.name, type: n.type, summary: n.raw?.summary || '',
        dijo: [...(dichos.get(n.id) || [])].reverse().slice(0, 6) }
    })
    .onBackgroundClick(() => { nodoSel.value = null })
  try { graph3d.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)) } catch (e) { /* sin WebGL avanzado */ }
  const ctl = graph3d.controls()
  ctl.autoRotate = true
  ctl.autoRotateSpeed = esMovil ? 0.6 : 0.45
  ctl.enableDamping = true
  // Si el usuario toca/arrastra/hace zoom: se pausan el giro y el encuadre automático unos segundos
  ctl.addEventListener('start', () => { ultimaInteraccion = Date.now(); ctl.autoRotate = false })
  ctl.addEventListener('end', () => {
    ultimaInteraccion = Date.now()
    setTimeout(() => { if (Date.now() - ultimaInteraccion >= 7000) ctl.autoRotate = true }, 7200)
  })
  graph3d.cameraPosition({ z: esMovil ? 220 : 200 })
  // Repulsión con alcance limitado: los nodos sueltos (sin aristas) ya no se alejan sin fin
  try { graph3d.d3Force('charge').distanceMax(140) } catch (e) { /* versión sin d3Force */ }
  graph3d.onEngineStop(() => encuadrar(900))
  if (route.query.debug === '1') window.__mfEnVivo = { graph3d, encuadrar, nodos }
  window.addEventListener('resize', alRedimensionar)
  raf = requestAnimationFrame(seguirNodos)
}

const esReciente = (l) => recientes.has(l.source?.id ?? l.source) || recientes.has(l.target?.id ?? l.target)
let ultimaInteraccion = 0
let ultimoEncuadre = 0

// Encuadra el grafo en la zona VISIBLE (arriba del panel), sin pelear con el usuario si está explorando
function encuadrar(ms = 1200) {
  if (!graph3d || !nodos.value.length || Date.now() - ultimaInteraccion < 10000) return
  ultimoEncuadre = Date.now()
  const panel = rootEl.value?.querySelector('.live-panel')
  const H = graphEl.value.clientHeight
  const tapado = esMovil && panel ? Math.min(panel.offsetHeight + 16, H * 0.5) : 0
  // Encuadre propio (no zoomToFit): centro y radio del 90 % de nodos más cercanos, para que un nodo rezagado
  // no mande la cámara al infinito. Distancia acotada entre 60 y 900.
  const pts = nodos.value.filter((n) => Number.isFinite(n.x) && Number.isFinite(n.y) && Number.isFinite(n.z))
  if (!pts.length) return
  const c = pts.reduce((a, n) => ({ x: a.x + n.x / pts.length, y: a.y + n.y / pts.length, z: a.z + n.z / pts.length }), { x: 0, y: 0, z: 0 })
  const d = pts.map((n) => Math.hypot(n.x - c.x, n.y - c.y, n.z - c.z)).sort((a, b) => a - b)
  const radio = Math.max(30, d[Math.min(d.length - 1, Math.floor(d.length * 0.9))])
  const cam = graph3d.camera()
  const ctl = graph3d.controls()
  const W = graphEl.value.clientWidth
  const fovV = (cam.fov * Math.PI) / 180
  const fovH = 2 * Math.atan(Math.tan(fovV / 2) * (W / Math.max(1, H - tapado)))
  const llenar = esMovil ? 1.08 : 1.0
  let dist = (radio * llenar) / Math.sin(Math.min(fovV, fovH) / 2)
  dist = Math.min(900, Math.max(60, dist))
  // Misma dirección de vista actual (el giro sigue), solo cambia la distancia y el centro
  const dir = cam.position.clone().sub(ctl.target)
  if (dir.length() < 1e-3) dir.set(0, 0, 1)
  dir.normalize()
  // Subir el centro la mitad de lo que tapa el panel, para que el grafo quede en la zona visible
  const mundoPorPx = (2 * dist * Math.tan(fovV / 2)) / H
  const dy = (tapado / 2) * mundoPorPx
  const mira = { x: c.x, y: c.y - dy, z: c.z }
  graph3d.cameraPosition({ x: mira.x + dir.x * dist, y: mira.y + dir.y * dist, z: mira.z + dir.z * dist }, mira, ms)
}

function alRedimensionar() {
  if (graph3d && graphEl.value) graph3d.width(graphEl.value.clientWidth).height(graphEl.value.clientHeight)
}

async function actualizarGrafo() {
  if (!graphId.value || !graph3d) return
  let data
  try { data = (await getGraphData(graphId.value, true)).data } catch (e) { return }
  const ahora = Date.now()
  let cambios = false
  // La primera carga (o al volver a la página a medias) no cuenta como "nuevo": solo lo que llega después se resalta
  const primeraCarga = nodeIndex.size === 0
  for (const n of data.nodes || []) {
    if (nodeIndex.has(n.uuid)) continue
    const tipo = n.labels?.find((l) => l !== 'Entity') || 'Entidad'
    const nodo = { id: n.uuid, name: n.name || 'Sin nombre', type: tipo, color: colorDe(tipo), deg: 0, raw: n }
    nodeIndex.set(n.uuid, nodo)
    nodos.value.push(nodo)
    if (!primeraCarga) recientes.set(n.uuid, ahora)
    cambios = true
  }
  for (const e of data.edges || []) {
    const a = e.source_node_uuid
    const b = e.target_node_uuid
    if (a === b || !nodeIndex.has(a) || !nodeIndex.has(b) || linkIndex.has(e.uuid)) continue
    linkIndex.add(e.uuid)
    links.push({ source: a, target: b, name: e.name })
    nodeIndex.get(a).deg++
    nodeIndex.get(b).deg++
    // Nace junto a su vecino ya ubicado: crece sin saltos
    const nuevo = recientes.has(b) ? nodeIndex.get(b) : (recientes.has(a) ? nodeIndex.get(a) : null)
    const ancla = nuevo === nodeIndex.get(b) ? nodeIndex.get(a) : nodeIndex.get(b)
    if (nuevo && nuevo.x === undefined && ancla?.x !== undefined) {
      nuevo.x = ancla.x + (Math.random() - 0.5) * 20
      nuevo.y = ancla.y + (Math.random() - 0.5) * 20
      nuevo.z = ancla.z + (Math.random() - 0.5) * 20
    }
    cambios = true
  }
  for (const [id, t0] of recientes) if (ahora - t0 > 12000) { recientes.delete(id); cambios = true }
  if (!cambios) return
  etiquetadas = conEtiqueta()
  graph3d.graphData({ nodes: nodos.value, links })
  graph3d.nodeThreeObject(objetoNodo).nodeColor(graph3d.nodeColor())
    .linkDirectionalParticles(graph3d.linkDirectionalParticles())
  if (Date.now() - ultimoEncuadre > 6000) setTimeout(() => encuadrar(1200), 1500)
}

// ---------------- conversación en vivo: burbujas que se funden en su nodo ----------------
const TIPOS = { CREATE_POST: 'publicó', QUOTE_POST: 'citó', CREATE_COMMENT: 'comentó', REPOST: 'compartió' }
const vistos = new Set()
const cola = []
const dichos = new Map()   // id de nodo -> [{tipo, texto}]
const MAX_BURBUJAS = esMovil ? 3 : 5
const VIDA_MS = esMovil ? 5200 : 6500
const ALTO_BURBUJA = esMovil ? 96 : 110
const ANCHO_BURBUJA = esMovil ? 190 : 220
let idBurbuja = 0
const norm = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim()

function nodoDelAgente(nombre) {
  const q = norm(nombre)
  if (!q) return null
  let mejor = null
  for (const n of nodeIndex.values()) {
    const m = norm(n.name)
    if (m === q) return n
    // "Rosa Elena Cruz" en el grafo vs "Rosa Elena Cruz Méndez" en la simulación (o al revés)
    if (!mejor && m.length > 4 && (q.startsWith(m) || m.startsWith(q))) mejor = n
  }
  return mejor
}

function anotarDicho(nodo, tipo, texto) {
  if (!dichos.has(nodo.id)) dichos.set(nodo.id, [])
  dichos.get(nodo.id).push({ tipo, texto })
  nodo.voces = (nodo.voces || 0) + 1
  hablando.set(nodo.id, Date.now())
  setTimeout(() => { hablando.delete(nodo.id); graph3d?.nodeColor(graph3d.nodeColor()) }, 2500)
  etiquetadas = conEtiqueta()
  graph3d?.nodeVal(graph3d.nodeVal()).nodeThreeObject(objetoNodo).nodeColor(graph3d.nodeColor())
}

function lanzarBurbujas() {
  while (cola.length && burbujas.value.filter((b) => b.estado !== 'saliendo').length < MAX_BURBUJAS) {
    const a = cola.shift()
    const nodo = nodoDelAgente(a.quien)
    burbujas.value.push({ id: ++idBurbuja, nodeId: nodo?.id || null, quien: a.quien, texto: a.corto, x: 0, y: 0,
                          estado: 'entrando', base: Math.random() })
    const b = burbujas.value[burbujas.value.length - 1]  // proxy reactivo: cambiar b.estado sí repinta
    ultimaVoz.value = { quien: a.quien, texto: a.corto }
    setTimeout(() => { b.estado = 'visible' }, 40)
    setTimeout(() => {
      b.estado = 'saliendo'
      if (nodo) anotarDicho(nodo, a.tipo, a.texto)
    }, VIDA_MS)
    setTimeout(() => {
      burbujas.value = burbujas.value.filter((x) => x.id !== b.id)
      lanzarBurbujas()
    }, VIDA_MS + 700)
  }
}

// Cada cuadro: las burbujas siguen a su nodo mientras el grafo gira
let raf = 0
function seguirNodos() {
  raf = requestAnimationFrame(seguirNodos)
  if (!graph3d || !burbujas.value.length || !graphEl.value) return
  const W = graphEl.value.clientWidth
  const H = graphEl.value.clientHeight
  for (const b of burbujas.value) {
    const n = b.nodeId ? nodeIndex.get(b.nodeId) : null
    if (n && n.x !== undefined) {
      const p = graph3d.graph2ScreenCoords(n.x, n.y, n.z)
      b.x = Math.max(8, Math.min(W - ANCHO_BURBUJA - 8, p.x - 24))
      b.y = Math.max(70, Math.min(H - 160, p.y - ALTO_BURBUJA))
    } else if (!b.x) {
      // Aún sin nodo: sube desde abajo como "voz de la ciudad"
      b.x = 16 + b.base * Math.max(1, W - ANCHO_BURBUJA - 32)
      b.y = H * (esMovil ? 0.38 : 0.55)
    }
  }
  // Sin encimarse: si dos burbujas se tocan, la de abajo baja lo necesario (o sube si no cabe)
  const orden = [...burbujas.value].filter((b) => b.estado !== 'saliendo').sort((a, b) => a.y - b.y)
  for (let i = 1; i < orden.length; i++) {
    const a = orden[i - 1]
    const b = orden[i]
    if (Math.abs(b.x - a.x) < ANCHO_BURBUJA && b.y - a.y < ALTO_BURBUJA + 6) {
      b.y = a.y + ALTO_BURBUJA + 6
      if (b.y > H - 160) b.y = Math.max(70, a.y - ALTO_BURBUJA - 6)
    }
  }
}

async function actualizarSimulacion() {
  if (!simulationId.value) return
  try {
    const d = (await getRunStatusDetail(simulationId.value)).data
    sim.value = d
    const acciones = (d.recent_actions || d.all_actions || [])
      .filter((a) => TIPOS[a.action_type] && (a.action_args?.content || a.action_args?.quote_content))
    for (const a of acciones) {
      const k = `${a.timestamp}-${a.agent_id}-${a.action_type}-${a.platform}`
      if (vistos.has(k)) continue
      vistos.add(k)
      const texto = String(a.action_args.content || a.action_args.quote_content)
      cola.push({ quien: a.agent_name || `Agente ${a.agent_id}`, tipo: TIPOS[a.action_type],
                  texto: texto.slice(0, 400), corto: texto.length > (esMovil ? 90 : 120) ? texto.slice(0, esMovil ? 87 : 117) + '…' : texto })
    }
    if (cola.length > 40) cola.splice(0, cola.length - 40)  // si llegan muchas de golpe, las más nuevas
    lanzarBurbujas()
  } catch (e) { /* reintenta en el siguiente ciclo */ }
}

// ---------------- estado del recorrido ----------------
async function actualizarRecorrido() {
  if (!taskId.value || taskId.value === 'new') return
  try {
    const d = (await getValidacion(taskId.value)).data
    estado.value = d.status
    progreso.value = d.progress
    detalle.value = d.message && d.message !== 'Tarea completa' ? d.message : ''
    error.value = d.error || ''
    if (d.fase) fase.value = d.fase
    if (d.status === 'completed') fase.value = 'listo'
    graphId.value = d.graph_id || graphId.value
    simulationId.value = d.simulation_id || simulationId.value
    reportId.value = d.report_id || reportId.value
    if (fase.value === 'listo' && !soloVer) terminar()
  } catch (e) { /* reintenta */ }
}

let terminado = false
function terminar() {
  if (terminado) return
  terminado = true
  detenerSondeos()
  ilustracion.value = illustrationImageUrl(reportId.value, Date.now())
  const t = setInterval(() => {
    cuentaAtras.value -= 1
    if (cuentaAtras.value <= 0) { clearInterval(t); irAlInforme() }
  }, 1000)
}
const irAlInforme = () => router.push({ name: 'Report', params: { reportId: reportId.value } })

// Inicio desde el formulario: lee el documento (ontología) y lanza el recorrido completo
async function iniciarDesdeInicio() {
  const { getPendingUpload, clearPendingUpload } = await import('../store/pendingUpload.js')
  const pend = getPendingUpload()
  let opciones = {}
  try { opciones = JSON.parse(sessionStorage.getItem('mf:validar') || '{}') } catch (e) { /* sin opciones */ }
  if (!pend?.files?.length) { router.replace('/'); return }
  fase.value = 'documento'
  progreso.value = 1
  try {
    const fd = new FormData()
    pend.files.forEach((f) => fd.append('files', f))
    fd.append('simulation_requirement', pend.simulationRequirement)
    const onto = await generateOntology(fd)
    const projectId = onto.data.project_id
    const r = await lanzarValidacion({ project_id: projectId, ...opciones })
    clearPendingUpload()
    taskId.value = r.data.task_id
    try { localStorage.setItem('mf:ultimo-recorrido', r.data.task_id) } catch (e) { /* sin almacenamiento */ }
    router.replace({ name: 'Live', params: { taskId: r.data.task_id } })
    actualizarRecorrido()
  } catch (e) {
    estado.value = 'failed'
    error.value = e.message
  }
}

// ---------------- sondeos ----------------
let timers = []
function detenerSondeos() { timers.forEach(clearInterval); timers = [] }

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen()
  else rootEl.value?.requestFullscreen?.()
}
const alCambiarFullscreen = () => { enFullscreen.value = !!document.fullscreenElement; setTimeout(alRedimensionar, 50) }

onMounted(async () => {
  await montarGrafo()
  document.addEventListener('fullscreenchange', alCambiarFullscreen)
  if (taskId.value === 'new') await iniciarDesdeInicio()
  else await actualizarRecorrido()
  if (terminado) return
  if (soloVer && fase.value === 'listo') { await actualizarGrafo(); setTimeout(() => encuadrar(900), 2500); return }
  timers.push(setInterval(actualizarRecorrido, 4000))
  timers.push(setInterval(actualizarGrafo, esMovil ? 7000 : 5000))
  timers.push(setInterval(() => { if (fase.value === 'simulacion' || fase.value === 'informe') actualizarSimulacion() }, 4000))
  actualizarGrafo()
})

onUnmounted(() => {
  cancelAnimationFrame(raf)
  detenerSondeos()
  window.removeEventListener('resize', alRedimensionar)
  document.removeEventListener('fullscreenchange', alCambiarFullscreen)
  try { graph3d?._destructor?.() } catch (e) { /* ya liberado */ }
})
</script>

<style scoped>
.live {
  --noche: #0B1026;
  --tinta: #E2E8F0;
  --tenue: rgba(226, 232, 240, 0.62);
  --cempasuchil: #F59E0B;
  position: fixed;
  inset: 0;
  overflow: hidden;
  background: radial-gradient(120% 80% at 50% 30%, #18234A 0%, var(--noche) 60%, #060914 100%);
  color: var(--tinta);
  font-family: 'Space Grotesk', system-ui, sans-serif;
}
.live-graph { position: absolute; inset: 0; }

.live-top {
  position: absolute; top: 0; left: 0; right: 0;
  display: flex; justify-content: space-between; align-items: center;
  padding: max(14px, env(safe-area-inset-top)) 18px 10px;
  pointer-events: none;
}
.live-top > * { pointer-events: auto; }
.brand {
  background: none; border: 0; color: var(--tinta); cursor: pointer;
  font-family: 'JetBrains Mono', monospace; font-weight: 800; letter-spacing: 0.08em; font-size: 15px;
}
.top-right { display: flex; gap: 10px; align-items: center; }
.top-right :deep(.saldo-badge) { color: var(--tinta); border-color: rgba(226, 232, 240, 0.35); background: rgba(11, 16, 38, 0.5); }
.icon-btn {
  display: grid; place-items: center; width: 32px; height: 32px; cursor: pointer;
  border: 1px solid rgba(226, 232, 240, 0.35); background: rgba(11, 16, 38, 0.5); color: var(--tinta);
}

.live-panel {
  position: absolute; left: 16px; bottom: max(16px, env(safe-area-inset-bottom));
  width: min(400px, calc(100% - 32px));
  background: rgba(11, 16, 38, 0.72); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(226, 232, 240, 0.12); border-radius: 18px;
  padding: 14px 16px 16px; max-height: 62vh; overflow-y: auto; overflow-x: hidden;
}
.panel-handle {
  display: flex; align-items: center; gap: 10px; width: 100%;
  background: none; border: 0; color: var(--tinta); cursor: pointer; padding: 0; text-align: left;
}
.fase-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--cempasuchil); box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.2); animation: latido 1.6s ease-in-out infinite; }
.fase-dot.ok { background: #34D399; box-shadow: 0 0 0 4px rgba(52, 211, 153, 0.2); animation: none; }
.fase-dot.err { background: #F87171; animation: none; }
.fase-txt { flex: 1; font-size: 16px; font-weight: 600; }
.fase-num { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--tenue); }
@keyframes latido { 50% { box-shadow: 0 0 0 7px rgba(245, 158, 11, 0.05); } }

.progreso { height: 3px; background: rgba(226, 232, 240, 0.12); border-radius: 3px; margin: 12px 0 10px; overflow: hidden; }
.progreso-barra { height: 100%; background: var(--cempasuchil); transition: width 0.8s ease; }

.pasos { list-style: none; display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 10px; padding: 0; font-size: 12px; color: var(--tenue); }
.pasos li { padding: 2px 8px; border-radius: 999px; border: 1px solid rgba(226, 232, 240, 0.14); }
.pasos li.hecho { color: var(--tinta); border-color: rgba(52, 211, 153, 0.45); }
.pasos li.actual { color: #1F1300; background: var(--cempasuchil); border-color: var(--cempasuchil); font-weight: 600; }

.sim-datos { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13px; color: var(--tenue); margin-bottom: 10px; font-variant-numeric: tabular-nums; }
.sim-datos b { color: var(--tinta); }

/* Burbujas sobre el grafo */
.burbujas { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.burbuja { position: absolute; top: 0; left: 0; width: 220px; will-change: transform; transition: transform 0.25s ease-out; }
.burbuja-caja {
  position: relative; background: rgba(255, 251, 240, 0.96); color: #1F1300; border-radius: 14px 14px 14px 4px;
  padding: 8px 11px; font-size: 13px; line-height: 1.38; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
  transform-origin: 24px 100%; transition: transform 0.6s cubic-bezier(0.5, 0, 0.75, 0), opacity 0.6s ease;
  overflow-wrap: anywhere;
}
.burbuja-caja::after {
  content: ''; position: absolute; left: 14px; bottom: -7px; width: 12px; height: 12px;
  background: inherit; transform: rotate(45deg); border-radius: 2px;
}
.burbuja-caja b { display: block; font-size: 12px; color: #92400E; margin-bottom: 2px; }
.burbuja.entrando .burbuja-caja { transform: scale(0.4) translateY(10px); opacity: 0; }
.burbuja.visible .burbuja-caja { transform: scale(1); opacity: 1; transition: transform 0.45s cubic-bezier(0.2, 0.9, 0.3, 1.25), opacity 0.3s ease; }
.burbuja.saliendo .burbuja-caja { transform: scale(0.05) translate(-40px, 60px); opacity: 0; }
.burbuja.flotante .burbuja-caja { border-radius: 14px; background: rgba(226, 232, 240, 0.92); }
.burbuja.flotante .burbuja-caja::after { display: none; }
.burbuja.flotante.visible .burbuja-caja { animation: sube 6s linear forwards; }
@keyframes sube { to { transform: translateY(-60px); } }

.ultima p { margin: 4px 0 2px; font-size: 13px; line-height: 1.4; color: #F1F5F9; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.pista { font-size: 12px; color: var(--cempasuchil); }

.feed-titulo { font-size: 12px; color: var(--tenue); margin: 6px 0 8px; }
.post { border-left: 2px solid var(--cempasuchil); padding: 2px 0 2px 10px; margin-bottom: 10px; animation: entra 0.5s ease; }
.post header { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.post header span { color: var(--tenue); font-size: 12px; }
.post p { margin: 2px 0 0; font-size: 14px; line-height: 1.45; color: #F1F5F9; overflow-wrap: anywhere; }
@keyframes entra { from { opacity: 0; transform: translateY(6px); } }
.nota { font-size: 14px; color: var(--tenue); margin: 4px 0 0; line-height: 1.5; }
.error p { color: #FCA5A5; font-size: 14px; }
.error button, .listo button {
  margin-top: 8px; padding: 10px 16px; border: 0; border-radius: 10px; cursor: pointer;
  background: var(--cempasuchil); color: #1F1300; font: inherit; font-weight: 700;
}

.listo {
  position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 24px; text-align: center; background: rgba(6, 9, 20, 0.72); backdrop-filter: blur(6px);
}
.listo img { width: min(560px, 92vw); border-radius: 14px; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.45); }
.listo h2 { margin: 14px 0 0; font-size: 24px; }
.listo p { margin: 0; color: var(--tenue); }
.listo-enter-active { transition: opacity 0.6s ease; }
.listo-enter-from { opacity: 0; }

.nodo-sel {
  position: absolute; top: calc(max(14px, env(safe-area-inset-top)) + 46px); right: 16px; width: min(320px, calc(100% - 32px));
  max-height: 55vh; overflow-y: auto; background: rgba(11, 16, 38, 0.9); backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 16px; padding: 14px 16px; font-size: 14px;
}
.nodo-sel .cerrar { position: absolute; top: 6px; right: 8px; background: none; border: 0; color: var(--tenue); font-size: 22px; cursor: pointer; }
.dijo-titulo { font-size: 12px; color: var(--tenue); margin: 10px 0 6px; }
.dijo p { margin: 0 0 8px; padding-left: 10px; border-left: 2px solid var(--cempasuchil); font-size: 13px; line-height: 1.45; color: #F1F5F9; }
.dijo em { font-style: normal; color: var(--cempasuchil); margin-right: 4px; }
.resumen { margin: 8px 0 0; color: var(--tenue); font-size: 13px; line-height: 1.45; }
.tarjeta-enter-active, .tarjeta-leave-active { transition: opacity 0.25s ease, transform 0.25s ease; }
.tarjeta-enter-from, .tarjeta-leave-to { opacity: 0; transform: translateY(-6px); }
.nodo-sel span { display: block; font-size: 12px; color: var(--cempasuchil); margin-top: 2px; }
.nodo-sel p { margin: 6px 0 0; color: var(--tenue); font-size: 13px; line-height: 1.45; max-height: 140px; overflow: hidden; }

@media (max-width: 768px) {
  .live-panel { left: 10px; right: 10px; width: auto; max-height: 46vh; border-radius: 16px; }
  .live-panel:not(.abierto) { max-height: 62px; overflow: hidden; }
  .post p { font-size: 13px; }
}
@media (max-width: 768px) {
  .burbuja { width: 190px; }
  .burbuja-caja { font-size: 12px; padding: 7px 10px; }
}
.fase-detalle { margin: 4px 0 6px; font-size: 12px; line-height: 1.4; color: rgba(226, 232, 240, 0.72); overflow-wrap: anywhere; }
.ver-informe { margin-top: 10px; width: 100%; padding: 10px; border: 0; border-radius: 10px; cursor: pointer;
  background: var(--cempasuchil); color: #1F1300; font: inherit; font-weight: 700; }
@media (prefers-reduced-motion: reduce) {
  .fase-dot, .post, .burbuja.flotante.visible .burbuja-caja { animation: none; }
  .burbuja-caja { transition: opacity 0.3s ease !important; }
  .progreso-barra { transition: none; }
}
</style>
