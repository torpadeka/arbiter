/** The Arbiter mark: a four point star set inside a compass.
 *
 *  Original geometry, drawn for this project. A compass is the right figure for
 *  a system whose whole claim is that it can tell you which way is true: the
 *  ring and its ticks are the bearing, the star is the fixed point, and the
 *  ember core is the single settled answer at the centre of it.
 *
 *  Hairline strokes only, no fills, in keeping with the rest of the system.
 *  Cardinal points are elongated diamonds, diagonals are plain hairlines, so
 *  the silhouette stays readable when the ring detail drops out at small sizes.
 */
export default function Mark({ size = 44, ember = '#cc6437', line = '#ffffff' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      {/* bearing ring, plus a ticked inner ring for the compass reading */}
      <circle cx="16" cy="16" r="13.2" stroke={line} strokeWidth="1" opacity="0.4" />
      <circle cx="16" cy="16" r="10.8" stroke={line} strokeWidth="1" opacity="0.26"
              strokeDasharray="0.6 3.2" />

      {/* secondary points: hairlines on the diagonals */}
      <g stroke={line} strokeWidth="1" opacity="0.45">
        <path d="M19.7 12.3 L23.5 8.5" />
        <path d="M19.7 19.7 L23.5 23.5" />
        <path d="M12.3 19.7 L8.5 23.5" />
        <path d="M12.3 12.3 L8.5 8.5" />
      </g>

      {/* cardinal points of the star */}
      <g stroke={line} strokeWidth="1">
        <path d="M16 3.2 L17.8 13.8 L16 16 L14.2 13.8 Z" />
        <path d="M16 28.8 L17.8 18.2 L16 16 L14.2 18.2 Z" />
        <path d="M28.8 16 L18.2 17.8 L16 16 L18.2 14.2 Z" />
        <path d="M3.2 16 L13.8 17.8 L16 16 L13.8 14.2 Z" />
      </g>

      {/* the settled answer at the centre */}
      <path d="M16 12.6 L19.4 16 L16 19.4 L12.6 16 Z" stroke={ember} strokeWidth="1" />
    </svg>
  )
}
