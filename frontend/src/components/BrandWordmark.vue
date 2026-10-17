<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { claimEffect, watchMotionStop } from '../atmosphere'

const title = ref('PRNL'), subtitle = ref('+'), accentReady = ref(false)
let frame = 0
let removeListeners = () => {}
function finish() {
  cancelAnimationFrame(frame)
  frame = 0
  accentReady.value = false
  title.value = 'PRNL'
  subtitle.value = '+'
  removeListeners()
}
onMounted(() => {
  if (!claimEffect('wordmark')) return
  accentReady.value = true
  removeListeners = watchMotionStop(finish)
  const start = performance.now()
  let lastUpdate = -Infinity
  const glyphs = '01ABCDEF/#'
  const reveal = (text: string, progress: number) => [...text].map((letter, index) =>
    index < Math.floor(progress * text.length) ? letter : glyphs[Math.floor(Math.random() * glyphs.length)],
  ).join('')
  function tick(time: number) {
    const progress = Math.min((time - start) / 560, 1)
    if (progress >= 1 || document.hidden) { finish(); return }
    if (time - lastUpdate >= 32) {
      title.value = reveal('PRNL', progress)
      subtitle.value = reveal('+', progress)
      lastUpdate = time
    }
    frame = requestAnimationFrame(tick)
  }
  frame = requestAnimationFrame(tick)
})
onBeforeUnmount(finish)
</script>

<template>
  <span class="wordmark" role="img" aria-label="PRNL plus">
    <span class="wordmark-line" aria-hidden="true"><span class="wordmark-base">PRNL</span><span class="wordmark-display">{{ title }}</span></span><span class="brand-sub wordmark-line wordmark-plus" aria-hidden="true"><span class="wordmark-base">+</span><span class="wordmark-display" :class="{ 'accent-ready': accentReady }">{{ subtitle }}</span></span>
  </span>
</template>
