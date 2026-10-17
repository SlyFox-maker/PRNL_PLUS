import type { ObjectDirective } from 'vue'

const seen = new Set<string>()

// One decorative run per page lifetime; hidden/reduced-motion mounts count as seen.
export function claimEffect(name: string) {
  if (seen.has(name)) return false
  seen.add(name)
  return !document.hidden && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function watchMotionStop(stop: () => void) {
  const media = window.matchMedia('(prefers-reduced-motion: reduce)')
  const visibility = () => { if (document.hidden) stop() }
  const preference = () => { if (media.matches) stop() }
  media.addEventListener('change', preference)
  document.addEventListener('visibilitychange', visibility)
  let listening = true
  return () => {
    if (!listening) return
    listening = false
    media.removeEventListener('change', preference)
    document.removeEventListener('visibilitychange', visibility)
  }
}

function decorativeDirective(name: string, build: (element: HTMLElement) => Animation[]): ObjectDirective<HTMLElement> {
  const cleanups = new WeakMap<HTMLElement, () => void>()
  return {
    mounted(element) {
      if (!claimEffect(name)) return
      const animations = build(element)
      let removeListeners = () => {}
      const stop = () => {
        for (const animation of animations) { animation.onfinish = null; animation.cancel() }
        removeListeners()
        cleanups.delete(element)
      }
      removeListeners = watchMotionStop(stop)
      cleanups.set(element, stop)
      let remaining = animations.length
      for (const animation of animations) animation.onfinish = () => { if (--remaining === 0) stop() }
      if (!remaining) stop()
    },
    beforeUnmount(element) { cleanups.get(element)?.() },
  }
}

// Reveals a decorative label, never fabricates progress or system events.
export const vTerminal = decorativeDirective('terminal', element => [element.animate([
  { clipPath: 'inset(0 100% 0 0)' }, { clipPath: 'inset(0 0% 0 0)' },
], { duration: 360, easing: 'steps(18, end)' })])

// Existing card entrance belongs to stage 2. Boot touches only static chrome.
export const vBoot = decorativeDirective('boot', element =>
  ['.brand', 'nav', '.topbar', '.page-heading'].flatMap((selector, index) => {
    const target = element.querySelector<HTMLElement>(selector)
    return target ? [target.animate([
      { opacity: .75, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' },
    ], { duration: 260, delay: index * 35, easing: 'cubic-bezier(.2,.7,.2,1)', fill: 'backwards' })] : []
  }),
)
