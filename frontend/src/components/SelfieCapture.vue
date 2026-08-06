<script setup>
import { ref } from 'vue'

const emit = defineEmits(['captured', 'cancel'])

// Deliberately not using getUserMedia: it needs a "secure context" (https,
// or localhost), which a phone on plain http://<lan-ip> doesn't have. A
// file input with `capture` instead hands off to the OS's own camera app -
// no live in-page preview, but it works over plain HTTP on both iOS and
// Android, and needs no separate permission-prompt handling.
const fileInput = ref(null)
const canvasEl = ref(null)
const error = ref(null)
const busy = ref(false)

function openCamera() {
  fileInput.value?.click()
}

function onFileChosen(evt) {
  const file = evt.target.files?.[0]
  evt.target.value = '' // reset so choosing the same file again still fires 'change'
  if (!file) return

  error.value = null
  busy.value = true
  const img = new Image()
  const objectUrl = URL.createObjectURL(file)

  img.onload = () => {
    URL.revokeObjectURL(objectUrl)
    // Center-crop to square, matching the gallery avatars' aspect ratio.
    const size = Math.min(img.naturalWidth, img.naturalHeight)
    canvasEl.value.width = size
    canvasEl.value.height = size
    const ctx = canvasEl.value.getContext('2d')
    const sx = (img.naturalWidth - size) / 2
    const sy = (img.naturalHeight - size) / 2
    ctx.drawImage(img, sx, sy, size, size, 0, 0, size, size)
    canvasEl.value.toBlob(
      (blob) => {
        busy.value = false
        emit('captured', blob)
      },
      'image/jpeg',
      0.85,
    )
  }
  img.onerror = () => {
    URL.revokeObjectURL(objectUrl)
    busy.value = false
    error.value = 'Could not read that photo — please try again.'
  }
  img.src = objectUrl
}
</script>

<template>
  <div class="selfie-overlay">
    <p class="muted">Opens your camera app.</p>
    <p v-if="error" class="status error">{{ error }}</p>
    <input
      ref="fileInput"
      type="file"
      accept="image/*"
      capture="user"
      class="hidden-input"
      @change="onFileChosen"
    />
    <canvas ref="canvasEl" style="display: none"></canvas>
    <div class="actions">
      <button class="primary" :disabled="busy" @click="openCamera">
        {{ busy ? 'Processing…' : 'Open camera' }}
      </button>
      <button class="ghost" @click="$emit('cancel')">Cancel</button>
    </div>
  </div>
</template>

<style scoped>
.selfie-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}

.hidden-input {
  display: none;
}
</style>
