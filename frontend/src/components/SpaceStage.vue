<script setup>
// The Space Invaders orbital playfield: the live board in the centre, the
// alien fleet orbiting it in three rows, and an aiming cannon that visibly
// fires lasers down the hit lanes - arcade style - whenever a dart lands.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import DartboardFace from './DartboardFace.vue'

const props = defineProps({
  view: { type: Object, required: true },     // the invaders game view
  darts: { type: Array, default: () => [] },  // this turn's darts
  geometry: { type: Object, default: null },
})

const SEGMENTS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

const animatingAttack = ref(false)
const attackForAnimation = ref(null)
const banner = ref(null)
const advancingIds = ref(new Set())
let attackTimer = null
let advanceTimer = null

function laneAngleDeg(lane) {
  const index = SEGMENTS.indexOf(Number(lane))
  return Math.max(0, index) * 18
}

function lanePoint(lane, radius) {
  const angle = (laneAngleDeg(lane) * Math.PI) / 180
  return [500 + Math.sin(angle) * radius, 500 - Math.cos(angle) * radius]
}

function alienRadius(row, advance = 0) {
  return ({ 1: 385, 2: 438, 3: 472 }[row] || 472) - advance * 22
}

const aliens = computed(() => props.view.aliens || [])

const attack = computed(() => props.view.last_attack || null)

// A new attack id triggers the firing animation once; destroyed aliens stay
// on screen just long enough to explode.
watch(
  () => attack.value?.id,
  (id, previous) => {
    if (!id || id === previous) return
    attackForAnimation.value = attack.value
    animatingAttack.value = true
    const type = String(attack.value.type || 'normal')
    banner.value =
      type === 'charge' ? 'MULTI-CANNON CHARGED'
      : type === 'miss' ? null
      : type === 'bull_barrage' ? `BULL BARRAGE · ALL LANES${attack.value.points ? ` · +${attack.value.points} PTS` : ''}`
      : type === 'multi_cannon' ? `MULTI-CANNON · ${(attack.value.lanes || []).join(' · ')}${attack.value.points ? ` · +${attack.value.points} PTS` : ''}`
      : `LASER STRIKE · LANE ${attack.value.origin_lane ?? '—'}${attack.value.points ? ` · +${attack.value.points} PTS` : ''}`
    clearTimeout(attackTimer)
    attackTimer = setTimeout(() => {
      animatingAttack.value = false
      attackForAnimation.value = null
      banner.value = null
    }, type === 'bull_barrage' ? 1100 : 800)
  },
)

watch(
  () => (props.view.last_advance || []).map((a) => `${a.id}:${a.to}`).join('|'),
  (signature, previous) => {
    if (!signature || signature === previous) return
    advancingIds.value = new Set((props.view.last_advance || []).map((a) => a.id))
    clearTimeout(advanceTimer)
    advanceTimer = setTimeout(() => { advancingIds.value = new Set() }, 800)
  },
)

onBeforeUnmount(() => {
  clearTimeout(attackTimer)
  clearTimeout(advanceTimer)
})

const attackLanes = computed(() =>
  animatingAttack.value ? (attackForAnimation.value?.lanes || []).filter((l) => SEGMENTS.includes(Number(l))) : [],
)

const destroyedIds = computed(() =>
  new Set(animatingAttack.value ? attackForAnimation.value?.destroyed_ids || [] : []),
)
const damagedIds = computed(() =>
  new Set(animatingAttack.value ? attackForAnimation.value?.damaged_ids || [] : []),
)

// Alive aliens always; just-destroyed ones only while their explosion plays.
const shownAliens = computed(() =>
  aliens.value
    .filter((a) => a.alive || destroyedIds.value.has(a.id))
    .map((a) => {
      const row = Math.max(1, Math.min(3, Number(a.row || 1)))
      const advance = Math.max(0, Number(a.advance || 0))
      const [x, y] = lanePoint(a.lane, alienRadius(row, advance))
      return {
        ...a,
        row,
        x,
        y,
        hit: damagedIds.value.has(a.id) || destroyedIds.value.has(a.id),
        exploding: destroyedIds.value.has(a.id),
        advancing: advancingIds.value.has(a.id),
      }
    }),
)

const explosions = computed(() =>
  shownAliens.value.filter((a) => a.exploding).map((a) => ({ id: a.id, x: a.x, y: a.y })),
)

const lasers = computed(() =>
  attackLanes.value.map((lane, index) => {
    const [x1, y1] = lanePoint(lane, 234)
    const [x2, y2] = lanePoint(lane, 480)
    return { lane, x1, y1, x2, y2, delay: `${index * 22}ms` }
  }),
)

const showCoreBlast = computed(
  () => animatingAttack.value && String(attackForAnimation.value?.type || '').includes('bull'),
)

const dividers = computed(() =>
  SEGMENTS.map((lane, index) => {
    const edge = (((index * 18 + 9) % 360) * Math.PI) / 180
    const sin = Math.sin(edge)
    const cos = Math.cos(edge)
    return { lane, x1: 500 + sin * 200, y1: 500 - cos * 200, x2: 500 + sin * 490, y2: 500 - cos * 490 }
  }),
)

const cannon = computed(() => {
  const lane = attackLanes.value[0]
    ?? attackForAnimation.value?.origin_lane
    ?? attack.value?.origin_lane
    ?? SEGMENTS[0]
  const angle = (laneAngleDeg(lane) * Math.PI) / 180
  const radius = 286
  return {
    x: 500 + Math.sin(angle) * radius,
    y: 500 - Math.cos(angle) * radius,
    deg: laneAngleDeg(lane),
    firing: animatingAttack.value && attackLanes.value.length > 0,
    active: (props.darts?.length || 0) > 0,
  }
})

const bannerBarrage = computed(() => String(attackForAnimation.value?.type || '').includes('bull'))
</script>

<template>
  <div class="space-orbit-stage">
    <div class="space-core-glow" aria-hidden="true"></div>
    <div class="space-board">
      <DartboardFace
        v-if="geometry"
        :geometry="geometry"
        fluid
        theme="space"
        :highlight="attackLanes"
        :darts="darts"
      />
    </div>
    <!-- Padded viewBox, still centred on (500,500): the outer alien row sits at
         radius 472 and its body and points label reach past 500, which clipped
         against a plain 0 0 1000 1000 box. -->
    <svg class="space-alien-field" viewBox="-25 -25 1050 1050" aria-hidden="true">
      <line
        v-for="d in dividers"
        :key="`div${d.lane}`"
        class="space-lane-divider"
        :x1="d.x1" :y1="d.y1" :x2="d.x2" :y2="d.y2"
      />

      <g
        v-for="alien in shownAliens"
        :key="alien.id"
        class="space-alien"
        :class="[
          `space-alien--row-${alien.row}`,
          {
            'space-alien--tank': alien.type === 'tank',
            'space-alien--damaged': alien.hp < alien.max_hp && alien.alive,
            'space-alien--hit': alien.hit && !alien.exploding,
            'space-alien--destroyed': alien.exploding,
            'space-alien--advanced': alien.advancing,
          },
        ]"
        :transform="`translate(${alien.x.toFixed(1)} ${alien.y.toFixed(1)})`"
      >
        <circle class="space-alien-aura" r="35" />
        <g v-if="alien.type === 'tank'" class="space-alien-body">
          <path d="M-27 8h7v-20h9v-9h22v9h9V8h7v14H16v8H7v-8H-7v8h-9v-8h-11z" />
          <path class="space-alien-core" d="M-11-8h22V8h-22z" />
          <circle cx="-7" cy="0" r="3" /><circle cx="7" cy="0" r="3" />
        </g>
        <g v-else-if="alien.type === 'heavy'" class="space-alien-body">
          <path d="M0-28 10-17h13v9h8v23h-9v12H10V16h-20v11h-12V15h-9V-8h8v-9h13z" />
          <path class="space-alien-core" d="M-13-9h26v19h-26z" />
          <path d="M-24-4h9v9h-9zm39 0h9v9h-9z" />
          <circle cx="-7" cy="0" r="3" /><circle cx="7" cy="0" r="3" />
        </g>
        <g v-else-if="alien.type === 'fighter'" class="space-alien-body">
          <path d="M0-25 9-10l18 4-9 9 8 17-18-6-8 15-8-15-18 6 8-17-9-9 18-4z" />
          <path class="space-alien-core" d="M-10-7h20v15h-20z" />
          <circle cx="-5" cy="0" r="2.7" /><circle cx="5" cy="0" r="2.7" />
        </g>
        <g v-else class="space-alien-body">
          <path d="M-26-8h8v-9h9v-8H9v8h9v9h8v23h-8v10H8V15H-8v10h-10V15h-8z" />
          <path class="space-alien-core" d="M-11-7h22V9h-22z" />
          <circle cx="-6" cy="0" r="3" /><circle cx="6" cy="0" r="3" />
        </g>
        <g v-if="alien.max_hp > 1" class="space-alien-health" transform="translate(-18 34)">
          <rect
            v-for="i in alien.max_hp"
            :key="i"
            :x="(i - 1) * 20"
            width="16" height="4" rx="2"
            :class="{ charged: i <= alien.hp }"
          />
        </g>
        <text class="space-alien-points" y="-37">{{ alien.points }}</text>
      </g>

      <g v-if="animatingAttack" class="space-attack-effect">
        <circle v-if="showCoreBlast" class="space-cannon-core-blast" cx="500" cy="500" r="72" />
        <line
          v-for="laser in lasers"
          :key="`laser${laser.lane}`"
          class="space-laser"
          :style="{ '--laser-delay': laser.delay }"
          :x1="laser.x1" :y1="laser.y1" :x2="laser.x2" :y2="laser.y2"
        />
        <g v-for="boom in explosions" :key="`boom${boom.id}`" class="space-explosion" :transform="`translate(${boom.x} ${boom.y})`">
          <circle r="10" />
          <path d="M0-31 6-12 24-23 13-6 34 0 13 7 23 25 6 13 0 34-7 13-25 24-13 6-34 0-13-7-23-24-6-13z" />
        </g>
      </g>

      <g
        class="space-cannon"
        :class="{ 'space-cannon--fire': cannon.firing, 'space-cannon--active': cannon.active }"
        :transform="`translate(${cannon.x.toFixed(1)}, ${cannon.y.toFixed(1)}) rotate(${cannon.deg})`"
      >
        <ellipse class="space-cannon-shadow" cx="0" cy="15" rx="29" ry="8" />
        <path class="space-cannon-outrigger" d="M-31 18-25-7-15-12-11 19zm62 0L25-7 15-12 11 19z" />
        <path class="space-cannon-base" d="M-23 18-20-10-12-21H12l8 11 3 28-10 9h-26z" />
        <path class="space-cannon-armour" d="M-16 10-13-10-7-16H7l6 6 3 20-8 8H-8z" />
        <rect class="space-cannon-barrel" x="-7" y="-49" width="14" height="39" rx="4" />
        <rect class="space-cannon-barrel-core" x="-2.5" y="-47" width="5" height="34" rx="2" />
        <path class="space-cannon-sight" d="M-16-31h9v6h-5v9h-4zm32 0H7v6h5v9h4z" />
        <circle class="space-cannon-core-ring" cx="0" cy="3" r="11" />
        <circle class="space-cannon-core" cx="0" cy="3" r="6" />
        <circle class="space-cannon-core-hot" cx="0" cy="3" r="2.5" />
        <circle class="space-cannon-muzzle" cx="0" cy="-52" r="4" />
      </g>
    </svg>
    <div v-if="banner" class="space-attack-banner" :class="{ barrage: bannerBarrage }">{{ banner }}</div>
  </div>
</template>

<style scoped>
.space-orbit-stage {
  width: 100%;
  position: relative;
  aspect-ratio: 1;
}

.space-core-glow {
  width: 61%;
  aspect-ratio: 1;
  position: absolute;
  top: 50%;
  left: 50%;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(145, 247, 255, 0.26), rgba(58, 125, 255, 0.1) 48%, transparent 72%);
  box-shadow: 0 0 95px rgba(69, 228, 255, 0.22);
  transform: translate(-50%, -50%);
  animation: space-core-pulse 3.8s ease-in-out infinite;
}

.space-board {
  width: 63%;
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 3;
  transform: translate(-50%, -50%);
  filter: drop-shadow(0 24px 28px rgba(0, 0, 0, 0.82)) drop-shadow(0 0 22px rgba(69, 228, 255, 0.5));
}

.space-alien-field {
  width: 100%;
  height: 100%;
  position: absolute;
  inset: 0;
  z-index: 4;
  overflow: visible;
  pointer-events: none;
}

.space-lane-divider {
  stroke: rgba(69, 228, 255, 0.22);
  stroke-width: 1.5;
}

.space-alien { --alien-color: #b8ff36; color: var(--alien-color); }
.space-alien--row-2 { --alien-color: #45e4ff; }
.space-alien--row-3 { --alien-color: #ff3bbd; }
.space-alien--tank { --alien-color: #ff9f32; }

.space-alien-aura {
  fill: color-mix(in srgb, var(--alien-color) 14%, transparent);
  stroke: color-mix(in srgb, var(--alien-color) 35%, transparent);
  stroke-width: 1;
  opacity: 0.68;
}

.space-alien-body {
  fill: var(--alien-color);
  stroke: #dffcff;
  stroke-width: 1.5;
  filter: drop-shadow(0 0 7px color-mix(in srgb, var(--alien-color) 75%, transparent));
  animation: space-alien-hover 3.4s ease-in-out infinite;
}

.space-alien:nth-child(3n) .space-alien-body { animation-delay: -1.1s; }
.space-alien:nth-child(4n) .space-alien-body { animation-delay: -2.2s; }
.space-alien-core { fill: #07111e; stroke: none; }
.space-alien-body circle { fill: #f4feff; stroke: none; filter: drop-shadow(0 0 4px white); }

.space-alien-points {
  fill: var(--alien-color);
  font-size: 15px;
  font-weight: 950;
  text-anchor: middle;
  paint-order: stroke;
  stroke: #020711;
  stroke-width: 4px;
}

.space-alien-health rect { fill: #203448; }
.space-alien-health rect.charged { fill: #ff9f32; filter: drop-shadow(0 0 4px #ff9f32); }
.space-alien--damaged .space-alien-body { opacity: 0.74; filter: drop-shadow(0 0 9px #ff4f68); }
.space-alien--hit .space-alien-body { animation: space-alien-hit 0.42s ease-out both; }
.space-alien--advanced .space-alien-body { animation: space-alien-advance 0.72s cubic-bezier(0.2, 0.85, 0.25, 1) both; }
.space-alien--destroyed .space-alien-body,
.space-alien--destroyed .space-alien-aura { animation: space-alien-destroy 0.55s ease-out both; }

.space-cannon { filter: drop-shadow(0 0 8px rgba(69, 228, 255, 0.8)) drop-shadow(0 8px 6px rgba(0, 0, 0, 0.85)); }
.space-cannon-shadow { fill: rgba(0, 0, 0, 0.58); }
.space-cannon-outrigger { fill: #0b2840; stroke: #45d9ff; stroke-width: 2; }
.space-cannon-base { fill: #164e73; stroke: #a8f7ff; stroke-width: 2.4; }
.space-cannon-armour { fill: #2388aa; stroke: #d8fcff; stroke-width: 1.6; }
.space-cannon-barrel { fill: #45d9ff; stroke: #d9fcff; stroke-width: 2; transform-box: fill-box; transform-origin: center bottom; }
.space-cannon-barrel-core { fill: #efffff; filter: drop-shadow(0 0 5px #45d9ff); }
.space-cannon-sight { fill: #183c5b; stroke: #79edff; stroke-width: 1.5; }
.space-cannon-core-ring { fill: #092039; stroke: #79edff; stroke-width: 2; }
.space-cannon-core { fill: #9df4ff; filter: drop-shadow(0 0 7px #45e4ff); }
.space-cannon-core-hot { fill: #fff; filter: drop-shadow(0 0 5px white); }
.space-cannon-muzzle { fill: #fff; stroke: #45e4ff; stroke-width: 1.5; filter: drop-shadow(0 0 9px #b8ff36); }
.space-cannon--active .space-cannon-base,
.space-cannon--active .space-cannon-outrigger { fill: #477523; stroke: #b8ff36; }
.space-cannon--active .space-cannon-core { fill: #b8ff36; filter: drop-shadow(0 0 10px #b8ff36); }
.space-cannon--fire .space-cannon-barrel { animation: space-cannon-fire 0.4s ease-out both; }
.space-cannon--fire .space-cannon-muzzle { animation: space-cannon-flash 0.35s ease-out both; }

.space-laser {
  stroke: #9df4ff;
  stroke-width: 7;
  stroke-linecap: round;
  filter: drop-shadow(0 0 8px #45e4ff) drop-shadow(0 0 15px #3a7dff);
  stroke-dasharray: 280;
  stroke-dashoffset: 280;
  animation: space-laser-fire 0.54s cubic-bezier(0.2, 0.8, 0.2, 1) var(--laser-delay) both;
}

.space-cannon-core-blast {
  fill: none;
  stroke: #b8ff36;
  stroke-width: 8;
  filter: drop-shadow(0 0 16px #b8ff36);
  animation: space-core-blast 0.9s ease-out both;
}

.space-explosion circle {
  fill: #fff4b3;
  filter: drop-shadow(0 0 12px #ff9f32);
  animation: space-explosion-core 0.55s ease-out both;
}

.space-explosion path {
  fill: #ff9f32;
  stroke: #fff1b8;
  stroke-width: 2;
  animation: space-explosion-burst 0.62s ease-out both;
}

.space-attack-banner {
  max-width: 86%;
  padding: 8px 17px;
  position: absolute;
  top: 48%;
  left: 50%;
  z-index: 8;
  border: 1px solid #9df4ff;
  background: rgba(3, 15, 28, 0.93);
  color: white;
  font-size: 12px;
  font-weight: 950;
  letter-spacing: 0.1em;
  text-align: center;
  box-shadow: 0 0 25px rgba(69, 228, 255, 0.4);
  transform: translate(-50%, -50%);
  animation: space-banner-in 0.8s ease-out both;
}

.space-attack-banner.barrage {
  border-color: #b8ff36;
  color: #b8ff36;
  box-shadow: 0 0 35px rgba(184, 255, 54, 0.45);
}

@keyframes space-core-pulse { 50% { opacity: 0.6; transform: translate(-50%, -50%) scale(1.07); } }
@keyframes space-alien-hover { 50% { transform: translateY(-4px); } }
@keyframes space-alien-hit { 20% { fill: white; transform: translateX(-7px); } 45% { transform: translateX(6px); } 70% { transform: translateX(-3px); } }
@keyframes space-alien-advance { from { opacity: 0.35; transform: translateY(-18px) scale(0.72); } 55% { filter: brightness(1.8); } to { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes space-alien-destroy { 45% { opacity: 1; transform: scale(1.35); filter: brightness(2); } to { opacity: 0; transform: scale(0.2) rotate(28deg); } }
@keyframes space-cannon-fire { 0% { fill: #fff; transform: scaleY(1.22); } 100% { fill: #45d9ff; transform: scaleY(1); } }
@keyframes space-cannon-flash { 0% { r: 11; opacity: 1; } 100% { r: 4; opacity: 0.35; } }
@keyframes space-laser-fire { 0% { opacity: 0; stroke-dashoffset: 280; } 15%, 68% { opacity: 1; } 100% { opacity: 0; stroke-dashoffset: -280; } }
@keyframes space-core-blast { from { opacity: 1; transform-origin: center; transform: scale(0.2); } to { opacity: 0; transform-origin: center; transform: scale(4.8); } }
@keyframes space-explosion-core { to { opacity: 0; r: 42; } }
@keyframes space-explosion-burst { from { opacity: 1; transform: scale(0.25) rotate(0); } to { opacity: 0; transform: scale(1.55) rotate(35deg); } }
@keyframes space-banner-in { from { opacity: 0; transform: translate(-50%, -35%) scale(0.82); } 20%, 80% { opacity: 1; } to { opacity: 0; transform: translate(-50%, -65%) scale(1.04); } }

@media (prefers-reduced-motion: reduce) {
  .space-core-glow, .space-alien-body { animation: none !important; }
  .space-laser, .space-explosion circle, .space-explosion path, .space-cannon-core-blast, .space-attack-banner {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
