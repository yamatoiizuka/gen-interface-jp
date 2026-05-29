import { useCallback, useEffect, useRef, useState } from "react";
import { SectionHead } from "./SectionHead";
import { CompositionGlyphText } from "./CompositionGlyphText";

// Lines positioned within the 128px-tall group, in px (converted to rem inline)
const GROUP_LINES_TOP = [29, 46, 99];
const GROUP_LINES_BOTTOM = [29, 47, 99];

// Debug tuner: sliders + play/reset for the simulation parameters. Hidden by
// default; flip to true if the underlying transforms need re-tuning.
const SHOW_TUNER = false;

type TunerProps = {
  wght: number;
  scale: number;
  tracking: number;
  trackingKana: number;
  /** Continuous 0–1 blend of the palt feature so it can animate smoothly. */
  paltAmount: number;
  /** Vertical offset in "px-at-base", applied as rem so the value tracks the
   *  rem scaling of `html { font-size: max(16px, 1.25vw) }` at wider viewports. */
  yShift: number;
};

/** "Raw Noto Sans JP, no transforms applied" — the start of the simulation. */
const INITIAL_PARAMS: TunerProps = {
  wght: 400,
  scale: 1.0,
  tracking: 0,
  trackingKana: 0,
  paltAmount: 0,
  yShift: 0,
};

/** Final state — matches font.build FAMILIES["normal"]. yShift is in font-coord
 *  units (1/1000 em) so the offset stays consistent at any font-size. */
const DEFAULT_PARAMS: TunerProps = {
  wght: 465,
  scale: 0.925,
  tracking: 30,
  trackingKana: 45,
  paltAmount: 1,
  yShift: 25,
};

/** Animation step: each step animates one or more keys to a target value
 *  simultaneously. The "from" values are sampled from current state at the
 *  start of the step, so a key can pick up where a prior step left it. */
type AnimStep = {
  updates: Array<{ key: keyof TunerProps; to: number }>;
};

const ANIM_STEPS: AnimStep[] = [
  { updates: [{ key: "scale", to: DEFAULT_PARAMS.scale }] },
  { updates: [{ key: "yShift", to: DEFAULT_PARAMS.yShift }] },
  { updates: [{ key: "wght", to: DEFAULT_PARAMS.wght }] },
  { updates: [{ key: "paltAmount", to: DEFAULT_PARAMS.paltAmount }] },
  // Bring kana along with the Latin/CJK base tracking, then…
  {
    updates: [
      { key: "tracking", to: DEFAULT_PARAMS.tracking },
      { key: "trackingKana", to: DEFAULT_PARAMS.tracking },
    ],
  },
  // …push kana further to its own tracking value.
  { updates: [{ key: "trackingKana", to: DEFAULT_PARAMS.trackingKana }] },
];

const ANIM_DURATION_MS = 300;
const ANIM_GAP_MS = 1000;
const ANIM_INITIAL_PAUSE_MS = 2000;
const ANIM_FINAL_PAUSE_MS = 2500;
/** Duration of the rewind tween that runs after the simulation finishes —
 *  every param eases back to INITIAL_PARAMS so the Tweak numbers visibly
 *  count down to default. Pairs with the param-opacity transition (which
 *  drops 1 → 0.3) to act as the loop's only "reset" cue. */
const RESET_TWEEN_MS = 600;
/** Hold the rewinded initial state for this long before kicking off the
 *  next pass — gives the viewer a beat to register the "before" values
 *  after they've ticked back into place. */
const RESET_HOLD_MS = 2000;

const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);

type SliderRowProps = {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  display: string;
  onChange: (v: number) => void;
};

function SliderRow({
  label,
  min,
  max,
  step,
  value,
  display,
  onChange,
}: SliderRowProps) {
  return (
    <>
      <span className="comp-tuner__label">{label}</span>
      <input
        className="comp-tuner__range"
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="comp-tuner__value">{display}</span>
    </>
  );
}

export function Composition() {
  const [p, setP] = useState<TunerProps>(INITIAL_PARAMS);
  // Which params are currently tweening — used to highlight the matching
  // entries in the Tweak label/value list while their values change.
  const [activeKeys, setActiveKeys] = useState<Set<keyof TunerProps>>(
    () => new Set(),
  );

  const setField = <K extends keyof TunerProps>(key: K, value: TunerProps[K]) =>
    setP((prev) => ({ ...prev, [key]: value }));

  // Refs so a re-trigger can cancel any in-flight RAF/timeout cleanly.
  const rafRef = useRef<number | null>(null);
  const timeoutRef = useRef<number | null>(null);

  const cancelAnimation = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // Mirror p in a ref so the animation loop can read the "from" values for
  // multi-step keys (trackingKana picks up at 30 after the joint step).
  const pRef = useRef<TunerProps>(INITIAL_PARAMS);
  pRef.current = p;

  const playAnimation = useCallback(() => {
    cancelAnimation();
    // Initial state is INITIAL_PARAMS (useState default or a prior reset);
    // the first run waits ANIM_INITIAL_PAUSE_MS before kicking off so the
    // viewer can see the "untouched Noto" baseline before the simulation
    // takes over.
    let stepIndex = 0;

    const runStep = () => {
      if (stepIndex >= ANIM_STEPS.length) {
        // Sequence done — hold the final state, then snap back and loop.
        timeoutRef.current = window.setTimeout(loopBack, ANIM_FINAL_PAUSE_MS);
        return;
      }
      const step = ANIM_STEPS[stepIndex];
      // Capture each key's current value as the "from" for this step's lerp.
      const startVals: Partial<Record<keyof TunerProps, number>> = {};
      for (const u of step.updates) {
        startVals[u.key] = pRef.current[u.key] as number;
      }
      // Light up the matching Tweak entries — additively, so once a value
      // settles to its final state it stays at full opacity until the loop
      // restarts. loopBack() is what clears the set.
      setActiveKeys((prev) => {
        const next = new Set(prev);
        for (const u of step.updates) next.add(u.key);
        return next;
      });

      const start = performance.now();
      const tick = () => {
        const t = Math.min((performance.now() - start) / ANIM_DURATION_MS, 1);
        const eased = easeOutCubic(t);
        setP((prev) => {
          const next = { ...prev };
          for (const u of step.updates) {
            const from = startVals[u.key]!;
            (next[u.key] as number) = from + (u.to - from) * eased;
          }
          return next;
        });
        if (t < 1) {
          rafRef.current = requestAnimationFrame(tick);
        } else {
          rafRef.current = null;
          stepIndex += 1;
          if (stepIndex < ANIM_STEPS.length) {
            timeoutRef.current = window.setTimeout(runStep, ANIM_GAP_MS);
          } else {
            timeoutRef.current = window.setTimeout(
              loopBack,
              ANIM_FINAL_PAUSE_MS,
            );
          }
        }
      };
      rafRef.current = requestAnimationFrame(tick);
    };

    const loopBack = () => {
      // Tween every param back to INITIAL_PARAMS while dimming the Tweak
      // entries 1 → 0.3 in lockstep — that's the only reset cue. The JP
      // simulation stays visible throughout; viewers see the live glyphs
      // morph back to the raw "before" state alongside the number countdown.
      setActiveKeys(new Set());
      const startVals = { ...pRef.current };
      const start = performance.now();
      const tween = () => {
        const t = Math.min((performance.now() - start) / RESET_TWEEN_MS, 1);
        const eased = easeOutCubic(t);
        setP({
          wght: startVals.wght + (INITIAL_PARAMS.wght - startVals.wght) * eased,
          scale:
            startVals.scale + (INITIAL_PARAMS.scale - startVals.scale) * eased,
          tracking:
            startVals.tracking +
            (INITIAL_PARAMS.tracking - startVals.tracking) * eased,
          trackingKana:
            startVals.trackingKana +
            (INITIAL_PARAMS.trackingKana - startVals.trackingKana) * eased,
          paltAmount:
            startVals.paltAmount +
            (INITIAL_PARAMS.paltAmount - startVals.paltAmount) * eased,
          yShift:
            startVals.yShift +
            (INITIAL_PARAMS.yShift - startVals.yShift) * eased,
        });
        if (t < 1) {
          rafRef.current = requestAnimationFrame(tween);
        } else {
          rafRef.current = null;
          stepIndex = 0;
          // Hold the rewinded initial state for RESET_HOLD_MS before
          // launching the next pass.
          timeoutRef.current = window.setTimeout(runStep, RESET_HOLD_MS);
        }
      };
      rafRef.current = requestAnimationFrame(tween);
    };

    timeoutRef.current = window.setTimeout(runStep, ANIM_INITIAL_PAUSE_MS);
  }, [cancelAnimation]);

  // Auto-play once on mount; clean up if the component unmounts mid-animation.
  useEffect(() => {
    playAnimation();
    return cancelAnimation;
  }, [playAnimation, cancelAnimation]);

  const reset = () => {
    cancelAnimation();
    setP(DEFAULT_PARAMS);
  };

  const replay = () => {
    cancelAnimation();
    setP(INITIAL_PARAMS);
    // Schedule next tick so React commits the reset before the rAF loop
    // starts reading the "from" values out of INITIAL_PARAMS again.
    timeoutRef.current = window.setTimeout(() => playAnimation(), 0);
  };

  return (
    <section className="composition">
      <SectionHead name="Composition" />

      <hr className="composition__separator" />

      <div className="composition__body">
        {SHOW_TUNER && (
          <div className="comp-tuner">
            <SliderRow
              label="wght"
              min={100}
              max={900}
              step={1}
              value={p.wght}
              display={p.wght.toFixed(0)}
              onChange={(v) => setField("wght", v)}
            />
            <SliderRow
              label="scale"
              min={0.7}
              max={1.1}
              step={0.005}
              value={p.scale}
              display={`${(p.scale * 100).toFixed(1)}%`}
              onChange={(v) => setField("scale", v)}
            />
            <SliderRow
              label="tracking"
              min={-50}
              max={150}
              step={1}
              value={p.tracking}
              display={p.tracking.toFixed(0)}
              onChange={(v) => setField("tracking", v)}
            />
            <SliderRow
              label="trackingKana"
              min={-50}
              max={150}
              step={1}
              value={p.trackingKana}
              display={p.trackingKana.toFixed(0)}
              onChange={(v) => setField("trackingKana", v)}
            />
            <SliderRow
              label="yShift"
              min={-50}
              max={150}
              step={1}
              value={p.yShift}
              display={p.yShift.toFixed(0)}
              onChange={(v) => setField("yShift", v)}
            />
            <SliderRow
              label="palt"
              min={0}
              max={1}
              step={0.01}
              value={p.paltAmount}
              display={p.paltAmount.toFixed(2)}
              onChange={(v) => setField("paltAmount", v)}
            />
            <div className="comp-tuner__buttons">
              <button
                type="button"
                className="comp-tuner__btn"
                onClick={replay}
              >
                ▶ play
              </button>
              <button type="button" className="comp-tuner__btn" onClick={reset}>
                reset
              </button>
            </div>
          </div>
        )}

        <div className="composition__grid">
          <span className="composition__label">
            ベース欧文書体
            <br />
            Inter
          </span>
          <div className="composition__group">
            <div className="composition__sub-group composition__sub-group--jp">
              {GROUP_LINES_TOP.map((y) => (
                <hr
                  key={y}
                  className="composition__group-line"
                  style={{ top: `${y / 16}rem` }}
                />
              ))}
              <span
                className="composition__group-text composition__group-text--left composition__group-text--reference"
                aria-hidden="true"
              >
                書体
              </span>
              <CompositionGlyphText
                className="composition__group-text composition__group-text--left composition__group-text--svg"
                shapeKey="jp-type"
                ariaLabel="書体"
                weight={p.wght}
                scale={p.scale}
                tracking={p.tracking}
                trackingKana={p.trackingKana}
                paltAmount={p.paltAmount}
                yShift={p.yShift}
              />
            </div>
            <div className="composition__sub-group composition__sub-group--latin">
              {GROUP_LINES_TOP.map((y) => (
                <hr
                  key={`la${y}`}
                  className="composition__group-line"
                  style={{ top: `${y / 16}rem` }}
                />
              ))}
              <CompositionGlyphText
                className="composition__group-text composition__group-text--right composition__group-text--svg"
                shapeKey="latin-type"
                ariaLabel="Type"
                weight={400}
                scale={1}
                tracking={0}
                trackingKana={0}
                paltAmount={0}
                yShift={0}
              />
            </div>
          </div>

          <span className="composition__label">
            和文書体
            <br />
            Noto Sans JP
          </span>
          <div className="composition__group">
            <div className="composition__sub-group composition__sub-group--jp">
              {GROUP_LINES_BOTTOM.map((y) => (
                <hr
                  key={`b${y}`}
                  className="composition__group-line"
                  style={{ top: `${y / 16}rem` }}
                />
              ))}
              <span
                className="composition__group-text composition__group-text--left composition__group-text--reference"
                aria-hidden="true"
              >
                デザイン
              </span>
              <CompositionGlyphText
                className="composition__group-text composition__group-text--left composition__group-text--svg"
                shapeKey="jp-design"
                ariaLabel="デザイン"
                weight={p.wght}
                scale={p.scale}
                tracking={p.tracking}
                trackingKana={p.trackingKana}
                paltAmount={p.paltAmount}
                yShift={p.yShift}
              />
            </div>
            <div className="composition__sub-group composition__sub-group--latin">
              {GROUP_LINES_BOTTOM.map((y) => (
                <hr
                  key={`lb${y}`}
                  className="composition__group-line"
                  style={{ top: `${y / 16}rem` }}
                />
              ))}
              <CompositionGlyphText
                className="composition__group-text composition__group-text--right composition__group-text--svg"
                shapeKey="latin-design"
                ariaLabel="Design"
                weight={400}
                scale={1}
                tracking={0}
                trackingKana={0}
                paltAmount={0}
                yShift={0}
              />
            </div>
          </div>
        </div>

        <p className="composition__tweak">
          Tweak／
          <br />
          {/* Trailing comma sits inside each span so it inherits that
              entry's opacity — keeps the punctuation in sync with the value
              it follows as params light up one by one. lang="en" pushes
              Safari to use latn/dflt where tnum is reliably present. */}
          <span
            lang="en"
            className={`composition__tweak-param${activeKeys.has("scale") ? " is-active" : ""}`}
          >
            scale: {(p.scale * 100).toFixed(1)}%,
          </span>{" "}
          <span
            lang="en"
            className={`composition__tweak-param${activeKeys.has("yShift") ? " is-active" : ""}`}
          >
            yShift: {p.yShift.toFixed(0)},
          </span>{" "}
          <span
            lang="en"
            className={`composition__tweak-param${activeKeys.has("wght") ? " is-active" : ""}`}
          >
            wght: {p.wght.toFixed(0)},
          </span>{" "}
          <span
            lang="en"
            className={`composition__tweak-param${activeKeys.has("paltAmount") ? " is-active" : ""}`}
          >
            palt: {p.paltAmount.toFixed(2)},
          </span>{" "}
          <span
            lang="en"
            className={`composition__tweak-param${activeKeys.has("tracking") || activeKeys.has("trackingKana") ? " is-active" : ""}`}
          >
            tracking: {p.tracking.toFixed(0)}–{p.trackingKana.toFixed(0)}
          </span>
        </p>
      </div>
    </section>
  );
}
