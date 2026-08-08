<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  players: { type: Array, default: () => [] },
  numbers: { type: Object, default: () => ({}) },
  track: { type: Number, default: 12 },
  currentPlayerId: { type: String, default: null },
  winnerId: { type: String, default: null },
  round: { type: Number, default: 1 },
})

const ACCENTS = ['#ff477e', '#ff9f1c', '#b967ff', '#2ec4e6', '#f4d35e', '#64df72', '#4f86ff', '#ff6b5f']
const motion = ref({})
const timers = new Map()

const leaderScore = computed(() => Math.max(0, ...props.players.map((player) => player.score ?? 0)))

function position(player) {
  const ratio = Math.max(0, Math.min(1, (player.score ?? 0) / Math.max(1, props.track)))
  return `${4 + ratio * 90}%`
}

function runnerStyle(player, index) {
  return {
    '--runner-color': ACCENTS[index % ACCENTS.length],
    '--runner-position': position(player),
  }
}

function isLeader(player) {
  return leaderScore.value > 0 && (player.score ?? 0) === leaderScore.value
}

watch(
  () => props.players.map((player) => [player.player_id, player.score ?? 0]),
  (next, previous = []) => {
    const before = Object.fromEntries(previous)
    for (const [playerId, score] of next) {
      if (!(playerId in before) || before[playerId] === score) continue
      const delta = score - before[playerId]
      motion.value = {
        ...motion.value,
        [playerId]: { direction: delta > 0 ? 'forward' : 'backward', delta },
      }
      clearTimeout(timers.get(playerId))
      timers.set(playerId, setTimeout(() => {
        const updated = { ...motion.value }
        delete updated[playerId]
        motion.value = updated
        timers.delete(playerId)
      }, 1400))
    }
  },
  { flush: 'post' },
)

onBeforeUnmount(() => {
  for (const timer of timers.values()) clearTimeout(timer)
})
</script>

<template>
  <section class="derby-race" :class="{ finished: winnerId }">
    <header class="race-header">
      <div class="race-brand">
        <span>SHEPDARTS RACING</span>
        <h2>DONKEY <i>DERBY</i></h2>
        <p>Hit yours to charge. Hit theirs to send them backwards.</p>
      </div>
      <div class="race-status">
        <span>RACE {{ round }}</span>
        <strong>{{ track }} FURLONGS</strong>
        <small>{{ winnerId ? 'WE HAVE A WINNER' : 'LIVE FROM THE OCHE' }}</small>
      </div>
    </header>

    <div class="race-bunting" aria-hidden="true">
      <i v-for="flag in 22" :key="flag"></i>
    </div>

    <div class="course">
      <div class="course-labels" aria-hidden="true">
        <span>START GATE</span>
        <b>THE HOME STRAIGHT</b>
        <span>FINISH</span>
      </div>

      <div class="finish-post" aria-hidden="true"><i></i><b>FINISH</b></div>

      <article
        v-for="(player, index) in players"
        :key="player.player_id"
        class="runner"
        :class="{
          current: player.player_id === currentPlayerId,
          leader: isLeader(player),
          winner: player.player_id === winnerId,
          forward: motion[player.player_id]?.direction === 'forward',
          backward: motion[player.player_id]?.direction === 'backward',
        }"
        :style="runnerStyle(player, index)"
        :aria-label="`${player.name}, target ${numbers[player.player_id]}, ${player.score ?? 0} of ${track} steps`"
      >
        <div class="runner-card">
          <span class="lane-number">{{ index + 1 }}</span>
          <img :src="player.avatar" alt="" />
          <div>
            <strong>{{ player.name }}</strong>
            <small v-if="player.player_id === winnerId">DERBY WINNER</small>
            <small v-else-if="player.player_id === currentPlayerId">AT THE OCHE</small>
            <small v-else-if="isLeader(player)">RACE LEADER</small>
            <small v-else>IN THE RUNNING</small>
          </div>
          <b class="target-number"><small>HIT</small>{{ numbers[player.player_id] }}</b>
        </div>

        <div class="lane">
          <div class="distance-fill" :style="{ width: position(player) }"></div>
          <span class="start-rail" aria-hidden="true"></span>
          <div class="donkey-marker">
            <span v-if="motion[player.player_id]" class="movement-callout">
              {{ motion[player.player_id].delta > 0 ? '+' : '' }}{{ motion[player.player_id].delta }}
            </span>
            <span v-if="player.player_id === winnerId" class="winner-rosette">1st</span>
            <svg viewBox="0 0 150 78" aria-hidden="true">
              <ellipse class="donkey-shadow" cx="75" cy="70" rx="48" ry="5" />
              <g class="donkey-body">
                <path class="donkey-tail" d="M31 38 Q13 28 16 13 Q20 22 28 22" />
                <ellipse class="donkey-coat" cx="72" cy="39" rx="42" ry="22" />
                <path class="donkey-coat" d="M101 35 Q112 18 123 22 L130 34 L111 45 Z" />
                <ellipse class="donkey-head" cx="126" cy="34" rx="18" ry="14" />
                <ellipse class="donkey-muzzle" cx="139" cy="40" rx="11" ry="8" />
                <path class="donkey-ear" d="M115 25 Q105 4 114 3 Q124 14 122 27 Z" />
                <path class="donkey-ear" d="M126 23 Q124 2 133 4 Q139 17 132 28 Z" />
                <circle class="donkey-eye" cx="131" cy="30" r="2.2" />
                <path class="donkey-leg leg-a" d="M48 51 L43 69 L51 69 L58 51" />
                <path class="donkey-leg leg-b" d="M76 53 L83 69 L91 69 L87 50" />
                <path class="donkey-leg leg-c" d="M92 50 L99 69 L107 69 L103 46" />
                <path class="saddle" d="M48 24 Q70 15 91 25 L84 45 L50 43 Z" />
                <path class="saddle-trim" d="M50 39 L86 40" />
                <text x="68" y="36">{{ numbers[player.player_id] }}</text>
              </g>
            </svg>
            <i class="dust dust-one"></i><i class="dust dust-two"></i><i class="dust dust-three"></i>
          </div>
        </div>

        <div class="runner-progress">
          <strong>{{ player.score ?? 0 }}</strong>
          <span>/ {{ track }}</span>
          <small>STEPS</small>
        </div>
      </article>
    </div>

    <footer class="race-footer">
      <span><b>SINGLE</b> +1 / -1</span>
      <span><b>DOUBLE</b> +2 / -2</span>
      <span><b>TREBLE</b> +3 / -3</span>
      <strong>FIRST PAST THE POST WINS</strong>
    </footer>
  </section>
</template>

<style scoped>
.derby-race {
  min-width: 0;
  min-height: 620px;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 2px solid #f7d56b;
  border-radius: 18px;
  background:
    radial-gradient(circle at 78% 8%, rgba(255, 245, 167, 0.86) 0 3%, transparent 3.2%),
    linear-gradient(#24659a 0 20%, #76bad8 20% 31%, #315f35 31% 100%);
  box-shadow: inset 0 0 0 4px rgba(76, 35, 7, 0.48), 0 24px 65px rgba(0, 0, 0, 0.48);
  color: #fffdf0;
}

.derby-race::before {
  position: absolute;
  inset: 23% 0 auto;
  height: 86px;
  content: '';
  opacity: 0.62;
  background:
    radial-gradient(circle at 2% 100%, #163520 0 24px, transparent 25px) 0 0 / 58px 58px repeat-x,
    linear-gradient(transparent 72%, rgba(15, 39, 22, 0.8) 73%);
}

.race-header {
  min-height: 105px;
  padding: 18px 23px 20px;
  z-index: 2;
  display: flex;
  justify-content: space-between;
  gap: 20px;
  border-bottom: 4px solid #f4d35e;
  background: linear-gradient(115deg, rgba(70, 20, 15, 0.96), rgba(137, 37, 25, 0.94) 50%, rgba(65, 18, 16, 0.97));
  box-shadow: 0 8px 24px rgba(32, 12, 4, 0.38);
}

.race-brand span { color: #ffd96a; font-size: 9px; font-weight: 950; letter-spacing: 0.24em; }
.race-brand h2 { margin: 1px 0 2px; font-size: clamp(29px, 3.1vw, 49px); font-style: italic; font-weight: 1000; line-height: 0.9; letter-spacing: -0.05em; text-shadow: 0 4px 0 #3b0b0b, 0 0 22px rgba(255, 208, 81, 0.25); }
.race-brand h2 i { color: #ffd44f; font-style: inherit; }
.race-brand p { margin: 7px 0 0; color: #ffdcca; font-size: 11px; font-weight: 700; }

.race-status { min-width: 150px; align-self: center; padding: 10px 14px; display: flex; flex-direction: column; border: 1px solid rgba(255, 232, 160, 0.55); border-radius: 8px; background: rgba(42, 8, 7, 0.5); text-align: right; }
.race-status span, .race-status small { color: #ffc85a; font-size: 8px; font-weight: 950; letter-spacing: 0.15em; }
.race-status strong { margin: 2px 0; font-size: 18px; }

.race-bunting { height: 20px; padding: 0 7px; z-index: 3; display: flex; justify-content: space-around; border-top: 2px solid #fff1b2; }
.race-bunting i { width: 0; height: 0; border-top: 14px solid var(--flag); border-right: 8px solid transparent; border-left: 8px solid transparent; filter: drop-shadow(0 2px 1px rgba(0, 0, 0, 0.28)); }
.race-bunting i:nth-child(4n + 1) { --flag: #ff4d6d; }
.race-bunting i:nth-child(4n + 2) { --flag: #ffd43b; }
.race-bunting i:nth-child(4n + 3) { --flag: #38d9f1; }
.race-bunting i:nth-child(4n) { --flag: #f5f1e6; }

.course { min-height: 0; padding: 23px 18px 15px; z-index: 1; flex: 1 1 auto; position: relative; display: flex; flex-direction: column; justify-content: center; gap: 6px; }
.course-labels { margin-left: 190px; padding: 0 70px 0 5px; display: flex; justify-content: space-between; color: rgba(255, 251, 219, 0.86); font-size: 8px; font-weight: 950; letter-spacing: 0.14em; text-shadow: 0 2px 3px #102516; }
.course-labels b { color: #ffe269; }

.finish-post { width: 49px; position: absolute; top: 38px; right: 96px; bottom: 13px; z-index: 6; pointer-events: none; }
.finish-post::before, .finish-post::after { width: 6px; position: absolute; top: 0; bottom: 0; content: ''; background: repeating-linear-gradient(#f4f1e7 0 8px, #171715 8px 16px); box-shadow: 0 2px 5px #000; }
.finish-post::before { left: 2px; }
.finish-post::after { right: 2px; }
.finish-post i { height: 18px; position: absolute; top: 9px; right: 4px; left: 4px; background: repeating-conic-gradient(#fff 0 25%, #111 0 50%) 0 / 12px 12px; }
.finish-post b { position: absolute; top: -17px; left: 50%; color: #fff7c4; font-size: 8px; letter-spacing: 0.15em; transform: translateX(-50%); }

.runner { min-height: 58px; display: grid; grid-template-columns: 184px minmax(220px, 1fr) 76px; align-items: center; gap: 8px; position: relative; border: 1px solid rgba(255, 242, 198, 0.25); border-radius: 10px; background: linear-gradient(90deg, rgba(46, 25, 11, 0.9), rgba(75, 44, 17, 0.72) 34%, rgba(38, 67, 31, 0.72)); box-shadow: inset 0 1px rgba(255, 255, 255, 0.08), 0 5px 12px rgba(13, 28, 14, 0.26); transition: border-color 0.25s, box-shadow 0.25s, transform 0.25s; }
.runner.current { z-index: 5; border-color: #ffe776; box-shadow: 0 0 0 2px color-mix(in srgb, var(--runner-color) 70%, #fff), 0 0 24px rgba(255, 222, 89, 0.38); transform: scale(1.012); }
.runner.winner { border-color: #fff2a2; background: linear-gradient(90deg, rgba(107, 63, 5, 0.94), rgba(146, 91, 8, 0.82), rgba(46, 93, 35, 0.8)); box-shadow: 0 0 0 2px #ffd84d, 0 0 30px rgba(255, 215, 61, 0.62); }

.runner-card { min-width: 0; height: 100%; padding: 6px 8px; display: grid; grid-template-columns: 24px 38px minmax(0, 1fr) 41px; align-items: center; gap: 7px; border-radius: 9px 0 0 9px; background: linear-gradient(95deg, color-mix(in srgb, var(--runner-color) 34%, #27180e), rgba(32, 20, 12, 0.94)); }
.lane-number { width: 23px; height: 23px; display: grid; place-items: center; border-radius: 50%; background: var(--runner-color); color: #1a120c; font-size: 11px; font-weight: 1000; }
.runner-card img { width: 36px; height: 36px; border: 2px solid #fff7da; border-radius: 50%; object-fit: cover; background: #30251d; }
.runner-card > div { min-width: 0; display: flex; flex-direction: column; }
.runner-card strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.runner-card small { color: color-mix(in srgb, var(--runner-color) 60%, #fff); font-size: 7px; font-weight: 950; letter-spacing: 0.08em; }
.target-number { width: 39px; height: 39px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 2px solid var(--runner-color); border-radius: 50%; background: #f8efd4; color: #22150e; font-size: 18px; line-height: 0.8; box-shadow: inset 0 0 0 2px #4b3420; }
.target-number small { color: #6d4e35; font-size: 6px; letter-spacing: 0.08em; }

.lane { height: 47px; position: relative; overflow: visible; border-top: 2px dashed rgba(255, 247, 209, 0.34); border-bottom: 2px dashed rgba(255, 247, 209, 0.34); background: repeating-linear-gradient(90deg, rgba(245, 220, 162, 0.06) 0 7%, rgba(25, 55, 27, 0.12) 7% 14%); }
.lane::after { position: absolute; inset: 50% 0 auto; border-top: 1px solid rgba(255, 255, 255, 0.08); content: ''; }
.distance-fill { height: 100%; position: absolute; top: 0; left: 0; border-right: 2px solid var(--runner-color); background: linear-gradient(90deg, color-mix(in srgb, var(--runner-color) 12%, transparent), color-mix(in srgb, var(--runner-color) 27%, transparent)); transition: width 0.8s cubic-bezier(.2, .8, .2, 1); }
.start-rail { width: 3px; position: absolute; top: -5px; bottom: -5px; left: 4%; background: #f5e7c4; box-shadow: 0 0 0 1px #4c3825; }

.donkey-marker { width: 96px; height: 54px; position: absolute; bottom: -2px; left: var(--runner-position); z-index: 4; transform: translateX(-50%); transition: left 0.8s cubic-bezier(.16, .84, .32, 1.08); }
.donkey-marker svg { width: 100%; height: 100%; overflow: visible; filter: drop-shadow(0 3px 2px rgba(0, 0, 0, 0.45)); }
.donkey-shadow { fill: rgba(0, 0, 0, 0.28); }
.donkey-coat, .donkey-head, .donkey-ear, .donkey-leg { fill: #6d5a49; stroke: #33261d; stroke-width: 2; stroke-linejoin: round; }
.donkey-muzzle { fill: #b6a58f; stroke: #33261d; stroke-width: 2; }
.donkey-tail { fill: none; stroke: #33261d; stroke-width: 5; stroke-linecap: round; }
.donkey-eye { fill: #fff; stroke: #17120f; stroke-width: 1.5; }
.saddle { fill: var(--runner-color); stroke: #33261d; stroke-width: 2.2; }
.saddle-trim { fill: none; stroke: #ffeeb8; stroke-width: 2; }
.donkey-body text { fill: #18100b; font: 1000 14px system-ui, sans-serif; text-anchor: middle; }

.movement-callout { min-width: 33px; padding: 4px 7px; position: absolute; top: -17px; left: 54%; z-index: 8; border: 2px solid #fff; border-radius: 99px; background: #48d66b; color: #071b0d; font-size: 13px; font-weight: 1000; text-align: center; animation: callout 1.35s ease-out both; }
.runner.backward .movement-callout { background: #ff5b63; color: #2b0508; }
.winner-rosette { width: 35px; height: 35px; position: absolute; top: -13px; left: 67%; z-index: 7; display: grid; place-items: center; border: 3px double #8b5500; border-radius: 50%; background: #ffd84e; color: #4d2d00; font-size: 10px; font-weight: 1000; transform: rotate(8deg); }

.runner.forward .donkey-body, .runner.backward .donkey-body { animation: gallop 0.23s ease-in-out 5; transform-origin: center bottom; }
.runner.backward .donkey-body { animation-name: buck; }
.runner.forward .leg-a, .runner.forward .leg-c, .runner.backward .leg-a, .runner.backward .leg-c { animation: legs 0.23s ease-in-out 5 alternate; transform-origin: top; }
.dust { width: 7px; height: 7px; position: absolute; bottom: 3px; left: 15px; border-radius: 50%; background: #e4d1a5; opacity: 0; }
.runner.forward .dust, .runner.backward .dust { animation: dust 0.65s ease-out 2; }
.dust-two { animation-delay: 0.12s !important; }
.dust-three { animation-delay: 0.24s !important; }

.runner-progress { height: 100%; display: grid; grid-template-columns: auto auto; place-content: center; align-items: baseline; border-radius: 0 9px 9px 0; background: rgba(28, 19, 12, 0.62); text-align: center; }
.runner-progress strong { color: #ffe16b; font-size: 27px; line-height: 0.9; }
.runner-progress span { color: #d8ccb1; font-size: 10px; }
.runner-progress small { grid-column: 1 / -1; color: #c5b999; font-size: 6px; font-weight: 950; letter-spacing: 0.14em; }

.race-footer { min-height: 45px; padding: 8px 17px; z-index: 2; display: flex; align-items: center; gap: 10px; border-top: 3px solid #f2cf5b; background: linear-gradient(90deg, #3c120d, #79231a, #3c120d); font-size: 9px; font-weight: 800; }
.race-footer span { padding: 5px 8px; border: 1px solid rgba(255, 236, 180, 0.28); border-radius: 5px; background: rgba(0, 0, 0, 0.2); }
.race-footer span b { color: #ffdc5a; }
.race-footer > strong { margin-left: auto; color: #ffe990; font-size: 9px; letter-spacing: 0.11em; }

@keyframes gallop { 50% { transform: translateY(-6px) rotate(-1deg); } }
@keyframes buck { 40% { transform: translateY(-4px) rotate(4deg); } 70% { transform: translateY(1px) rotate(-3deg); } }
@keyframes legs { to { transform: rotate(13deg); } }
@keyframes dust { 0% { opacity: 0.7; transform: translate(0, 0) scale(0.5); } 100% { opacity: 0; transform: translate(-24px, -10px) scale(2); } }
@keyframes callout { 0% { opacity: 0; transform: translateY(8px) scale(0.7); } 18%, 72% { opacity: 1; transform: translateY(0) scale(1); } 100% { opacity: 0; transform: translateY(-12px) scale(0.9); } }

@media (max-width: 1200px) {
  .runner { grid-template-columns: 160px minmax(190px, 1fr) 65px; }
  .runner-card { grid-template-columns: 21px 32px minmax(0, 1fr) 36px; gap: 5px; }
  .runner-card img { width: 31px; height: 31px; }
  .target-number { width: 34px; height: 34px; font-size: 16px; }
  .course-labels { margin-left: 165px; }
  .finish-post { right: 82px; }
}

@media (prefers-reduced-motion: reduce) {
  .donkey-marker, .distance-fill { transition-duration: 0.001ms; }
  .runner .donkey-body, .runner .donkey-leg, .runner .dust, .movement-callout { animation: none !important; }
}
</style>
