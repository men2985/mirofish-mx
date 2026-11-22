import axios from 'axios'
import i18n from '../i18n'
import { mostrarSinSaldo } from './sinSaldo'

// 创建axios实例
const service = axios.create({
  // Producción (parche MX): rutas relativas al mismo dominio (nginx reenvía /api al backend);
  // así el mismo build sirve en cualquier dominio o tenant. En desarrollo sigue apuntando a localhost:5001.
  baseURL: import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.PROD ? '' : 'http://localhost:5001'),
  timeout: 300000, // 5分钟超时（本体生成可能需要较长时间）
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    // F4: tras una acción (POST) el saldo pudo cambiar: el indicador de créditos se refresca
    if ((response.config?.method || '').toLowerCase() === 'post') {
      window.dispatchEvent(new Event('mf:saldo'))
    }
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)
    const apiError = error.response?.data?.error || error.response?.data?.message

    // F4: saldo agotado -> aviso con botón para recargar; sesión vencida -> al login y de vuelta aquí
    if (error.response?.status === 402 && error.response?.data?.sin_saldo) {
      mostrarSinSaldo(apiError, error.response.data.recargar)
    } else if (error.response?.status === 401) {
      const aqui = encodeURIComponent(window.location.pathname + window.location.search)
      window.location.href = `/_auth/login?next=${aqui}`
    }
    
    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    // 处理网络错误
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }

    // Axios rejects non-2xx responses before the success interceptor can
    // surface the backend's safe, actionable error message.
    if (typeof apiError === 'string' && apiError) {
      error.message = apiError
    }
    
    return Promise.reject(error)
  }
)

export default service
