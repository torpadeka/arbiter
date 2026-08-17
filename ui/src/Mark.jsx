/** The Arbiter mark.
 *
 *  Original geometry, drawn for this project rather than borrowed: two claims
 *  arrive from opposite sides at a vertical line of judgment. The upper one
 *  crosses and continues in ember; the lower one stops at the line. That is
 *  literally what the system does, so the mark carries the idea rather than
 *  decorating around it.
 *
 *  Hairline strokes only, no fills, so it holds at 16px and obeys the system.
 */
export default function Mark({ size = 42, ember = '#cc6437', line = '#ffffff' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      {/* the line of judgment */}
      <path d="M16 3 V29" stroke={line} strokeWidth="1" />

      {/* upheld: arrives from the left, crosses, continues */}
      <path d="M4 12 H24" stroke={ember} strokeWidth="1" />
      <path d="M21 9 L24 12 L21 15" stroke={ember} strokeWidth="1" />

      {/* overruled: arrives from the right, stops at the line */}
      <path d="M28 21 H16" stroke={line} strokeWidth="1" opacity="0.55" />
      <path d="M19 18 L16 21 L19 24" stroke={line} strokeWidth="1" opacity="0.55" />
    </svg>
  )
}
