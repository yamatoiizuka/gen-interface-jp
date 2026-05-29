import type { CSSProperties } from "react";
import {
  compositionShapes,
  type CompositionShape,
  type CompositionWeightShape,
} from "../generated/compositionShapes";

const UPEM = 1000;

// SVG line-box geometry for the generated font-coordinate paths.
const BASELINE_FROM_TOP = 1030;
const BELOW_BASELINE = 303;
const VIEWBOX_HEIGHT = BASELINE_FROM_TOP + BELOW_BASELINE;

const isKanaOrPunct = (cp: number): boolean =>
  (cp >= 0x3000 && cp <= 0x303f) ||
  (cp >= 0x3040 && cp <= 0x309f) ||
  (cp >= 0x30a0 && cp <= 0x30ff) ||
  (cp >= 0x31f0 && cp <= 0x31ff) ||
  (cp >= 0xff00 && cp <= 0xffef);

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

export type CompositionShapeKey = keyof typeof compositionShapes;

type Props = {
  shapeKey: CompositionShapeKey;
  weight?: number;
  scale?: number;
  tracking?: number;
  trackingKana?: number;
  paltAmount?: number;
  yShift?: number;
  ariaLabel?: string;
  ariaHidden?: boolean;
  className?: string;
  style?: CSSProperties;
};

type WeightLayer = {
  key: string;
  opacity: number;
  shape: CompositionWeightShape;
};

type PlacedGlyph = { d: string; x: number; y: number };
type PlacedLayer = {
  key: string;
  opacity: number;
  placed: PlacedGlyph[];
  totalAdvance: number;
};

function getWeightLayers(
  shape: CompositionShape,
  weight: number,
): WeightLayer[] {
  const entries = Object.entries(shape.weights)
    .map(([key, value]) => ({ key, weight: Number(key), shape: value }))
    .sort((a, b) => a.weight - b.weight);

  if (entries.length === 0) return [];
  if (entries.length === 1) {
    return [{ key: entries[0].key, opacity: 1, shape: entries[0].shape }];
  }

  if (weight <= entries[0].weight) {
    return [{ key: entries[0].key, opacity: 1, shape: entries[0].shape }];
  }

  const last = entries[entries.length - 1];
  if (weight >= last.weight) {
    return [{ key: last.key, opacity: 1, shape: last.shape }];
  }

  for (let i = 0; i < entries.length - 1; i += 1) {
    const lower = entries[i];
    const upper = entries[i + 1];
    if (weight >= lower.weight && weight <= upper.weight) {
      const t = clamp(
        (weight - lower.weight) / (upper.weight - lower.weight),
        0,
        1,
      );
      return [
        { key: lower.key, opacity: 1 - t, shape: lower.shape },
        { key: upper.key, opacity: t, shape: upper.shape },
      ].filter((layer) => layer.opacity > 0.001);
    }
  }

  return [{ key: last.key, opacity: 1, shape: last.shape }];
}

function placeLayer(
  shape: CompositionShape,
  layer: WeightLayer,
  paltAmount: number,
  tracking: number,
  trackingKana: number,
): PlacedLayer {
  const placed: PlacedGlyph[] = [];
  let totalAdvance = 0;

  for (const glyph of layer.shape.glyphs) {
    const ax = lerp(glyph.noPalt.ax, glyph.withPalt.ax, paltAmount);
    const dx = lerp(glyph.noPalt.dx, glyph.withPalt.dx, paltAmount);
    const dy = lerp(glyph.noPalt.dy, glyph.withPalt.dy, paltAmount);
    const cp = shape.codepoints[glyph.cl] ?? 0;
    const t = isKanaOrPunct(cp) ? trackingKana : tracking;
    const half = t / 2;
    placed.push({ d: glyph.path, x: totalAdvance + dx + half, y: -dy });
    totalAdvance += ax + t;
  }

  return {
    key: layer.key,
    opacity: layer.opacity,
    placed,
    totalAdvance,
  };
}

export function CompositionGlyphText({
  shapeKey,
  weight = 465,
  scale = 0.925,
  tracking = 30,
  trackingKana = 45,
  paltAmount = 1,
  yShift = 0,
  ariaLabel,
  ariaHidden,
  className,
  style,
}: Props) {
  const shape = compositionShapes[shapeKey] as CompositionShape;
  const layers = getWeightLayers(shape, weight).map((layer) =>
    placeLayer(shape, layer, paltAmount, tracking, trackingKana),
  );
  const totalAdvance = Math.max(
    1,
    ...layers.map((layer) => layer.totalAdvance),
  );
  const widthEm = (totalAdvance * scale) / UPEM;
  const heightEm = (VIEWBOX_HEIGHT * scale) / UPEM;
  const baselineAnchorEm = (BASELINE_FROM_TOP / UPEM) * (1 - scale);
  const yShiftEm = yShift / UPEM;

  return (
    <svg
      className={className}
      role={ariaHidden ? undefined : ariaLabel ? "img" : undefined}
      aria-label={ariaHidden ? undefined : ariaLabel}
      aria-hidden={ariaHidden}
      focusable={ariaHidden ? "false" : undefined}
      style={{
        ...style,
        display: "block",
        width: `${widthEm}em`,
        height: `${heightEm}em`,
        top: `${baselineAnchorEm - yShiftEm}em`,
      }}
      viewBox={`0 0 ${totalAdvance} ${VIEWBOX_HEIGHT}`}
    >
      {layers.map((layer) => (
        <g key={layer.key} opacity={layer.opacity}>
          {layer.placed.map((glyph, index) => (
            <path
              key={index}
              d={glyph.d}
              transform={`translate(${glyph.x} ${
                BASELINE_FROM_TOP + glyph.y
              }) scale(1 -1)`}
            />
          ))}
        </g>
      ))}
    </svg>
  );
}
