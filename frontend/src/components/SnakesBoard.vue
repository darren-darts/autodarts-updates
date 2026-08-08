<script setup>
// The Snakes & Ladders board: a 10x10 boustrophedon grid (1 bottom-left, 100
// top-left), the snakes and ladders drawn over it, and one avatar token per
// player.
//
// Movement is animated per dart, not snapped. The backend resolves one dart at
// a time and hands over a `last_move` describing it - where the token started,
// where the dart landed it, and where any ladder or snake then carried it, each
// stamped with a monotonic `seq`. This component watches that seq and plays the
// move out: it walks the token square by square to the landing square, shows a
// LADDER / SNAKE / TOO HIGH / WINNER banner, then climbs or slides to the final
// square. Moves queue, so three quick darts animate in order. Anything that is
// not the next expected seq (a new game, an undo, a rewind) snaps instantly
// instead of animating a nonsensical path.
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  players: { type: Array, default: () => [] },      // [{ player_id, name, avatar, accent, score }]
  positions: { type: Object, default: () => ({}) }, // { player_id: square } (authoritative)
  ladders: { type: Object, default: () => ({}) },   // { foot: top }
  snakes: { type: Object, default: () => ({}) },    // { head: tail }
  finish: { type: Number, default: 100 },
  columns: { type: Number, default: 10 },
  currentPlayerId: { type: String, default: null },
  winnerId: { type: String, default: null },
  lastMove: { type: Object, default: null },
  dartNumber: { type: Number, default: 1 },
  dartsPerTurn: { type: Number, default: 3 },
})

const SNAKE_COLORS = ['#e0554f', '#f0872f', '#d1495b', '#e2662c', '#c74b52']

// Animation timing. Kept here so the feel is easy to tune in one place.
const STEP_MS = 150        // pause on each square while walking
const STEP_DUR = '0.13s'   // token transition while walking square to square
const SLIDE_DUR = '0.6s'   // token transition while climbing a ladder / sliding a snake

const cols = computed(() => props.columns || 10)
const rows = computed(() => Math.ceil((props.finish || 100) / cols.value))
const stepX = computed(() => 100 / cols.value)
const stepY = computed(() => 100 / rows.value)

const ladderTops = computed(() => new Set(Object.values(props.ladders).map(Number)))
const snakeTails = computed(() => new Set(Object.values(props.snakes).map(Number)))

// Centre of a square, in board percentages. Squares snake back and forth: even
// rows (counting from the bottom) run left-to-right, odd rows right-to-left.
function center(n) {
  const idx = n - 1
  const rowFromBottom = Math.floor(idx / cols.value)
  const colInRow = idx % cols.value
  const col = rowFromBottom % 2 === 0 ? colInRow : cols.value - 1 - colInRow
  const rowFromTop = rows.value - 1 - rowFromBottom
  return { x: (col + 0.5) * stepX.value, y: (rowFromTop + 0.5) * stepY.value }
}

// Cells in visual order: top row first, left to right.
const cells = computed(() => {
  const out = []
  for (let rowFromTop = 0; rowFromTop < rows.value; rowFromTop++) {
    const rowFromBottom = rows.value - 1 - rowFromTop
    for (let col = 0; col < cols.value; col++) {
      const colInRow = rowFromBottom % 2 === 0 ? col : cols.value - 1 - col
      const n = rowFromBottom * cols.value + colInRow + 1
      out.push({
        n,
        dark: (rowFromTop + col) % 2 === 0,
        ladderFoot: n in props.ladders,
        ladderTop: ladderTops.value.has(n),
        snakeHead: n in props.snakes,
        snakeTail: snakeTails.value.has(n),
        finish: n === props.finish,
      })
    }
  }
  return out
})

// A ladder as two rails plus rungs.
const ladderShapes = computed(() =>
  Object.entries(props.ladders).map(([foot, top]) => {
    const a = center(Number(foot))
    const b = center(Number(top))
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len = Math.hypot(dx, dy) || 1
    const px = (-dy / len) * 1.7   // perpendicular, half the rail spacing
    const py = (dx / len) * 1.7
    const rungCount = Math.max(2, Math.round(len / 3.4))
    const rungs = []
    for (let i = 1; i < rungCount; i++) {
      const t = i / rungCount
      const cx = a.x + dx * t
      const cy = a.y + dy * t
      rungs.push({ x1: cx + px, y1: cy + py, x2: cx - px, y2: cy - py })
    }
    return {
      key: `${foot}-${top}`,
      rail1: { x1: a.x + px, y1: a.y + py, x2: b.x + px, y2: b.y + py },
      rail2: { x1: a.x - px, y1: a.y - py, x2: b.x - px, y2: b.y - py },
      rungs,
    }
  }),
)

// A snake as a wiggling body from head to tail, with a head blob.
const snakeShapes = computed(() =>
  Object.entries(props.snakes).map(([head, tail], i) => {
    const a = center(Number(head))
    const b = center(Number(tail))
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len = Math.hypot(dx, dy) || 1
    const px = -dy / len
    const py = dx / len
    const amp = Math.min(6, len * 0.2)
    const c1x = a.x + dx * 0.33 + px * amp
    const c1y = a.y + dy * 0.33 + py * amp
    const c2x = a.x + dx * 0.66 - px * amp
    const c2y = a.y + dy * 0.66 - py * amp
    return {
      key: `${head}-${tail}`,
      color: SNAKE_COLORS[i % SNAKE_COLORS.length],
      d: `M ${a.x} ${a.y} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${b.x} ${b.y}`,
      head: a,
    }
  }),
)

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

// ------------------------------------------------------------ animation state

// What the board is *showing*, which lags the authoritative positions while a
// move plays out. Driven entirely by the last_move queue below.
const displayPos = reactive({})
const banner = ref(null)          // { icon, title, sub, tone } while a move plays
const activeId = ref(null)        // the token currently moving (drawn on top)
const moveDur = ref(STEP_DUR)     // token transition-duration, swapped mid-move

const currentName = computed(
  () => props.players.find((p) => p.player_id === props.currentPlayerId)?.name ?? '',
)
const currentAccent = computed(
  () => props.players.find((p) => p.player_id === props.currentPlayerId)?.accent ?? '#4f9bff',
)

function syncAll() {
  for (const p of props.players) displayPos[p.player_id] = props.positions?.[p.player_id] ?? 0
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

let lastSeq = 0
let queue = []
let running = false

async function runMove(mv) {
  const pid = mv.player_id
  activeId.value = pid
  displayPos[pid] = mv.from

  if (mv.type === 'miss') {
    banner.value = { icon: '🎯', title: mv.dart_label, sub: 'NO MOVE', tone: 'idle' }
    await wait(650)
  } else if (mv.type === 'overshoot') {
    banner.value = { icon: '🚫', title: 'TOO HIGH!', sub: `NEED EXACTLY ${mv.need}`, tone: 'warn' }
    await wait(1150)
  } else {
    // 1) walk square by square to where the dart landed
    banner.value = { icon: '🎯', title: mv.dart_label, sub: `MOVE ${mv.spaces}`, tone: 'move' }
    moveDur.value = STEP_DUR
    await wait(420)
    for (let s = mv.from + 1; s <= mv.landed; s++) {
      displayPos[pid] = s
      await wait(STEP_MS)
    }
    await wait(220)

    // 2) take any ladder or snake off the landing square
    if (mv.type === 'ladder' || (mv.type === 'win' && mv.to !== mv.landed)) {
      banner.value = { icon: '🪜', title: 'LADDER!', sub: `CLIMB TO ${mv.to}`, tone: 'good' }
      await wait(600)
      moveDur.value = SLIDE_DUR
      displayPos[pid] = mv.to
      await wait(700)
    } else if (mv.type === 'snake') {
      banner.value = { icon: '🐍', title: 'OH NO! SNAKE!', sub: `SLIDE TO ${mv.to}`, tone: 'bad' }
      await wait(600)
      moveDur.value = SLIDE_DUR
      displayPos[pid] = mv.to
      await wait(700)
    }

    // 3) crown the winner
    if (mv.type === 'win') {
      moveDur.value = SLIDE_DUR
      displayPos[pid] = mv.to
      banner.value = { icon: '🏆', title: 'WINNER!', sub: `SQUARE ${mv.to}`, tone: 'good' }
      await wait(1500)
    }
  }

  displayPos[pid] = mv.to
  banner.value = null
  moveDur.value = STEP_DUR
  activeId.value = null
}

async function drain() {
  if (running) return
  running = true
  while (queue.length) await runMove(queue.shift())
  running = false
}

watch(
  () => props.lastMove?.seq,
  (seq) => {
    if (seq == null) { lastSeq = 0; syncAll(); return }
    // Only the very next dart animates. A new game, an undo or a rewind lands on
    // some other seq - snap straight to the truth rather than animate a path
    // that never happened.
    if (seq !== lastSeq + 1) {
      lastSeq = seq
      queue = []
      banner.value = null
      activeId.value = null
      syncAll()
      return
    }
    lastSeq = seq
    queue.push({ ...props.lastMove })
    drain()
  },
  { immediate: true },
)

const tokens = computed(() => {
  const seen = {}
  return props.players.map((p) => {
    const sq = displayPos[p.player_id] ?? 0
    const idx = seen[sq] ?? 0
    seen[sq] = idx + 1
    const base = sq <= 0 ? { x: 5, y: 97 } : center(sq)
    const x = sq <= 0 ? base.x + idx * 6 : base.x + idx * 2.6
    const y = sq <= 0 ? base.y : base.y - idx * 2.6
    return {
      id: p.player_id,
      name: p.name,
      avatar: p.avatar,
      initial: (p.name || 'P').trim().charAt(0).toUpperCase() || 'P',
      accent: p.accent || '#4f84ff',
      square: sq,
      active: p.player_id === activeId.value,
      current: p.player_id === props.currentPlayerId,
      winner: p.player_id === props.winnerId,
      x: clamp(x, 3, 97),
      y: clamp(y, 3, 97),
    }
  })
})
</script>

<template>
  <div class="snl-board">
    <div v-if="currentName" class="snl-turn-ribbon">
      <b :style="{ color: currentAccent }">{{ currentName }}</b>
      <span>DART {{ dartNumber }} / {{ dartsPerTurn }}</span>
    </div>

    <div class="snl-grid">
      <div
        v-for="c in cells"
        :key="c.n"
        class="snl-cell"
        :class="{
          dark: c.dark,
          'is-ladder': c.ladderFoot || c.ladderTop,
          'is-snake': c.snakeHead || c.snakeTail,
          'is-finish': c.finish,
        }"
      >
        <span class="snl-num">{{ c.n }}</span>
        <span v-if="c.ladderFoot" class="snl-tag up">▲{{ ladders[c.n] }}</span>
        <span v-else-if="c.snakeHead" class="snl-tag down">▼{{ snakes[c.n] }}</span>
      </div>
    </div>

    <!-- snakes and ladders drawn over the grid -->
    <svg class="snl-overlay" viewBox="0 0 100 100" aria-hidden="true">
      <g class="snl-ladders">
        <g v-for="l in ladderShapes" :key="l.key">
          <line :x1="l.rail1.x1" :y1="l.rail1.y1" :x2="l.rail1.x2" :y2="l.rail1.y2" />
          <line :x1="l.rail2.x1" :y1="l.rail2.y1" :x2="l.rail2.x2" :y2="l.rail2.y2" />
          <line v-for="(r, i) in l.rungs" :key="i" class="rung" :x1="r.x1" :y1="r.y1" :x2="r.x2" :y2="r.y2" />
        </g>
      </g>
      <g class="snl-snakes">
        <g v-for="s in snakeShapes" :key="s.key">
          <path :d="s.d" :stroke="s.color" />
          <circle :cx="s.head.x" :cy="s.head.y" r="2.4" :fill="s.color" />
          <circle :cx="s.head.x - 0.8" :cy="s.head.y - 0.6" r="0.5" fill="#0b0f16" />
          <circle :cx="s.head.x + 0.8" :cy="s.head.y - 0.6" r="0.5" fill="#0b0f16" />
        </g>
      </g>
    </svg>

    <!-- player tokens -->
    <div
      v-for="t in tokens"
      :key="t.id"
      class="snl-token"
      :class="{ current: t.current, winner: t.winner, active: t.active, start: t.square <= 0 }"
      :style="{ left: `${t.x}%`, top: `${t.y}%`, '--tok': t.accent, transitionDuration: moveDur }"
      :title="`${t.name} — square ${t.square}`"
    >
      <img v-if="t.avatar" :src="t.avatar" alt="" />
      <b v-else>{{ t.initial }}</b>
    </div>

    <!-- per-dart feedback: what the dart did, and any snake/ladder reaction -->
    <transition name="snl-pop">
      <div v-if="banner" class="snl-banner" :class="banner.tone">
        <span class="snl-banner-icon">{{ banner.icon }}</span>
        <strong>{{ banner.title }}</strong>
        <small>{{ banner.sub }}</small>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.snl-board {
  position: relative;
  width: 100%;
  max-width: min(100%, 86vh);
  margin: 0 auto;
  aspect-ratio: 1;
}

.snl-turn-ribbon {
  position: absolute;
  top: -14px;
  left: 50%;
  z-index: 6;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 14px;
  border: 1px solid rgba(140, 234, 251, 0.5);
  border-radius: 999px;
  background: rgba(9, 16, 30, 0.94);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.5);
  white-space: nowrap;
}

.snl-turn-ribbon b { font-size: 13px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.04em; }
.snl-turn-ribbon span { color: #8ceafb; font-size: 9px; font-weight: 900; letter-spacing: 0.14em; }

.snl-grid {
  position: absolute;
  inset: 0;
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  grid-template-rows: repeat(10, 1fr);
  border: 3px solid #7a5230;
  border-radius: 8px;
  overflow: hidden;
  background: #e9d3a4;
}

.snl-cell {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  padding: 3px 4px;
  border-right: 1px solid rgba(122, 82, 48, 0.22);
  border-bottom: 1px solid rgba(122, 82, 48, 0.22);
  background: #f0dcb0;
}

/* checkerboard: darker squares by (row + column) parity */
.snl-cell.dark { background: #e6cd9a; }

.snl-cell.is-ladder { background: rgba(79, 155, 232, 0.28); }
.snl-cell.is-snake { background: rgba(224, 85, 79, 0.26); }
.snl-cell.is-finish {
  background: linear-gradient(135deg, #4caf50, #2e7d32);
  color: #fff;
}

.snl-num {
  color: #3a2a17;
  font-size: clamp(7px, 1.4vw, 12px);
  font-weight: 800;
  line-height: 1;
}

.is-finish .snl-num { color: #fff; }

.snl-tag {
  position: absolute;
  right: 2px;
  bottom: 1px;
  font-size: clamp(5px, 0.95vw, 8px);
  font-weight: 900;
  letter-spacing: -0.02em;
}

.snl-tag.up { color: #205fa6; }
.snl-tag.down { color: #a3302b; }

.snl-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.snl-ladders line {
  stroke: #4f9bff;
  stroke-width: 0.7;
  stroke-linecap: round;
  opacity: 0.9;
}

.snl-ladders line.rung {
  stroke: #8fc3ff;
  stroke-width: 0.55;
}

.snl-snakes path {
  fill: none;
  stroke-width: 2.4;
  stroke-linecap: round;
  opacity: 0.92;
}

.snl-token {
  position: absolute;
  z-index: 3;
  width: 8.4%;
  height: 8.4%;
  display: grid;
  place-items: center;
  overflow: hidden;
  border-radius: 50%;
  background: #1a2438;
  border: 2.5px solid var(--tok);
  box-shadow: 0 0 0 1.5px rgba(255, 255, 255, 0.85), 0 3px 7px rgba(0, 0, 0, 0.55);
  transform: translate(-50%, -50%);
  transition-property: left, top;
  transition-timing-function: cubic-bezier(0.34, 1.2, 0.5, 1);
}

.snl-token img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.snl-token b {
  color: #fff;
  font-size: clamp(8px, 1.5vw, 14px);
  font-weight: 900;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
}

.snl-token.current {
  z-index: 5;
  box-shadow: 0 0 0 2px #fff, 0 0 0 4px var(--tok), 0 0 16px color-mix(in srgb, var(--tok) 70%, transparent);
}

.snl-token.active {
  z-index: 7;
  transform: translate(-50%, -50%) scale(1.14);
}

.snl-token.winner {
  border-color: #ffd54a;
  box-shadow: 0 0 0 2px #fff, 0 0 0 4px #ffd54a, 0 0 20px rgba(255, 213, 74, 0.75);
}

.snl-token.start {
  opacity: 0.92;
}

/* per-dart feedback banner, centred over the board */
.snl-banner {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 8;
  transform: translate(-50%, -50%);
  min-width: 42%;
  padding: 14px 22px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  border-radius: 16px;
  border: 2px solid rgba(255, 255, 255, 0.25);
  background: rgba(8, 14, 26, 0.9);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.55);
  text-align: center;
  pointer-events: none;
}

.snl-banner-icon { font-size: clamp(26px, 5vw, 44px); line-height: 1; }
.snl-banner strong { font-size: clamp(18px, 3.4vw, 30px); font-weight: 950; letter-spacing: 0.02em; }
.snl-banner small { font-size: clamp(10px, 1.7vw, 15px); font-weight: 900; letter-spacing: 0.12em; color: #cfe4ff; }

.snl-banner.move { border-color: rgba(140, 234, 251, 0.6); }
.snl-banner.move strong { color: #cfe4ff; }
.snl-banner.good { border-color: rgba(111, 232, 135, 0.75); background: rgba(11, 34, 20, 0.92); }
.snl-banner.good strong { color: #9cf6ad; }
.snl-banner.bad { border-color: rgba(224, 85, 79, 0.8); background: rgba(40, 12, 12, 0.92); }
.snl-banner.bad strong { color: #ff8c86; }
.snl-banner.warn { border-color: rgba(255, 191, 77, 0.8); background: rgba(40, 28, 8, 0.92); }
.snl-banner.warn strong { color: #ffd27a; }
.snl-banner.idle strong { color: #cbd8ef; }

.snl-pop-enter-active { transition: transform 0.22s cubic-bezier(0.34, 1.56, 0.5, 1), opacity 0.22s ease; }
.snl-pop-leave-active { transition: transform 0.18s ease, opacity 0.18s ease; }
.snl-pop-enter-from { transform: translate(-50%, -50%) scale(0.6); opacity: 0; }
.snl-pop-leave-to { transform: translate(-50%, -50%) scale(0.9); opacity: 0; }
</style>
