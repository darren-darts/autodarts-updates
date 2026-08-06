<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const SLOT_COUNT = 3

const devices = ref([])
const slots = ref(Array(SLOT_COUNT).fill(null)) // device ids or null
const loading = ref(true)
const status = ref(null) // { kind: 'ok' | 'error', text }

const duplicates = computed(() => {
  const chosen = slots.value.filter((id) => id !== null)
  return new Set(chosen.filter((id, i) => chosen.indexOf(id) !== i))
})

const canSave = computed(() => duplicates.value.size === 0 && !loading.value)

function deviceName(id) {
  return devices.value.find((d) => d.id === id)?.name ?? `Camera ${id}`
}

async function refresh() {
  loading.value = true
  status.value = null
  try {
    const [{ devices: found }, settings] = await Promise.all([
      api.getCameras(),
      api.getSettings(),
    ])
    devices.value = found
    slots.value = settings.cameras.slots.map((slot) => {
      if (slot === null) return null
      // Drop stored selections whose device is no longer attached
      return found.some((d) => d.id === slot.device_id) ? slot.device_id : null
    })
    if (found.length === 0) {
      status.value = { kind: 'error', text: 'No camera devices found. Plug in the USB cameras and press Rescan.' }
    }
  } catch (err) {
    status.value = { kind: 'error', text: `Failed to load: ${err.message}` }
  } finally {
    loading.value = false
  }
}

async function save() {
  status.value = null
  try {
    await api.saveCameraSlots(
      slots.value.map((id) =>
        id === null ? null : { device_id: id, name: deviceName(id) },
      ),
    )
    status.value = { kind: 'ok', text: 'Camera selection saved.' }
  } catch (err) {
    status.value = { kind: 'error', text: `Save failed: ${err.message}` }
  }
}

onMounted(refresh)
</script>

<template>
  <div>
    <h1>Camera setup</h1>
    <p class="muted">
      Assign a USB camera to each of the three positions around the board.
      The live preview opens the camera on demand and releases it when you
      leave this page.
    </p>

    <div class="camera-grid">
      <div v-for="(slot, i) in slots" :key="i" class="card camera-slot">
        <h3>Camera {{ i + 1 }}</h3>
        <select v-model="slots[i]">
          <option :value="null">— not assigned —</option>
          <option v-for="d in devices" :key="d.id" :value="d.id">
            {{ d.name }}
          </option>
        </select>
        <p v-if="slot !== null && duplicates.has(slot)" class="status error">
          This device is used in another slot.
        </p>
        <div class="preview">
          <img
            v-if="slot !== null && !duplicates.has(slot)"
            :src="`/api/cameras/${slot}/stream`"
            :alt="`Camera ${i + 1} preview`"
          />
          <span v-else>no camera assigned</span>
        </div>
      </div>
    </div>

    <div class="actions">
      <button class="primary" :disabled="!canSave" @click="save">Save selection</button>
      <button class="ghost" @click="refresh">Rescan devices</button>
      <span v-if="loading" class="status muted">Scanning devices…</span>
      <span v-else-if="status" class="status" :class="status.kind">{{ status.text }}</span>
    </div>
  </div>
</template>
