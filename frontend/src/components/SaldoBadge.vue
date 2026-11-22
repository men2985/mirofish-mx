<template>
  <!-- Saldo de créditos siempre visible (F4, parche MX 2026-11-22). Las cuentas 'bypass' no lo ven. -->
  <a
    v-if="visible"
    class="saldo-badge"
    :class="{ agotado: saldo <= 0 }"
    :href="enlace"
    :title="saldo <= 0 ? 'Sin saldo: toca para recargar' : 'Mis créditos'"
  >
    <span class="saldo-num">{{ saldo.toFixed(2) }}</span>
    <span class="saldo-txt">créditos</span>
  </a>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const visible = ref(false)
const saldo = ref(0)
let timer = null

const enlace = computed(() => `/_auth/creditos?next=${encodeURIComponent(route?.fullPath || '/')}`)

async function cargar() {
  if (document.visibilityState === 'hidden') return
  try {
    const r = await fetch('/_auth/me', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    if (!r.ok) return
    const me = await r.json()
    visible.value = me.account_type === 'credit'
    saldo.value = Number(me.saldo_usd) || 0
  } catch (e) {
    // sin red: se conserva el último saldo mostrado
  }
}

const alVolver = () => { if (document.visibilityState === 'visible') cargar() }

onMounted(() => {
  cargar()
  timer = setInterval(cargar, 30000)
  document.addEventListener('visibilitychange', alVolver)
  window.addEventListener('mf:saldo', cargar)
})
onUnmounted(() => {
  clearInterval(timer)
  document.removeEventListener('visibilitychange', alVolver)
  window.removeEventListener('mf:saldo', cargar)
})
</script>

<style scoped>
.saldo-badge {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 12px;
  border: 1px solid #CCC;
  color: #333;
  background: transparent;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  text-decoration: none;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  transition: border-color 0.2s;
}
.saldo-badge:hover { border-color: #999; }
.saldo-badge:focus-visible { outline: 2px solid #111; outline-offset: 2px; }
.saldo-num { font-weight: 600; }
.saldo-badge.agotado { border-color: #B42318; color: #B42318; }

@media (max-width: 480px) {
  .saldo-txt { display: none; }
  .saldo-badge { padding: 4px 8px; }
}
</style>
