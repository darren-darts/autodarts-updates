import { defineStore } from 'pinia'
import { api } from '../api'

export const usePlayersStore = defineStore('players', {
  state: () => ({
    players: [],
    minPlayers: 1,
    maxPlayers: 8,
    loaded: false,
    error: null,
    ws: null,
  }),
  actions: {
    async fetch() {
      try {
        const data = await api.getPlayers()
        this.players = data.players
        this.minPlayers = data.min_players
        this.maxPlayers = data.max_players
        this.loaded = true
        this.error = null
      } catch (err) {
        this.error = err.message
      }
    },

    // Live sync: any client (main screen or a phone) that changes the
    // roster broadcasts over /ws, so every other connected client updates
    // immediately without polling.
    connect() {
      if (this.ws) return
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${window.location.host}/ws`)
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data)
          if (msg.type === 'players.updated') this.players = msg.players
        } catch {
          // ignore malformed frames
        }
      }
      ws.onclose = () => {
        this.ws = null
        setTimeout(() => this.connect(), 2000)
      }
      this.ws = ws
    },

    async addPlayer() {
      const data = await api.addPlayer()
      this.players = data.players
    },
    async removePlayer(id) {
      const data = await api.removePlayer(id)
      this.players = data.players
    },
    async renamePlayer(id, name) {
      const data = await api.updatePlayer(id, { name })
      this.players = data.players
    },
    async setAvatar(id, avatar) {
      const data = await api.updatePlayer(id, { avatar })
      this.players = data.players
    },
    async uploadSelfie(id, blob) {
      const data = await api.uploadSelfie(id, blob)
      this.players = data.players
    },
    async clearSelfie(id) {
      const data = await api.clearSelfie(id)
      this.players = data.players
    },
  },
})
