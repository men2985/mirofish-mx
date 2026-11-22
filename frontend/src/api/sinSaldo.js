// Aviso de saldo agotado (F4, parche MX 2026-11-22).
// Cuando cualquier acción de IA responde 402 { sin_saldo: true }, se muestra este aviso con un botón que lleva a
// "Mis créditos" (servicio auth) y vuelve a la misma página al terminar de pagar. Sin dependencias de UI.

let abierto = false

function volverAqui() {
  // /process/new solo existe mientras se sube un archivo (vive en memoria): tras pagar se vuelve al inicio
  if (window.location.pathname.startsWith('/process/new')) return encodeURIComponent('/')
  return encodeURIComponent(window.location.pathname + window.location.search + window.location.hash)
}

export function irARecargar(ruta) {
  const base = ruta || '/_auth/creditos'
  window.location.href = `${base}${base.includes('?') ? '&' : '?'}next=${volverAqui()}`
}

export function mostrarSinSaldo(mensaje, ruta) {
  if (abierto || typeof document === 'undefined') return
  abierto = true

  const fondo = document.createElement('div')
  fondo.setAttribute('role', 'dialog')
  fondo.setAttribute('aria-modal', 'true')
  fondo.setAttribute('aria-labelledby', 'mf-sin-saldo-titulo')
  fondo.style.cssText =
    'position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;' +
    'padding:16px;background:rgba(17,24,39,.45);backdrop-filter:blur(3px)'

  const caja = document.createElement('div')
  caja.style.cssText =
    'width:100%;max-width:380px;background:#fff;color:#111827;border-radius:12px;padding:24px;' +
    'box-shadow:0 20px 40px rgba(0,0,0,.18);font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif'

  const titulo = document.createElement('h2')
  titulo.id = 'mf-sin-saldo-titulo'
  titulo.textContent = 'Te quedaste sin créditos'
  titulo.style.cssText = 'margin:0 0 8px;font-size:18px'

  const texto = document.createElement('p')
  texto.textContent = mensaje || 'Recarga créditos para seguir validando ideas.'
  texto.style.cssText = 'margin:0 0 20px;color:#4B5563'

  const recargar = document.createElement('button')
  recargar.type = 'button'
  recargar.textContent = 'Recargar créditos'
  recargar.style.cssText =
    'width:100%;padding:12px;border:0;border-radius:8px;background:#111827;color:#fff;font-size:15px;' +
    'font-weight:600;cursor:pointer;margin-bottom:8px'

  const cerrar = document.createElement('button')
  cerrar.type = 'button'
  cerrar.textContent = 'Ahora no'
  cerrar.style.cssText =
    'width:100%;padding:10px;border:1px solid #E5E7EB;border-radius:8px;background:transparent;color:#374151;' +
    'font-size:15px;cursor:pointer'

  const quitar = () => {
    fondo.remove()
    abierto = false
    document.removeEventListener('keydown', alTeclado)
  }
  const alTeclado = (e) => { if (e.key === 'Escape') quitar() }

  recargar.addEventListener('click', () => irARecargar(ruta))
  cerrar.addEventListener('click', quitar)
  fondo.addEventListener('click', (e) => { if (e.target === fondo) quitar() })
  document.addEventListener('keydown', alTeclado)

  caja.append(titulo, texto, recargar, cerrar)
  fondo.appendChild(caja)
  document.body.appendChild(fondo)
  recargar.focus()
}
