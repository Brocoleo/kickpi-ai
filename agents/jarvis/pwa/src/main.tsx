import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { registerSW } from 'virtual:pwa-register'
import './index.css'
import App from './App.tsx'

// Auto-actualizacion de la PWA.
//
// El Service Worker comprueba si hay una version nueva al arrancar, cada
// minuto y cada vez que la app vuelve a primer plano. Si la hay y no hay
// una decision abierta (el modal bloquea), se activa y recarga sola.
// Esto evita el clasico "sigo viendo el bundle viejo" en el iPhone.
const updateSW = registerSW({
  immediate: true,
  onNeedRefresh() {
    if (document.body.classList.contains('modal-open')) return
    void updateSW(true)
  },
  onRegisteredSW(_url, registration) {
    if (!registration) return
    const check = () => {
      void registration.update().catch(() => {
        /* sin red: se reintenta en el siguiente ciclo */
      })
    }
    const interval = window.setInterval(check, 60_000)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') check()
    })
    window.addEventListener('beforeunload', () => window.clearInterval(interval))
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
