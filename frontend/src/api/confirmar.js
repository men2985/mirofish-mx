// Confirmación antes de gastar créditos + modo automático (parche MX).
// Ningún paso de la mesa de trabajo que gaste IA arranca solo. La ventana muestra el costo estimado y el saldo; con
// `tarifas` y `ajustes` deja mover agentes y rondas y lo recalcula todo al instante (créditos, minutos reales y tiempo
// simulado). Si el saldo no alcanza, ofrece "Ajustar a mi saldo".
import { irARecargar } from './sinSaldo'

const AUTO_KEY = (sid) => `mf:auto:${sid}`
const OK_KEY = (sid, paso) => `mf:ok:${paso}:${sid}`
const RONDAS_KEY = (sid) => `mf:rondas:${sid}`

export function setAuto(sid, on) {
  try { on ? sessionStorage.setItem(AUTO_KEY(sid), '1') : sessionStorage.removeItem(AUTO_KEY(sid)) } catch (e) { /* sin almacenamiento */ }
}
export function isAuto(sid) {
  try { return sessionStorage.getItem(AUTO_KEY(sid)) === '1' } catch (e) { return false }
}
export function marcarConfirmado(sid, paso) {
  try { sessionStorage.setItem(OK_KEY(sid, paso), '1') } catch (e) { /* sin almacenamiento */ }
}
export function tomarConfirmado(sid, paso) {
  try {
    const ok = sessionStorage.getItem(OK_KEY(sid, paso)) === '1'
    sessionStorage.removeItem(OK_KEY(sid, paso))
    return ok
  } catch (e) { return false }
}
// Rondas elegidas al preparar: el paso 2 las aplica cuando llega la configuración
export function guardarRondas(sid, n) {
  try { sessionStorage.setItem(RONDAS_KEY(sid), String(n)) } catch (e) { /* sin almacenamiento */ }
}
export function leerRondas(sid) {
  try { return Number(sessionStorage.getItem(RONDAS_KEY(sid))) || null } catch (e) { return null }
}

const fmt = (n) => (Number(n) || 0).toFixed(2)

export function tiempoSimulado(rondas, minutosPorRonda = 60) {
  const horas = (rondas * minutosPorRonda) / 60
  if (horas < 48) return `${Math.round(horas)} h de vida en la ciudad`
  const dias = horas / 24
  return `${dias % 1 === 0 ? dias : dias.toFixed(1)} días de vida en la ciudad`
}

/**
 * @param {Object} o
 * @param {string} o.titulo
 * @param {string} [o.detalle]
 * @param {Array<{label:string, creditos:number}>} [o.lineas]   líneas fijas (sin barras)
 * @param {Object} [o.tarifas]   {preparacion_por_agente, simulacion_por_agente_ronda, informe, segundos_por_agente_ronda,
 *                                minutos_simulados_por_ronda}
 * @param {Object} [o.ajustes]   {agentes:{min,max,valor}, rondas:{min,max,valor}, incluirPreparacion, incluirInforme}
 * @param {{account_type?:string, saldo?:number}} [o.cuenta]
 * @param {boolean} [o.ofrecerAuto]
 * @param {string} [o.boton]
 * @returns {Promise<{ok:boolean, auto:boolean, agentes?:number, rondas?:number}>}
 */
export function confirmarGasto(o) {
  return new Promise((resolve) => {
    const bypass = o.cuenta?.account_type === 'bypass'
    const saldo = Number(o.cuenta?.saldo) || 0
    const t = o.tarifas || null
    const aj = o.ajustes || {}
    const estado = {
      agentes: aj.agentes ? aj.agentes.valor : null,
      rondas: aj.rondas ? aj.rondas.valor : null,
    }

    const calcular = () => {
      if (!t) return { lineas: o.lineas || [], minutos: null }
      const ag = estado.agentes || 0
      const r = estado.rondas || 0
      const lineas = []
      if (aj.incluirPreparacion) lineas.push({ label: 'Preparación', creditos: ag * t.preparacion_por_agente })
      if (r) lineas.push({ label: 'Simulación', creditos: ag * r * t.simulacion_por_agente_ronda })
      if (aj.incluirInforme) lineas.push({ label: 'Informe', creditos: t.informe })
      const minutos = Math.max(1, Math.round((ag * r * (t.segundos_por_agente_ronda || 0.15)) / 60))
      return { lineas, minutos }
    }

    const ajustarASaldo = () => {
      if (!t) return
      const fijo = aj.incluirInforme ? t.informe : 0
      const prepA = aj.incluirPreparacion ? t.preparacion_por_agente : 0
      const simAR = t.simulacion_por_agente_ronda
      // Primero se bajan los AGENTES conservando las rondas (al menos 24 = un día completo: de madrugada casi nadie
      // publica); solo si con el mínimo de agentes no alcanza, se bajan las rondas.
      let r = estado.rondas || 0
      if (aj.rondas) r = Math.max(Math.min(r, aj.rondas.max), Math.min(24, aj.rondas.max))
      if (aj.agentes && aj.agentes.max > aj.agentes.min) {
        const ag = Math.floor((saldo - fijo) / Math.max(prepA + r * simAR, 1e-9))
        estado.agentes = Math.max(aj.agentes.min, Math.min(aj.agentes.max, ag))
      }
      if (aj.rondas) {
        const ag = estado.agentes || 0
        const rFit = Math.floor((saldo - fijo - ag * prepA) / Math.max(ag * simAR, 1e-9))
        estado.rondas = Math.max(aj.rondas.min, Math.min(r, rFit))
      }
      pintar()
    }

    const fondo = document.createElement('div')
    fondo.setAttribute('role', 'dialog')
    fondo.setAttribute('aria-modal', 'true')
    fondo.setAttribute('aria-labelledby', 'mf-confirmar-titulo')
    fondo.style.cssText =
      'position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;' +
      'padding:16px;background:rgba(17,24,39,.45);backdrop-filter:blur(3px);overflow-y:auto'

    const barra = (id, etiqueta, a) => `
      <label for="${id}" style="display:block;font-size:13px;color:#4B5563;margin:12px 0 2px">${etiqueta}:
        <b id="${id}-val" style="color:#111827"></b> <span id="${id}-sub" style="color:#6B7280"></span></label>
      <input id="${id}" type="range" min="${a.min}" max="${a.max}" step="1" value="${a.valor}"
             style="width:100%;accent-color:#111827">`

    fondo.innerHTML = `
      <div style="width:100%;max-width:400px;background:#fff;color:#111827;border-radius:12px;padding:24px;margin:auto;
                  box-shadow:0 20px 40px rgba(0,0,0,.18);font:15px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif">
        <h2 id="mf-confirmar-titulo" style="margin:0 0 6px;font-size:18px">${o.titulo}</h2>
        ${o.detalle ? `<p style="margin:0 0 6px;color:#4B5563;font-size:14px">${o.detalle}</p>` : ''}
        ${aj.agentes && aj.agentes.max > aj.agentes.min ? barra('mf-agentes', 'Agentes', aj.agentes) : ''}
        ${aj.rondas && aj.rondas.max > aj.rondas.min ? barra('mf-rondas', 'Rondas', aj.rondas) : ''}
        <div id="mf-lineas" style="font-size:14px;margin:14px 0 6px"></div>
        <div style="display:flex;justify-content:space-between;font-weight:600;padding:8px 0 2px">
          <span>Total estimado</span><span id="mf-total" style="font-variant-numeric:tabular-nums"></span>
        </div>
        <p id="mf-saldo" style="margin:0 0 10px;font-size:13px"></p>
        <button type="button" id="mf-ajustar" style="display:none;width:100%;padding:9px;border:1px dashed #111827;
                border-radius:8px;background:#fff;color:#111827;font-size:14px;cursor:pointer;margin-bottom:12px">
          Ajustar a mi saldo</button>
        ${o.ofrecerAuto ? `
        <label style="display:flex;gap:10px;align-items:flex-start;font-size:14px;margin:0 0 16px;cursor:pointer">
          <input type="checkbox" id="mf-auto" style="margin-top:3px;width:18px;height:18px">
          <span>Continuar automáticamente hasta el informe<br>
            <span style="color:#6B7280;font-size:13px">Se prepara, se simula y se genera el informe sin pedirte nada más.</span></span>
        </label>` : ''}
        <button type="button" id="mf-ok" style="width:100%;padding:12px;border:0;border-radius:8px;background:#111827;
                color:#fff;font-size:15px;font-weight:600;cursor:pointer;margin-bottom:8px">${o.boton || 'Continuar'}</button>
        <button type="button" id="mf-recargar" style="display:none;width:100%;padding:10px;border:1px solid #111827;
                border-radius:8px;background:#fff;color:#111827;font-size:15px;cursor:pointer;margin-bottom:8px">
          Recargar créditos</button>
        <button type="button" id="mf-no" style="width:100%;padding:10px;border:1px solid #E5E7EB;border-radius:8px;
                background:transparent;color:#374151;font-size:15px;cursor:pointer">Cancelar</button>
      </div>`

    const $ = (sel) => fondo.querySelector(sel)

    function pintar() {
      const { lineas, minutos } = calcular()
      const total = lineas.reduce((s, l) => s + (Number(l.creditos) || 0), 0)
      const noAlcanza = !bypass && saldo < total
      if ($('#mf-agentes')) {
        $('#mf-agentes').value = estado.agentes
        $('#mf-agentes-val').textContent = estado.agentes
      }
      if ($('#mf-rondas')) {
        $('#mf-rondas').value = estado.rondas
        $('#mf-rondas-val').textContent = estado.rondas
        $('#mf-rondas-sub').textContent =
          `= ${tiempoSimulado(estado.rondas, t?.minutos_simulados_por_ronda || 60)} · aprox. ${minutos} min reales`
      }
      $('#mf-lineas').innerHTML = lineas.map((l) =>
        `<div style="display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid #F0F0F0">
           <span>${l.label}</span><span style="font-variant-numeric:tabular-nums;white-space:nowrap">≈ ${fmt(l.creditos)}</span>
         </div>`).join('')
      $('#mf-total').textContent = `≈ ${fmt(total)} créditos`
      $('#mf-saldo').style.color = noAlcanza ? '#B42318' : '#6B7280'
      $('#mf-saldo').textContent = bypass ? 'Tu cuenta no paga créditos. Es una estimación.'
        : `Tienes ${fmt(saldo)} créditos.${noAlcanza ? ' No alcanza para esta configuración.' : ''} Es una estimación; se cobra lo que realmente consume.`
      $('#mf-ajustar').style.display = noAlcanza && t && (aj.rondas || aj.agentes) ? 'block' : 'none'
      $('#mf-recargar').style.display = noAlcanza ? 'block' : 'none'
    }

    const cerrar = (res) => {
      fondo.remove()
      document.removeEventListener('keydown', alTeclado)
      resolve(res)
    }
    const alTeclado = (e) => { if (e.key === 'Escape') cerrar({ ok: false, auto: false }) }
    document.addEventListener('keydown', alTeclado)
    document.body.appendChild(fondo)

    $('#mf-agentes')?.addEventListener('input', (e) => { estado.agentes = Number(e.target.value); pintar() })
    $('#mf-rondas')?.addEventListener('input', (e) => { estado.rondas = Number(e.target.value); pintar() })
    $('#mf-ajustar').addEventListener('click', ajustarASaldo)
    $('#mf-ok').addEventListener('click', () => cerrar({
      ok: true, auto: !!$('#mf-auto')?.checked,
      agentes: estado.agentes ?? undefined, rondas: estado.rondas ?? undefined,
    }))
    $('#mf-no').addEventListener('click', () => cerrar({ ok: false, auto: false }))
    $('#mf-recargar').addEventListener('click', () => irARecargar())
    pintar()
    $('#mf-ok').focus()
  })
}

/**
 * Confirmación ÚNICA del recorrido completo "validar idea" (inicio -> pantalla en vivo -> informe).
 * @param {Object} o  {tarifas, cuenta, ciudad, fragmentos}
 * @returns {Promise<{ok:boolean, n_ciudadanos?:number, max_agentes?:number, rondas?:number}>}
 */
export function confirmarValidacion(o) {
  return new Promise((resolve) => {
    const t = o.tarifas || {}
    const bypass = o.cuenta?.account_type === 'bypass'
    const saldo = Number(o.cuenta?.saldo) || 0
    const conCiudad = !!o.ciudad
    const est = { ciudadanos: conCiudad ? 40 : 0, actores: 40, rondas: 40 }
    const frag = Math.max(1, o.fragmentos || 20)

    const calcular = () => {
      const agentes = est.ciudadanos + est.actores
      const lineas = [
        { label: 'Leer tu documento y armar el grafo', creditos: frag * t.grafo_por_fragmento },
        { label: `Preparar ${agentes} agentes`, creditos: est.actores * t.preparacion_por_agente + est.ciudadanos * (t.preparacion_por_agente + t.ciudadano_por_idea) },
        { label: `Conversación, ${est.rondas} rondas`, creditos: agentes * est.rondas * t.simulacion_por_agente_ronda },
        { label: 'Informe e infografía', creditos: (t.informe || 0) + (t.ilustracion || 0) },
      ]
      const total = lineas.reduce((s, l) => s + (Number(l.creditos) || 0), 0)
      const minutos = Math.max(1, Math.round(agentes * est.rondas * (t.segundos_por_agente_ronda || 0.15) / 60))
      return { lineas, total, minutos, agentes }
    }

    const fondo = document.createElement('div')
    fondo.setAttribute('role', 'dialog')
    fondo.setAttribute('aria-modal', 'true')
    fondo.setAttribute('aria-labelledby', 'mf-val-titulo')
    fondo.style.cssText =
      'position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;' +
      'padding:16px;background:rgba(10,14,32,.55);backdrop-filter:blur(4px);overflow-y:auto'
    const barra = (id, etiqueta, min, max, valor, ayuda) => `
      <label for="${id}" style="display:flex;justify-content:space-between;font-size:13px;color:#4B5563;margin:14px 0 2px">
        <span>${etiqueta}</span><b id="${id}-val" style="color:#111827;font-variant-numeric:tabular-nums"></b></label>
      <input id="${id}" type="range" min="${min}" max="${max}" step="1" value="${valor}" style="width:100%;accent-color:#111827">
      ${ayuda ? `<div id="${id}-ayuda" style="font-size:12px;color:#6B7280">${ayuda}</div>` : ''}`
    fondo.innerHTML = `
      <div style="width:100%;max-width:420px;background:#fff;color:#111827;border-radius:14px;padding:24px;margin:auto;
                  box-shadow:0 24px 48px rgba(0,0,0,.22);font:15px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif">
        <h2 id="mf-val-titulo" style="margin:0 0 4px;font-size:19px">Validar tu idea en la ciudad</h2>
        <p style="margin:0;color:#4B5563;font-size:14px">${conCiudad
          ? `Ciudad: <b>${o.ciudad.nombre}</b>. Verás el grafo crecer en vivo y al final tu informe.`
          : 'Sin ciudad confirmada: solo participan los actores de tu documento.'}</p>
        ${conCiudad ? barra('mf-v-ciud', 'Ciudadanos de tu ciudad', 0, 150, est.ciudadanos, 'Personas comunes con datos del censo: opinan como tu público.') : ''}
        ${barra('mf-v-act', 'Actores de tu documento (máximo)', 10, 100, est.actores, 'Tu negocio, competencia, medios… los que aparezcan en el documento.')}
        ${barra('mf-v-ron', 'Rondas', 24, 168, est.rondas, '')}
        <div id="mf-v-lineas" style="font-size:14px;margin:16px 0 6px"></div>
        <div style="display:flex;justify-content:space-between;font-weight:600;padding:8px 0 2px">
          <span>Total estimado</span><span id="mf-v-total" style="font-variant-numeric:tabular-nums"></span></div>
        <p id="mf-v-saldo" style="margin:0 0 16px;font-size:13px"></p>
        <button type="button" id="mf-v-ok" style="width:100%;padding:13px;border:0;border-radius:10px;background:#111827;
                color:#fff;font-size:16px;font-weight:600;cursor:pointer;margin-bottom:8px">Lanzar a la ciudad</button>
        <button type="button" id="mf-v-recargar" style="display:none;width:100%;padding:10px;border:1px solid #111827;
                border-radius:10px;background:#fff;color:#111827;font-size:15px;cursor:pointer;margin-bottom:8px">Recargar créditos</button>
        <button type="button" id="mf-v-no" style="width:100%;padding:10px;border:1px solid #E5E7EB;border-radius:10px;
                background:transparent;color:#374151;font-size:15px;cursor:pointer">Cancelar</button>
      </div>`
    const $ = (s) => fondo.querySelector(s)
    function pintar() {
      const c = calcular()
      if ($('#mf-v-ciud')) $('#mf-v-ciud-val').textContent = est.ciudadanos
      $('#mf-v-act-val').textContent = est.actores
      $('#mf-v-ron-val').textContent = `${est.rondas} · ${tiempoSimulado(est.rondas)}`
      $('#mf-v-lineas').innerHTML = c.lineas.map((l) =>
        `<div style="display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid #F0F0F0">
           <span>${l.label}</span><span style="font-variant-numeric:tabular-nums;white-space:nowrap">≈ ${fmt(l.creditos)}</span></div>`).join('')
      $('#mf-v-total').textContent = `≈ ${fmt(c.total)} créditos · ~${c.minutos + 8} min`
      const noAlcanza = !bypass && saldo < c.total
      $('#mf-v-saldo').style.color = noAlcanza ? '#B42318' : '#6B7280'
      $('#mf-v-saldo').textContent = bypass ? 'Tu cuenta no paga créditos. Es una estimación.'
        : `Tienes ${fmt(saldo)} créditos.${noAlcanza ? ' No alcanza: baja ciudadanos o rondas, o recarga.' : ''} Se cobra lo que realmente se consume.`
      $('#mf-v-recargar').style.display = noAlcanza ? 'block' : 'none'
    }
    const cerrar = (r) => { fondo.remove(); document.removeEventListener('keydown', tecla); resolve(r) }
    const tecla = (e) => { if (e.key === 'Escape') cerrar({ ok: false }) }
    document.addEventListener('keydown', tecla)
    document.body.appendChild(fondo)
    $('#mf-v-ciud')?.addEventListener('input', (e) => { est.ciudadanos = Number(e.target.value); pintar() })
    $('#mf-v-act').addEventListener('input', (e) => { est.actores = Number(e.target.value); pintar() })
    $('#mf-v-ron').addEventListener('input', (e) => { est.rondas = Number(e.target.value); pintar() })
    $('#mf-v-ok').addEventListener('click', () => cerrar({ ok: true, n_ciudadanos: est.ciudadanos, max_agentes: est.actores, rondas: est.rondas }))
    $('#mf-v-no').addEventListener('click', () => cerrar({ ok: false }))
    $('#mf-v-recargar').addEventListener('click', () => irARecargar())
    pintar()
    $('#mf-v-ok').focus()
  })
}
