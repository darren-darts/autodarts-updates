// Mirrors backend/calibration/board_model.py's board-space convention:
// origin at the bullseye, mm, x+ = right, y+ = down (matches SVG's own
// coordinate sense directly - no flip needed), angle 0 = top, clockwise.
// Radii and segment order come from GET /api/detection/board-geometry
// (backend/detection/routes.py) rather than being restated here, so
// there's exactly one source of truth for anything scoring-relevant, this
// file only turns that data into drawable shapes.

export function boardPointMm(angleRad, radiusMm) {
  return [radiusMm * Math.sin(angleRad), -radiusMm * Math.cos(angleRad)]
}

// Approximates an annular wedge (or, if rInner is 0, a pie slice) with a
// short polygon rather than SVG arc commands - sidesteps sweep-flag
// direction bugs entirely, and at 18-degree wedges the facet count below is
// visually indistinguishable from a true arc.
export function wedgePoints(rInner, rOuter, angleStart, angleEnd, steps = 8) {
  const pts = []
  for (let i = 0; i <= steps; i++) {
    const a = angleStart + ((angleEnd - angleStart) * i) / steps
    pts.push(boardPointMm(a, rOuter))
  }
  if (rInner > 0) {
    for (let i = steps; i >= 0; i--) {
      const a = angleStart + ((angleEnd - angleStart) * i) / steps
      pts.push(boardPointMm(a, rInner))
    }
  } else {
    pts.push([0, 0])
  }
  return pts
}

export function pointsToPath(pts) {
  return pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ') + ' Z'
}

// Builds every filled shape for a full front-on dartboard face from the
// server's geometry payload ({ radii_mm, segments }): 20 wedges x (outer
// single, double, inner single, triple) plus the two bull rings, each
// tagged with the segment number and a fill role for CSS/coloring.
export function buildBoardShapes(geometry) {
  const { radii_mm: r, segments } = geometry
  const wedge = (2 * Math.PI) / segments.length
  const shapes = []

  segments.forEach((segment, i) => {
    const start = i * wedge - wedge / 2
    const end = i * wedge + wedge / 2
    const parity = i % 2 === 0 ? 'a' : 'b'
    shapes.push({ segment, role: `double-${parity}`, points: wedgePoints(r.double_in, r.double_out, start, end) })
    shapes.push({ segment, role: `single-outer-${parity}`, points: wedgePoints(r.triple_out, r.double_in, start, end) })
    shapes.push({ segment, role: `triple-${parity}`, points: wedgePoints(r.triple_in, r.triple_out, start, end) })
    shapes.push({ segment, role: `single-inner-${parity}`, points: wedgePoints(r.bull_out, r.triple_in, start, end) })
  })
  shapes.push({ segment: 25, role: 'bull-outer', points: wedgePoints(r.bull_in, r.bull_out, 0, 2 * Math.PI, 48) })
  shapes.push({ segment: 25, role: 'bull-inner', points: wedgePoints(0, r.bull_in, 0, 2 * Math.PI, 48) })

  // Numbers sit on the surround outside the double ring, as on a real
  // board - putting them inside the scoring area would cover the very
  // beds you're trying to click.
  const outer = geometry.physical_board_radius_mm ?? r.double_out * 1.3
  const labelRadius = (r.double_out + outer) / 2
  const labels = segments.map((segment, i) => ({ segment, pos: boardPointMm(i * wedge, labelRadius) }))

  return { shapes, labels }
}
