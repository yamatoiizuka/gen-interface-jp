import { useRef, useState } from 'react'

type Props = {
  kind: 'size' | 'weight'
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  label: string
  /** All possible label strings the pill can show. Rendered as a stacked
   *  grid with non-active entries kept in flow (visibility: hidden), so the
   *  pill's width reflects the widest label and the slider track doesn't
   *  jump as the value changes (e.g. "Thin" → "ExtraLight"). */
  pillLabels?: string[]
}

export function Slider({
  kind,
  value,
  min,
  max,
  step = 1,
  onChange,
  label,
  pillLabels,
}: Props) {
  const trackRef = useRef<HTMLSpanElement>(null)
  /* Tracked so CSS can keep "control-hover" styling active for the whole
     drag, even when the pointer leaves the slider. preventDefault() on
     pointerdown breaks :active reliability across browsers, so we drive
     this from React instead. */
  const [isDragging, setIsDragging] = useState(false)

  const updateFromX = (clientX: number) => {
    const el = trackRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    const raw = min + ratio * (max - min)
    const snapped = Math.round(raw / step) * step
    onChange(Math.max(min, Math.min(max, snapped)))
  }

  const handlePointerDown = (e: React.PointerEvent<HTMLSpanElement>) => {
    e.preventDefault()
    setIsDragging(true)
    updateFromX(e.clientX)
    const move = (ev: PointerEvent) => updateFromX(ev.clientX)
    const up = () => {
      setIsDragging(false)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }

  const ratio = (value - min) / (max - min)

  return (
    <div className={`slider${isDragging ? ' slider--dragging' : ''}`}>
      <div className="slider__main">
        <span className={`slider__icon slider__icon--${kind}`} aria-hidden="true">
          <span className="slider__icon-a slider__icon-a-large">A</span>
          <span className="slider__icon-a slider__icon-a-small">A</span>
        </span>
        <span
          className="slider__track"
          ref={trackRef}
          onPointerDown={handlePointerDown}
          role="slider"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={value}
          aria-label={kind === 'size' ? 'Font size' : 'Font weight'}
          tabIndex={0}
        >
          <hr className="slider__line" />
          <span
            className="slider__thumb"
            style={{ left: `calc(${ratio * 100}% - var(--slider-thumb-half, 0.4375rem))` }}
          />
        </span>
      </div>
      <span className="slider__pill">
        {(() => {
          // Always include the active label so it can render visibly even if
          // the caller passed only a "widest possible" sample list.
          const labels = pillLabels
            ? pillLabels.includes(label)
              ? pillLabels
              : [...pillLabels, label]
            : [label]
          return labels.map((l) => (
            <span
              key={l}
              className={`slider__pill-text${l === label ? ' is-active' : ''}`}
            >
              {l}
            </span>
          ))
        })()}
      </span>
    </div>
  )
}
