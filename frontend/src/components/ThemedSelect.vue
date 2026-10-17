<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ChevronDown } from 'lucide-vue-next'

type Option = { value: string; label: string }
const props = defineProps<{ modelValue: string; options: Option[]; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const id = `prnl-select-${Math.random().toString(36).slice(2)}`
const root = ref<HTMLElement|null>(null)
const trigger = ref<HTMLButtonElement|null>(null)
const opened = ref(false)
const activeIndex = ref(0)
const selectedIndex = computed(() => props.options.findIndex(option => option.value === props.modelValue))
const selectedLabel = computed(() => props.options[selectedIndex.value]?.label || 'Выбрать…')

function close() { opened.value = false }
function toggle() {
  opened.value = !opened.value
  if (opened.value) activeIndex.value = Math.max(0, selectedIndex.value)
}
function choose(option: Option) {
  emit('update:modelValue', option.value)
  close()
  trigger.value?.focus()
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!opened.value) {
      opened.value = true
      activeIndex.value = Math.max(0, selectedIndex.value)
    } else {
      const delta = event.key === 'ArrowDown' ? 1 : -1
      activeIndex.value = (activeIndex.value + delta + props.options.length) % props.options.length
    }
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    opened.value = true
    activeIndex.value = event.key === 'Home' ? 0 : props.options.length - 1
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (opened.value && props.options[activeIndex.value]) choose(props.options[activeIndex.value])
    else toggle()
  } else if (event.key === 'Escape' && opened.value) {
    event.preventDefault()
    close()
  } else if (event.key === 'Tab') close()
}
function onOutsidePointer(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) close()
}
onMounted(() => document.addEventListener('pointerdown', onOutsidePointer))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutsidePointer))
</script>

<template>
  <div ref="root" class="themed-select">
    <button
      ref="trigger"
      class="themed-select-trigger"
      type="button"
      role="combobox"
      :aria-label="label"
      :aria-expanded="opened"
      :aria-controls="`${id}-options`"
      :aria-activedescendant="opened ? `${id}-option-${activeIndex}` : undefined"
      @click="toggle"
      @keydown="onKeydown"
    >
      <span>{{ selectedLabel }}</span>
      <ChevronDown :size="15" :class="{ rotated: opened }" aria-hidden="true" />
    </button>
    <Transition name="select-pop">
      <div v-if="opened" :id="`${id}-options`" class="themed-select-options" role="listbox" :aria-label="label">
        <button
          v-for="(option, index) in options"
          :id="`${id}-option-${index}`"
          :key="option.value"
          type="button"
          role="option"
          tabindex="-1"
          :aria-selected="option.value === modelValue"
          :class="{ active: index === activeIndex, selected: option.value === modelValue }"
          @mouseenter="activeIndex = index"
          @click="choose(option)"
        >{{ option.label }}</button>
      </div>
    </Transition>
  </div>
</template>
