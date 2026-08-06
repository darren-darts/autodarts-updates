<script setup>
import { onMounted, ref } from 'vue'
import { usePlayersStore } from '../stores/players'
import AvatarPicker from './AvatarPicker.vue'

const store = usePlayersStore()
const editingId = ref(null)
const draftName = ref('')
const busy = ref(false)
const error = ref(null)

function startEdit(player) {
  editingId.value = player.id
  draftName.value = player.name
}

async function commitEdit(player) {
  const name = draftName.value.trim()
  editingId.value = null
  if (!name || name === player.name) return
  try {
    await store.renamePlayer(player.id, name)
  } catch (err) {
    error.value = err.message
  }
}

async function add() {
  busy.value = true
  error.value = null
  try {
    await store.addPlayer()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function remove(id) {
  error.value = null
  try {
    await store.removePlayer(id)
  } catch (err) {
    error.value = err.message
  }
}

onMounted(() => {
  store.fetch()
  store.connect()
})
</script>

<template>
  <div class="player-roster">
    <p v-if="error" class="status error">{{ error }}</p>

    <div class="player-list">
      <div v-for="p in store.players" :key="p.id" class="player-row card">
        <AvatarPicker :player="p" />
        <input
          v-if="editingId === p.id"
          v-model="draftName"
          class="name-input"
          maxlength="24"
          @keyup.enter="commitEdit(p)"
          @blur="commitEdit(p)"
        />
        <button v-else class="name-button" @click="startEdit(p)">{{ p.name }}</button>
        <button
          class="ghost remove-button"
          :disabled="store.players.length <= store.minPlayers"
          :title="
            store.players.length <= store.minPlayers
              ? 'At least one player is required'
              : 'Remove player'
          "
          @click="remove(p.id)"
        >
          ✕
        </button>
      </div>
    </div>

    <button class="primary" :disabled="busy || store.players.length >= store.maxPlayers" @click="add">
      + Add player
    </button>
    <p v-if="store.players.length >= store.maxPlayers" class="muted small">
      Maximum {{ store.maxPlayers }} players.
    </p>
  </div>
</template>

<style scoped>
.player-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  margin-bottom: 1rem;
}

.player-row {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.6rem 1rem;
}

.name-button {
  flex: 1;
  text-align: left;
  background: none;
  border: none;
  color: var(--text);
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0.4rem 0;
}

.name-input {
  flex: 1;
  font-size: 1.1rem;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--text);
}

.remove-button {
  padding: 0.4rem 0.7rem;
  flex-shrink: 0;
}

.small {
  font-size: 0.85rem;
}
</style>
