<script setup>
// Hand-built SVG scenes, one per game. No image files: nothing to load, it
// stays crisp at any size, themes cleanly, and works offline on the Pi.
// Each scene gets its own palette so the library reads as a set of distinct
// games rather than a grid of identical cards.
defineProps({
  art: { type: String, required: true },
  animate: { type: Boolean, default: false },
})
</script>

<template>
  <svg class="game-art" :class="{ animate }" viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    <!-- X01: big countdown numerals over a treble bed -->
    <g v-if="art === 'x01'">
      <defs>
        <linearGradient id="g-x01" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#0f2027" /><stop offset="100%" stop-color="#2c5364" />
        </linearGradient>
      </defs>
      <rect width="320" height="180" fill="url(#g-x01)" />
      <g opacity="0.18" stroke="#7ee8fa" stroke-width="2" fill="none">
        <circle cx="160" cy="90" r="70" /><circle cx="160" cy="90" r="46" /><circle cx="160" cy="90" r="16" />
        <path v-for="i in 10" :key="i" :d="`M160 90 L${160 + 70 * Math.cos(i * 0.628)} ${90 + 70 * Math.sin(i * 0.628)}`" />
      </g>
      <text x="160" y="104" class="art-big" fill="#f6f9ff">501</text>
      <text x="160" y="130" class="art-sub" fill="#7ee8fa">DOUBLE OUT</text>
    </g>

    <!-- Round the Clock: a clock face made of dart numbers -->
    <g v-else-if="art === 'clock'">
      <rect width="320" height="180" fill="#171a2b" />
      <circle cx="160" cy="90" r="66" fill="none" stroke="#3d4a8a" stroke-width="3" />
      <g v-for="i in 12" :key="i">
        <circle :cx="160 + 66 * Math.sin(i * 0.5236)" :cy="90 - 66 * Math.cos(i * 0.5236)" r="7"
                :fill="i <= 4 ? '#ffd166' : '#2a3157'" />
      </g>
      <line x1="160" y1="90" x2="160" y2="42" stroke="#ffd166" stroke-width="4" stroke-linecap="round" class="hand-h" />
      <line x1="160" y1="90" x2="196" y2="112" stroke="#ff6b6b" stroke-width="3" stroke-linecap="round" class="hand-m" />
      <circle cx="160" cy="90" r="6" fill="#ff6b6b" />
    </g>

    <!-- Shanghai: three darts fanned into one bed -->
    <g v-else-if="art === 'shanghai'">
      <defs>
        <linearGradient id="g-sh" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3a1c71" /><stop offset="55%" stop-color="#d76d77" /><stop offset="100%" stop-color="#ffaf7b" />
        </linearGradient>
      </defs>
      <rect width="320" height="180" fill="url(#g-sh)" />
      <!-- three darts converging on one bed, fanned so they read as three -->
      <g opacity="0.95">
        <g v-for="(tail, i) in [[34, 18], [22, 74], [48, 132]]" :key="i">
          <line :x1="tail[0]" :y1="tail[1]" x2="150" y2="88"
                stroke="#2b1240" stroke-width="5" stroke-linecap="round" />
          <polygon
            :points="`${tail[0] - 12},${tail[1] - 9} ${tail[0] + 10},${tail[1] - 2} ${tail[0] - 8},${tail[1] + 10}`"
            fill="#ffe066" stroke="#2b1240" stroke-width="1.5" />
        </g>
      </g>
      <circle cx="152" cy="88" r="15" fill="none" stroke="#2b1240" stroke-width="5" />
      <circle cx="152" cy="88" r="5" fill="#2b1240" />
      <text x="228" y="150" class="art-sub" fill="#2b1240">1 &#183; 2 &#183; 3 &#8230; 20</text>
    </g>

    <!-- Killer: crosshair over a skull-ish target -->
    <g v-else-if="art === 'killer'">
      <rect width="320" height="180" fill="#12060a" />
      <circle cx="160" cy="90" r="62" fill="#2a0b12" stroke="#e63946" stroke-width="3" />
      <circle cx="160" cy="90" r="38" fill="none" stroke="#e63946" stroke-width="2" opacity="0.6" />
      <g stroke="#ff8fa3" stroke-width="3" stroke-linecap="round">
        <line x1="160" y1="14" x2="160" y2="44" /><line x1="160" y1="136" x2="160" y2="166" />
        <line x1="84" y1="90" x2="114" y2="90" /><line x1="206" y1="90" x2="236" y2="90" />
      </g>
      <circle cx="160" cy="90" r="10" fill="#e63946" class="pulse" />
      <text x="160" y="172" class="art-sub" fill="#ff8fa3">LAST ONE STANDING</text>
    </g>

    <!-- Donkey Derby: silhouetted racers with a finish post -->
    <g v-else-if="art === 'derby'">
      <defs>
        <linearGradient id="g-dd" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#f7d08a" /><stop offset="100%" stop-color="#8ab17d" />
        </linearGradient>
      </defs>
      <rect width="320" height="180" fill="url(#g-dd)" />
      <rect y="128" width="320" height="52" fill="#6b8f5e" />
      <!-- Positioning lives on the OUTER g and the animation on the inner
           one: a CSS transform overrides the SVG transform attribute, so
           animating the positioned element would snap them all to x=0. -->
      <g v-for="(x, i) in [52, 132, 212]" :key="i" :transform="`translate(${x},0)`">
        <g :class="'racer r' + i">
          <ellipse cx="0" cy="112" rx="26" ry="13" fill="#4a3728" />
          <rect x="-20" y="118" width="6" height="18" fill="#4a3728" />
          <rect x="10" y="118" width="6" height="18" fill="#4a3728" />
          <circle cx="24" cy="98" r="10" fill="#4a3728" />
          <polygon points="20,90 24,78 29,90" fill="#4a3728" />
          <ellipse cx="-26" cy="104" rx="4" ry="9" fill="#4a3728" transform="rotate(-25 -26 104)" />
        </g>
      </g>
      <g>
        <rect x="286" y="70" width="5" height="70" fill="#333" />
        <rect x="291" y="70" width="24" height="16" fill="#fff" />
        <rect x="291" y="70" width="12" height="8" fill="#111" />
        <rect x="303" y="78" width="12" height="8" fill="#111" />
      </g>
    </g>

    <!-- Space Invaders: pixel invader grid over a starfield -->
    <g v-else-if="art === 'invaders'">
      <rect width="320" height="180" fill="#05060f" />
      <circle v-for="i in 34" :key="i" :cx="(i * 47) % 320" :cy="(i * 29) % 150" :r="i % 5 === 0 ? 1.6 : 0.9" fill="#8fb8ff" opacity="0.7" />
      <!-- Outer g positions, inner g animates - see the note on the racers. -->
      <g v-for="row in 2" :key="row">
        <g v-for="col in 5" :key="col" :transform="`translate(${20 + col * 47},${34 + row * 36})`">
          <g class="invader" :style="{ animationDelay: `${(row + col) * 0.12}s` }">
            <rect x="-11" y="-8" width="22" height="12" :fill="row === 1 ? '#5eead4' : '#a78bfa'" />
            <rect x="-15" y="-4" width="6" height="10" :fill="row === 1 ? '#5eead4' : '#a78bfa'" />
            <rect x="9" y="-4" width="6" height="10" :fill="row === 1 ? '#5eead4' : '#a78bfa'" />
            <rect x="-7" y="-4" width="4" height="4" fill="#05060f" />
            <rect x="3" y="-4" width="4" height="4" fill="#05060f" />
            <rect x="-9" y="4" width="5" height="4" :fill="row === 1 ? '#5eead4' : '#a78bfa'" />
            <rect x="4" y="4" width="5" height="4" :fill="row === 1 ? '#5eead4' : '#a78bfa'" />
          </g>
        </g>
      </g>
      <polygon points="160,150 148,168 172,168" fill="#facc15" />
      <rect x="158" y="120" width="4" height="22" fill="#facc15" class="laser" />
    </g>

    <!-- Darts Golf: the flag on the green, with the board as the hole -->
    <g v-else-if="art === 'golf'">
      <rect width="320" height="180" fill="#0b1c0e" />
      <!-- fairway sweeping up to the green -->
      <path d="M0 180 Q80 118 160 128 Q250 138 320 96 L320 180 Z" fill="#1c4023" />
      <path d="M0 180 Q90 140 176 150 Q262 160 320 128 L320 180 Z" fill="#2f7a3c" />
      <ellipse cx="196" cy="140" rx="86" ry="26" fill="#4f9a4a" />
      <!-- a bunker, because a course needs one -->
      <ellipse cx="64" cy="150" rx="34" ry="12" fill="#e8dfbc" opacity="0.85" />
      <!-- the hole: a dartboard sunk into the green -->
      <g transform="translate(196,138)">
        <ellipse rx="26" ry="9" fill="#061007" />
        <g transform="scale(1,0.36)">
          <circle r="24" fill="#12250f" stroke="#7fbf5a" stroke-width="1.5" />
          <circle r="15" fill="none" stroke="#7fbf5a" stroke-width="2" opacity="0.6" />
          <circle r="6" fill="#f2c14e" />
        </g>
      </g>
      <!-- flagstick and pennant -->
      <rect x="194" y="52" width="3" height="86" fill="#f4f1e4" />
      <path d="M197 54 L243 64 L197 76 Z" fill="#f2c14e" />
      <path d="M197 54 L243 64 L197 64 Z" fill="#ffdd8a" />
      <text x="217" y="69" class="art-pin" fill="#2a1e05">18</text>
      <!-- ball on the tee -->
      <circle cx="64" cy="146" r="6" fill="#f8f6ee" />
      <circle cx="62" cy="144" r="1" fill="#cfcab5" />
      <circle cx="66" cy="147" r="1" fill="#cfcab5" />
      <text x="160" y="34" class="art-sub" fill="#e8dfbc">DARTS GOLF</text>
      <text x="160" y="172" class="art-sub" fill="#7fbf5a">18 HOLES &#183; LOWEST WINS</text>
    </g>

    <!-- Noughts & Crosses: a neon grid mid-game, X about to win the diagonal -->
    <g v-else-if="art === 'tictactoe'">
      <rect width="320" height="180" fill="#0a0f1e" />
      <circle cx="160" cy="90" r="150" fill="#16224a" opacity="0.35" />
      <g stroke="#3d4d78" stroke-width="5" stroke-linecap="round">
        <line x1="132" y1="26" x2="132" y2="154" />
        <line x1="188" y1="26" x2="188" y2="154" />
        <line x1="76" y1="68" x2="244" y2="68" />
        <line x1="76" y1="112" x2="244" y2="112" />
      </g>
      <!-- cells centres: columns 104/160/216, rows 47/90/133 -->
      <g stroke="#4ba3ff" stroke-width="7" stroke-linecap="round">
        <g v-for="(c, i) in [[104, 47], [160, 90], [216, 133]]" :key="i">
          <line :x1="c[0] - 11" :y1="c[1] - 11" :x2="c[0] + 11" :y2="c[1] + 11" />
          <line :x1="c[0] - 11" :y1="c[1] + 11" :x2="c[0] + 11" :y2="c[1] - 11" />
        </g>
      </g>
      <circle cx="216" cy="47" r="13" fill="none" stroke="#ff5a5a" stroke-width="7" />
      <circle cx="104" cy="90" r="13" fill="none" stroke="#ff5a5a" stroke-width="7" />
      <line x1="92" y1="35" x2="228" y2="145" stroke="#f2c14e" stroke-width="4" stroke-linecap="round" opacity="0.85" />
      <text x="270" y="40" class="art-pin" fill="#4ba3ff">20</text>
      <text x="52" y="150" class="art-pin" fill="#ff5a5a">12</text>
    </g>

    <!-- generic fallback: a stylised board for catalogued-but-unbuilt games -->
    <g v-else>
      <rect width="320" height="180" fill="#1b1f2a" />
      <g transform="translate(160,90)">
        <circle r="60" fill="#232838" stroke="#3a4157" stroke-width="2" />
        <g v-for="i in 20" :key="i">
          <path :d="`M0 0 L${60 * Math.sin(i * 0.314)} ${-60 * Math.cos(i * 0.314)}`" stroke="#2c3145" stroke-width="1" />
        </g>
        <circle r="40" fill="none" stroke="#4ade80" stroke-width="4" opacity="0.55" />
        <circle r="22" fill="none" stroke="#f87171" stroke-width="4" opacity="0.55" />
        <circle r="7" fill="#4ade80" />
      </g>
    </g>
  </svg>
</template>

<style scoped>
.game-art {
  display: block;
  width: 100%;
  height: 100%;
}

.art-big {
  font: 700 54px ui-monospace, monospace;
  text-anchor: middle;
  letter-spacing: 2px;
}

.art-sub {
  font: 600 13px ui-monospace, monospace;
  text-anchor: middle;
  letter-spacing: 3px;
}

/* The hole number on the golf pennant - too small a space for art-sub's
   3px tracking, which would push it off the flag. */
.art-pin {
  font: 800 11px ui-monospace, monospace;
  text-anchor: middle;
}

/* Motion only on hover, and never for people who ask for reduced motion. */
.pulse,
.laser,
.invader,
.racer,
.hand-h,
.hand-m {
  animation-play-state: paused;
}

.animate .pulse {
  animation: pulse 1.6s ease-in-out infinite;
  animation-play-state: running;
  transform-origin: 160px 90px;
}

.animate .laser {
  animation: laser 1.1s linear infinite;
  animation-play-state: running;
}

.animate .invader {
  animation: drift 2.4s ease-in-out infinite;
  animation-play-state: running;
}

.animate .racer {
  animation: gallop 1.1s ease-in-out infinite;
  animation-play-state: running;
}

.animate .r1 { animation-delay: 0.16s; }
.animate .r2 { animation-delay: 0.32s; }

.animate .hand-m {
  animation: spin 3s linear infinite;
  animation-play-state: running;
  transform-origin: 160px 90px;
}

@keyframes pulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.5); opacity: 0.55; } }
@keyframes laser { 0% { transform: translateY(0); opacity: 1; } 100% { transform: translateY(-105px); opacity: 0; } }
@keyframes drift { 0%, 100% { transform: translateX(-5px); } 50% { transform: translateX(5px); } }
@keyframes gallop { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
@keyframes spin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .animate * { animation: none !important; }
}
</style>
