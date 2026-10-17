import type { ObjectDirective } from 'vue'

type Feedback = { value: unknown; animation?: Animation; media: MediaQueryList; stop: () => void }
const feedback = new WeakMap<HTMLElement, Feedback>()

// Scalar bindings compare meaningful values, never SSE object identities or timestamps.
export const vChanged: ObjectDirective<HTMLElement, unknown> = {
  mounted(element, binding) {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const state: Feedback = { value: binding.value, media, stop: () => {} }
    state.stop = () => { state.animation?.cancel(); state.animation = undefined }
    media.addEventListener('change', state.stop)
    feedback.set(element, state)
  },
  updated(element, binding) {
    const state = feedback.get(element)
    if (!state || Object.is(state.value, binding.value)) return
    state.value = binding.value
    state.stop()
    if (document.hidden || state.media.matches) return
    state.animation = element.animate([
      { backgroundColor: 'rgba(197,138,255,.19)' },
      { backgroundColor: 'rgba(197,138,255,0)' },
    ], { duration: 480, easing: 'ease-out' })
    state.animation.onfinish = state.stop
  },
  beforeUnmount(element) {
    const state = feedback.get(element)
    if (!state) return
    state.stop()
    state.media.removeEventListener('change', state.stop)
    feedback.delete(element)
  },
}
