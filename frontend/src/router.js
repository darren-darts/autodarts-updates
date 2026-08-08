import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import LedsView from './views/LedsView.vue'
import PlayersView from './views/PlayersView.vue'
import JoinView from './views/JoinView.vue'
import GamesView from './views/GamesView.vue'
import PlayView from './views/PlayView.vue'
import UpdatesView from './views/UpdatesView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    { path: '/games', component: GamesView },
    { path: '/play', component: PlayView },
    { path: '/players', component: PlayersView },
    { path: '/leds', component: LedsView },
    { path: '/updates', component: UpdatesView },
    { path: '/join', component: JoinView, meta: { phone: true } },
  ],
})
