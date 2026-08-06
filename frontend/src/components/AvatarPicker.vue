<script setup>
import { computed, ref } from 'vue'
import { AVATAR_GALLERY } from '../avatars'
import { usePlayersStore } from '../stores/players'
import SelfieCapture from './SelfieCapture.vue'

const props = defineProps({
  player: { type: Object, required: true },
})

const store = usePlayersStore()
const open = ref(false)
const capturing = ref(false)
const error = ref(null)

const isSelfie = computed(() => props.player.avatar.startsWith('/api/players/'))

async function choose(avatar) {
  error.value = null
  try {
    await store.setAvatar(props.player.id, avatar)
    open.value = false
  } catch (err) {
    error.value = err.message
  }
}

async function onCaptured(blob) {
  capturing.value = false
  error.value = null
  try {
    await store.uploadSelfie(props.player.id, blob)
    open.value = false
  } catch (err) {
    error.value = err.message
  }
}

async function removeSelfie() {
  error.value = null
  try {
    await store.clearSelfie(props.player.id)
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <div class="avatar-picker">
    <button class="avatar-button" @click="open = !open">
      <img :src="player.avatar" :alt="player.name" />
    </button>

    <div v-if="open" class="picker-panel card">
      <SelfieCapture v-if="capturing" @captured="onCaptured" @cancel="capturing = false" />
      <template v-else>
        <div class="gallery">
          <button
            v-for="a in AVATAR_GALLERY"
            :key="a"
            class="gallery-item"
            :class="{ active: a === player.avatar }"
            @click="choose(a)"
          >
            <img :src="a" :alt="a" />
          </button>
        </div>
        <div class="actions">
          <button class="ghost" @click="capturing = true">Take selfie</button>
          <button v-if="isSelfie" class="ghost" @click="removeSelfie">Remove selfie</button>
          <button class="ghost" @click="open = false">Close</button>
        </div>
      </template>
      <p v-if="error" class="status error">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.avatar-picker {
  position: relative;
}

.avatar-button {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  padding: 0;
  border: 2px solid var(--border);
  background: var(--panel-2);
  cursor: pointer;
  overflow: hidden;
  flex-shrink: 0;
}

.avatar-button img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.picker-panel {
  position: absolute;
  z-index: 10;
  top: 72px;
  left: 0;
  width: 280px;
  max-width: 90vw;
}

.gallery {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.4rem;
}

.gallery-item {
  padding: 0;
  border: 2px solid transparent;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  overflow: hidden;
}

.gallery-item.active {
  border-color: var(--accent);
}

.gallery-item img {
  width: 100%;
  aspect-ratio: 1 / 1;
  display: block;
}

.picker-panel .actions {
  margin-top: 0.75rem;
  flex-wrap: wrap;
}
</style>
