import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
// Capa móvil (parche MX): un solo archivo con todos los ajustes de celular
import './styles/mobile.css'

const app = createApp(App)

app.use(router)
app.use(i18n)

app.mount('#app')

// Celular: las consolas de registro son un cajón; tocar su barra lo abre o lo cierra
const mobileQuery = window.matchMedia('(max-width: 768px)')
document.addEventListener('click', (event) => {
  if (!mobileQuery.matches) return
  const header = event.target.closest('.system-logs .log-header, .console-logs .log-header')
  if (header) header.parentElement.classList.toggle('is-open')
})
