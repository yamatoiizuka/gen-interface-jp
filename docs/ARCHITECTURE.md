# Gen Interface JP — Architecture

## Overview

Gen Interface JP is a font build pipeline (no app, no UI). A `make` target
turns vendor sources into distributable TTF / WOFF2 / npm artifacts. Each
weight passes through three Python stages, then a static demo site
consumes the published webfont package.

```
┌────────────────────────────────────────────────────────────────────┐
│  Source                                                            │
│    vendor/fonts/Inter-4.1/extras/ttf/Inter-{Weight}.ttf            │
│    vendor/fonts/Inter-4.1/extras/ttf/InterDisplay-{Weight}.ttf     │
│    vendor/fonts/Inter-4.1/InterVariable.ttf                        │
│    vendor/fonts/Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  font/build.py  (per family × per weight)       │
        │                                                 │
        │   [1/3] Bake — font-baker, base-only            │
        │         Noto wght axis → static TTF             │
        │         metadataMode=inheritBase                │
        │         output.upm=2048                         │
        │             ↓                                   │
        │   [2/3] Proportionalise — proportional.py       │
        │      palt → hmtx + yakumono ss09                │
        │      BASE VertAxis for vertical Latin alignment │
        │         tracking (with repeatable skips)        │
        │         + strip extreme bbox                    │
        │             ↓                                   │
        │   [3/3] Merge — font-baker                      │
        │         Inter (sub) + proportional Noto (base)  │
        │         subFont.excludeCodepoints keeps         │
        │         CJK-conventional symbols on Noto        │
        │         output.upm=2048, metricsSource=sub      │
        │         project version + manufacturer stamp    │
        │         normalize GSUB/GPOS coverage order      │
        │             ↓                                   │
        │   dist/ttf/  (one TTF per family × weight)      │
        │                                                  │
        └─────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  webfont/build.py — unicode-range subsetting    │
        │     google-japanese strategy (default) →        │
        │     all.css + per-weight CSS + WOFF2 chunks     │
        │     dist/webfont/gen-interface-jp/              │
        └─────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  release/build.py — packaging                   │
        │     dist/release/github/   → GitHub Releases    │
        │     dist/release/npm/      → npm publish        │
        │     dist/release/webfonts/ → GitHub Pages       │
        └─────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  site/  — Vite static demo                      │
        │     loads webfont via jsDelivr (npm CDN)        │
        │     deployed to GitHub Pages                    │
        └─────────────────────────────────────────────────┘
```

## Data Flow

### 1. Build a Font Weight (`font.build`)

```
For each (family, weight) in FAMILIES × WEIGHTS:
  → choose Inter source:
      - ExtraLight through Bold use vendor static Inter/InterDisplay TTFs
      - Thin and ExtraBold are instantiated from InterVariable.ttf:
          Gen Interface JP         : opsz=14, wght=125 / 775
          Gen Interface JP Display : opsz=32, wght=125 / 775
        The generated static instances are stamped back to usWeightClass
        100 / 800 so output metadata follows the public weight names, not
        the internal wght-axis coordinates.
  → font-baker bake: variable Noto → static TTF
                    inheritBase passes designer/OFL/version
                    through; only weight is stamped
  → reload inst, read palt/vpal from cached variable font
    and scale those 1000-UPM records to the active 2048-UPM grid
  → bake Noto palt at full strength except ss09 yakumono,
    whose palt is split into a 34% baked base + 66% ss09 residual
    (glyphs without palt keep metrics)
  → make_proportional bakes palt → hmtx and removes palt/vpal/halt/vhal;
    horizontal palt residuals and vertical vpal records are held for
    final ss09 alternates
  → _apply_tracking widens advances + half-balances LSB
    except repeatable no-gap symbols in family["trackingIgnore"]
  → _apply_glyph_spacing applies family["glyphSpacing"] sidebearing tweaks
  → _strip_extreme_glyphs neutralises vertical iteration marks 〱-〵
    (1000-UPM design thresholds: yMax > 1200 / yMin < -400)
  → font-baker merge: Inter source + proportional Noto
                     subFont.excludeCodepoints = SUB_EXCLUDE_CODEPOINTS
                     keeps CJK-conventional symbols on Noto;
                     font-baker also auto-renames glyph-name collisions
                     (e.g. Inter U+0298 vs Noto U+25CE both `uni25CE`)
                     family/weight stamped to "Gen Interface JP …"
                     output.upm=2048 preserves Inter's native grid
                     metricsSource=sub anchors hhea on Inter
                     project version stamped into name metadata
                     manufacturer / manufacturerURL stamped
  → reload/save final TTF once so GSUB/GPOS coverage tables are ordered
    by the final merged glyph IDs
  → install yakumono-only ss09 against the final cmap / glyph names so
    merge-time glyph renames keep optional yakumono behavior on both
    horizontal and vertical glyphs
  → install vertical-only Latin/digit centering alternates under vert/vrt2
    so upright ASCII in vertical text uses the CJK column center
```

### 2. Subset for the Web (`webfont.build`)

```
Read dist/ttf/{family}/{family}-{weight}.ttf
  → planner picks strategy:
      google-japanese (default) — replays Google Fonts' Japanese
                                  unicode-range slices (~120 chunks)
      gen              — hand-tuned slice plan
  → for each (family × weight × slice):
      fontTools.subset → WOFF2 chunk
      .nam file → human-readable codepoint list
  → emit @font-face per slice with `unicode-range:` guard
  → write all.css (full family) + per-weight CSS files
    using relative ./w/... WOFF2 URLs for npm/self-hosting
  → manifest.json with sizes / brotli sizes for benchmarking
```

### 3. Package & Publish (`release.build`)

```
require dist/ttf/ + dist/webfont/gen-interface-jp/
  → zip GenInterfaceJP-<version>.zip (TTF, all weights × both families)
  → copy webfont package → npm/      (with package.json + README.md)
  → copy webfont package → webfonts/ (Pages-hosted mirror)
  → add cdn/*.css entrypoints with version-pinned jsDelivr WOFF2 URLs
  → manifest.json with version, tag, asset URLs
```

## Build Pipeline (`font/build.py`)

### Stage 1 — Bake variable to static

font-baker runs in base-only mode against Noto Variable. The wght axis is
pinned to the family's per-weight value (off-grid: e.g. `465` for Regular,
`690` for SemiBold — Noto's axis is non-linear and stems read lighter than
Inter at the round positions). `output.metadataMode = "inheritBase"` keeps
Noto's identity records (designer, OFL, manufacturer, version) intact, so
the inst TTF carries clean source metadata into Stage 2 without manual
save/restore.

`output.upm = 2048` is set at this first stage. Noto's source is 1000 UPM,
but Inter / Inter Display are native 2048 UPM; moving the Noto intermediate
onto Inter's grid before project-owned spacing work avoids rounding Inter
down to 1000 UPM in the final merge. font-baker is expected to scale the
full glyph order here, including unmapped vertical alternates that can still
carry palt records.

### Stage 2 — Proportionalise + tune metrics

Four sub-passes, all in-place on the inst:

1. **palt baking / yakumono ss09** (`proportional.make_proportional`) — palt values are
   read from the cached variable font (instantiation can corrupt palt's
   ValueRecords at non-default axis positions), then scaled from Noto's
   1000-UPM source grid to the active 2048-UPM build grid.
   XPlacement/XAdvance pairs are added to LSB / advance, outlines shifted.
   Most Noto palt entries are
   baked at full strength, but yakumono listed in `PALT_FEATURE_CHARS`
   is split: 34% of the palt adjustment is baked into `hmtx` so the glyph is
   still tightened by default, and the remaining 66% is held for a final
   yakumono-only `ss09` stylistic set named "約物半角". For Noto's common
   `XAdvance=-500` yakumono records, that means palt-off gets `-170` baked
   into the base advance and enabling `ss09` applies the remaining `-330`
   before UPM scaling. Runtime `vpal` is not reinstalled as a feature; its
   selected yakumono records are kept as source data for vertical `.ss09`
   alternates whose `vmtx` carries the vertical placement / advance delta.
   Final fonts strip `palt`, `vpal`, `halt`, and `vhal` so optional yakumono
   spacing is exposed through `ss09` only. Glyphs without palt entries are not
   automatically sidebearing-squeezed; they keep their original
   `hmtx` unless a later explicit spacing rule touches them.
   `U+30FB` (・) is split from Noto's shared `uni2027` glyph before palt
   baking so `U+2027` (‧) and `U+30FB` (・) can carry separate optional
   spacing records when needed.
2. **Tracking** (`_apply_tracking`) — advance grows by `tracking`;
   `tracking // 2` is added to LSB so the outline sits centred in the
   wider slot. Kana / punctuation get a separate `trackingKana` value
   when set on the family. These constants are authored on the 1000-UPM
   design grid (`+30` / `+40` for the normal family) and scaled to the
   active UPM before application. `trackingIgnore` accepts codepoints / ranges
   and skips glyphs resolved through cmap. The default ignore list keeps
   the Noto tracking stage from widening Box Drawing (`U+2500-U+257F`),
   Block Elements (`U+2580-U+259F`), two-dot / midline leaders
   (`U+2025`, `U+22EF`), halfwidth middle dot (`U+FF65`), `U+3030`,
   vertical/compat leader
   forms (`U+FE19`, `U+FE30-U+FE34`, `U+FE49-U+FE4F`), two-/three-em
   dashes (`U+2E3A`, `U+2E3B`), fullwidth low line (`U+FF3F`), and
   fullwidth macron (`U+FFE3`).
3. **Per-glyph spacing** (`_apply_glyph_spacing`) — manual fallback for
   the rare glyph whose sidebearings still read off after palt + uniform
   tracking. The family's `glyphSpacing` dict maps a codepoint or
   character to a `(lsb_delta, rsb_delta)` pair, authored at 1000 UPM and
   scaled before application: `lsb_delta` increases
   hmtx LSB and advance by the same amount, while `rsb_delta` only grows
   advance on the right. Outline coordinates are never touched. Populate
   sparingly — each entry hand-tuned for one glyph against a specific
   neighbour rhythm. Refer to `FAMILIES` in `font/build.py` for the
   current set of adjustments. Small hiragana / katakana use this layer to
   add explicit margin on both sides after palt, because the small forms
   otherwise read too tight in UI text; most small kana use 15 units per
   side, with a few optical exceptions such as small katakana i (`ィ`),
   small katakana ya (`ャ`), and small hiragana yo (`ょ`). Punctuation,
   including `U+30FB` (・), is intentionally not handled here: it passes
   through tracking and tightens only when the explicit `ss09` feature is
   enabled.
4. **Bbox strip** (`_strip_extreme_glyphs`) — see [Vertical metrics]
   below.

Optional **horizontal scale** (`xScale` family setting, currently unused)
runs after the above when set, condensing CJK in x without touching y.

### Stage 3 — Merge with Inter

font-baker merge mode: Inter is sub, the proportional Noto is base.
`subFont.excludeCodepoints = SUB_EXCLUDE_CODEPOINTS` lists the CJK-conventional
symbols that must keep the Noto outline (`①` `Ⓐ` `※` `◯` …); font-baker
strips those entries from Inter's cmap before the merge so the base glyph
survives. font-baker additionally auto-detects cross-codepoint glyph-name
collisions — Inter's U+0298 (`ʘ`) and Noto's U+25CE (`◎`) both ship under
the glyph name `uni25CE`; rather than letting Inter's outline overwrite
`◎`, font-baker renames the sub glyph to `uni25CE.sub`. Together the two
mechanisms cover both direct overlaps and the trickier name-collision case
without any manual cmap surgery in this project.

`output.upm = 2048` preserves Inter's native coordinate grid in the final TTF,
and `output.metricsSource = "sub"` anchors the merged hhea / OS/2 envelope on
Inter so Latin metrics drive line height. `BASELINE_OFFSET = 25` is a 1000-UPM
design value (built as `51` at 2048 UPM) that nudges Noto up so CJK ideographs
share an optical baseline with Latin caps; `SCALE = 0.925` is still the optical
CJK design scale, not the UPM conversion. It shrinks Noto so a CJK character
lines up in width with Inter's cap-height — a
typographic convention for Latin/CJK pairing where CJK is slightly down-scaled
to feel proportionate.

`output.version` is read from `pyproject.toml` via the shared
`project_metadata.project_version()` helper, so final TTF nameID 5 and
nameID 3 carry the same project/release version as the release zip / npm
package / site metadata. OpenType version comparison only uses the leading
`major.minor` numeric prefix, so a project version like `1.2.3` appears in
name strings while `head.fontRevision` resolves to the numeric `1.2`
prefix. `output.manufacturer = "Yamato Iizuka"`, `output.manufacturerURL =
"https://yamatoiizuka.com"` stamp nameID 8 / 11 on every released TTF.
After the merge, the TTF is reloaded and saved once with fontTools so
GSUB/GPOS coverage tables are sorted against the final merged glyph order.
Then the minimal runtime `ss09` behavior is rebuilt from codepoint-keyed
horizontal records and codepoint/glyph-keyed vertical records captured before
the merge. This matters
for shared Noto glyphs such as `U+FF40` (｀), which uses Noto's `uni2035`
before merge but may be renamed to `uni2035.orig` when Inter also provides
`U+2035`. Because this final install happens after the merge, the saved
residual records are also scaled by `SCALE` so live `ss09` alternates match
the optically scaled Noto base.
The same final-font pass also rebuilds `BASE.VertAxis` from the final CJK
advance width so Illustrator can align Latin in vertical text through BASE.
`BASE.HorizAxis` is never copied or modified by this pass; if a font ever
reaches this step without a BASE table, the build creates one with
`HorizAxis = NULL` and a vertical axis only. The vertical axis uses the
Noto/Hiragino baseline tags (`icfb`, `icft`, `ideo`, `romn`) and scales their
coordinates to the merged font's Noto column width, including the optical Noto
scale and any family tracking already present in the CJK advance.

## Proportional Metrics (`font/proportional.py`)

CJK fonts ship full-width: every glyph occupies the same em-square
regardless of outline width, with `palt` GPOS narrowing kana / Latin
optically at runtime. Apps that don't enable `palt` (Adobe's Japanese
composer, browser fallbacks, anything treating CJK as monospaced for
layout) miss those live adjustments. Gen Interface JP avoids a fully
monospaced fallback by baking most palt values directly into `hmtx`, and by
baking a reduced palt fraction for optional yakumono.

`make_proportional` bakes most `palt` values into the static `hmtx` so the
font reads proportional everywhere. When `runtime_palt` is provided together
with `runtime_palt_base_scale`, the build bakes that fraction of each runtime
record into the base metrics. For the production build, `install_runtime_palt`
is disabled so the residual is not exposed as live `palt`; instead it is
captured by codepoint and later reinstalled as yakumono-only `ss09` after the
Inter merge. The project uses
`RUNTIME_PALT_BASE_SCALE = 0.34`, so palt-off yakumono is already partially
tightened (`-170` for the common `-500` palt advance before UPM scaling) while
enabling `ss09` reaches the former full Noto palt target.
The production build uses `PALT_FEATURE_CHARS` (48 chars) for horizontal
`ss09` targets and `SS09_VERTICAL_FEATURE_CHARS` plus
`SS09_VERTICAL_FEATURE_GLYPHS` for vertical `ss09` targets. The latter uses
Noto's `vpal` values as source data but bakes them into alternate glyph
metrics instead of exposing a runtime `vpal` feature. This avoids
double-applying palt to baked kana / Latin while consolidating optional
yakumono spacing under `ss09`. Only TrueType outlines are supported
— palt baking writes back to `glyf`, not CFF.
Because the Inter merge can rename colliding base glyphs, the build captures
horizontal residuals by codepoint and vertical adjustments by codepoint or
glyph name before the merge, then retargets them against the final cmap /
glyph order afterward. Those final records are scaled by the final Noto
optical scale (`SCALE = 0.925`) at install time only; the pre-merge
`palt_data` / `vpal_data` remain on the active UPM grid so baked base metrics
are scaled exactly once by the merge.

`_remove_prop_features` walks GPOS in two coordinated passes:
FeatureRecord deletions and the corresponding LangSys index remap.
Removing a record changes the indices of every later record, so each
LangSys's `FeatureIndex` array is re-keyed against the surviving records.
After feature removal, lookup indices are pruned only when no surviving
feature references them; shared lookups stay intact. `kern` remains in GPOS,
and horizontal optional yakumono is
handled as GSUB: `_install_ss09_punctuation_feature` creates `.ss09` metric
alternates from the former palt residual and installs an `ss09` stylistic set
with the UI name "約物半角". For vertical glyphs, it creates `.ss09`
alternates whose `vmtx` bakes the former vpal YPlacement/YAdvance. Existing
PairPos kerning is extended to those alternates so `kern` continues to apply
after substitution. Horizontal-only alternates copy their source glyphs'
`vmtx` records so saved fonts keep vertical metrics table length in sync with
the expanded glyph order. `_install_vertical_base_axis` then replaces only
`BASE.VertAxis` for vertical Latin alignment. This avoids Latin-specific
GSUB alternates and keeps horizontal metrics untouched; existing
`BASE.HorizAxis` is left as-is. After `ss09` is
appended, the GSUB `FeatureList` is sorted back into `FeatureTag` order and
all LangSys feature indices are remapped; lookup order is left untouched.

## Vertical Metrics & the Illustrator Box Problem

### Background

In Illustrator, any font with CJK glyphs is forced through the **Japanese
composer**, which fixes leading at `pt × ≈170%`. The Latin-only behaviour
of Inter — line height adjusted per-line based on glyph extents — is not
controllable from the font side.

But Illustrator's **text frame auto-sizing** reads `head.yMax` /
`head.yMin`, so shrinking the head bbox does shrink the vertical padding
of new text frames.

### Stripped glyphs

`_strip_extreme_glyphs` neutralises vertical-text iteration marks
`U+3031-U+3035` explicitly, plus any glyph with `yMax > 1200` or
`yMin < -400` on the 1000-UPM design grid. The thresholds are scaled to the
active font UPM before comparison (about `yMax > 2458` / `yMin < -819` in the
2048-UPM build). In Noto Sans JP the bbox outliers are
the full vertical iteration marks and their `vert` / `vrt2` alternates;
the upper/lower-half remnants (`〳〴〵`) are removed by codepoint because
they are vertical-only and can confuse Adobe's Japanese composer in
horizontal text.

| Glyph | Codepoint | Reason |
|---|---|---|
| `uni3031` 〱 | U+3031 | Vertical kana repeat mark |
| `uni3032` 〲 | U+3032 | Vertical voiced repeat mark |
| `uni3033` 〳 | U+3033 | Vertical kana repeat mark upper half |
| `uni3034` 〴 | U+3034 | Vertical voiced repeat mark upper half |
| `uni3035` 〵 | U+3035 | Vertical kana repeat mark lower half |
| (vert alternate) | (unmapped) | `uni3031` rotated form |
| (vert alternate) | (unmapped) | `uni3032` rotated form |

Outlines are emptied rather than the slots removed, so GSUB / GPOS
indices stay valid; cmap entries are dropped so typing the codepoints
falls through to .notdef.

| | yMin / yMax | span |
|---|---|---|
| Before (vanilla Noto) | -1047 / +1807 | 2.85×em |
| After (final 2048 UPM) | ~-660 / +2269 | ~1.43×em (Inter-equivalent) |

### Design choice — UI font, horizontal only

This font is a **horizontal-only UI / body face**.

- **Vertical typesetting / classical Japanese composition: not supported.**
- Strict em-square compliance (Hiragino-style hhea = 880 / -120) is not
  pursued — `metricsSource: "sub"` inherits Inter's ratio (~1.21×em) so
  Vietnamese / accented Latin (~1.11×em) doesn't clip.
- The trade-off is accepted: head bbox is trimmed for Illustrator
  ergonomics; iteration marks 〱〲 won't render in this font and that's
  fine for UI text.

## Webfont Subsetting (`webfont/build.py`)

The TTF/WOFF2 outputs from `font.build` are too large to load whole on
the web (~5 MB WOFF2 per weight). `webfont.build` slices each weight
along Unicode ranges and emits one `@font-face` rule per slice with a
`unicode-range:` guard. Browsers download only the chunks the page text
references.

### Strategies

- **`google-japanese`** *(default)* — replays Google Fonts' Japanese
  slicing strategy (`vendor/nam-files/slices/japanese_default.txt`).
  Same chunk boundaries as Google's hosted Noto, so coverage and cache
  behaviour match what users already encounter on most Japanese sites.
- **`gen`** — hand-tuned plan: Latin / kana / punct / JIS row 16-92 /
  remaining Han split into `extra_han_slices` even chunks.

### Outputs

```
dist/webfont/gen-interface-jp/
  all.css                # all weights × both families
  400.css                # normal Regular (one per weight)
  display-400.css        # display Regular (one per weight)
  ...
  w/{family}/{weight}/{slice}.woff2
  nam/{slice}.nam        # human-readable codepoint list
  manifest.json          # sizes / brotli sizes per slice
```

These source webfont CSS files intentionally use relative `./w/...`
WOFF2 URLs. `release.build` keeps them for npm installs and self-hosting,
then writes `cdn/*.css` files whose WOFF2 URLs are absolute,
version-pinned jsDelivr URLs:

```
dist/release/npm/
  all.css
  cdn/all.css
  400.css
  cdn/400.css
  display-400.css
  cdn/display-400.css
  README.md              # documents CDN vs self-host entrypoints
```

`benchmark.mjs` (Node) replays a throttled fetch against the local
subsets to validate the slicing pays off vs. a full single-file WOFF2.
The benchmark generates the full WOFF2 from the Regular TTF on demand
(via `webfont.build` without `--all`); it is not part of the release
artifact set.

## Release Packaging (`release/build.py`)

Three downstream consumers, three outputs:

- **GitHub Releases** (`dist/release/github/`) — single
  `GenInterfaceJP-<version>.zip` containing all 16 TTFs (both families ×
  8 weights). The asset filename embeds the version so each release is
  linkable unambiguously even after a newer "latest" lands. Full
  single-file WOFF2 is intentionally not redistributed — web delivery
  flows through the npm subset package below; self-hosters can convert
  TTF→WOFF2 trivially with fontTools.
- **npm package** (`dist/release/npm/`) — webfont subsets + a generated
  `package.json` (name, version, files, OFL-1.1 license) and `README.md`.
  Plain `all.css` / per-weight CSS use relative WOFF2 URLs for npm
  installs and self-hosting. The `cdn/*.css` files use absolute,
  version-pinned jsDelivr WOFF2 URLs for direct CDN linking.
- **GitHub Pages mirror** (`dist/release/webfonts/gen-interface-jp/`) —
  static mirror of the webfont CSS / WOFF2 layout, served alongside the
  demo site.

Version is read from `pyproject.toml` (or `GITHUB_REF_NAME` in CI). The
font build also reads `pyproject.toml` through the same shared metadata helper
when stamping final TTF version records, so font bodies and release artifacts
do not diverge. The `manifest.json` next to the github / npm / webfonts dirs
records release URLs for downstream tooling.

## Site (`site/`)

Vite static site under `site/`. Loads the published webfont package via
jsDelivr's npm CDN at runtime — i.e. the live site uses the same npm
artifact a third-party consumer would, exercising the package end-to-end.
It uses the `cdn/*.css` entrypoints because the site links directly to
jsDelivr.
GitHub Pages deploys the build via `.github/workflows/pages.yml`.

## Tests

```bash
PYTHONPATH=src python3 -m pytest        # full suite (~35s)
```

Tests live under `tests/`, split by surface:

- **`tests/conftest.py`** — shared fixtures: a session-cached subset of
  Noto Variable for tests that need real palt / vpal / vert / cmap data, and a
  hand-built minimal TrueType (`FontBuilder`) for whole-font mutation
  tests where 17 000 Noto glyphs would be wasteful.
- **`tests/test_font_build.py`** — UPM design-unit scaling, project-version
  metadata forwarding
  (`SOURCE_UPM = 1000`, `TARGET_UPM = 2048`), `_glyph_codepoint`, `_is_kana_or_punct`,
  `_is_cjk_codepoint`, `_is_kana_letter`, `_get_cjk_glyphs`,
  `_get_vert_alternates`, `_apply_x_scale`, `_strip_extreme_glyphs`,
  `_apply_tracking`, `_apply_glyph_spacing`, `_glyphs_for_codepoints`,
  `_split_cmap_codepoint_glyph`, `_get_variable_palt`,
  explicit palt/ss09 spacing policy, and InterVariable edge-instance
  source selection for Thin / ExtraBold.
- **`src/font/verify_edge_instances.py`** — post-build verification for
  Thin / ExtraBold edge outputs. Confirms the generated InterVariable static
  instances keep the vendor static cmap / GSUB / GPOS surface, contain no
  variation tables, use the requested `wght` / `opsz` coordinates, and that
  final TTF metadata stays at public weights 100 / 800 without leaking
  InterVariable axis names.
- **`tests/test_proportional.py`** — `_read_palt`, `_read_vpal`,
  `_shift_glyph_x`, `_remove_prop_features`, `make_proportional` (palt
  baking, optional runtime-palt reinstall, runtime-palt base/residual splitting,
  reduced palt scaling, non-palt metric preservation, optional squeeze SB sidebearing math, palt_override
  precedence, CFF rejection, LangSys index validity post-removal), plus
  `ss09` construction, BASE VertAxis construction, and HarfBuzz shaping.
- **`tests/test_release.py`** — public distribution contracts: GitHub
  asset URL shape (referenced by the site download button), npm package
  layout including `files` glob, generated README, `cdn/*.css` entrypoints,
  and license metadata that jsDelivr / `npm publish` consume.
- **`tests/test_webfont_build.py`** — codepoint range merging, unicode-range
  formatting, JIS row → codepoint mapping, subset plan non-overlap +
  per-bucket placement, Google-Japanese strategy parsing including the
  closing-brace-inside-comment edge case.

| File | Tests | Verifies |
|---|---|---|
| `test_font_build.py` | 108 | UPM scaling policy, project-version metadata forwarding, glyph-name parsing, kana / CJK classification, GSUB/GPOS walk, x-scale, bbox strip, tracking, ss09 feature retargeting, final runtime feature scaling, InterVariable edge-instance compatibility |
| `test_proportional.py` | 40 | palt/vpal extraction, glyph translation, GPOS feature removal, runtime-palt/vpal helper coverage + base/residual split + optional squeeze helper, ss09 construction, BASE VertAxis construction |
| `test_release.py` | 2 | GitHub asset URL contract, npm package layout (files glob, license, README, self-host/CDN CSS entrypoints at root) |
| `test_webfont_build.py` | 42 | Range merge / dedup, unicode-range formatting incl. 5-digit, JIS row mapping, subset plan placement / non-overlap / coverage, strategy parser edge cases |

## Commands

| Command | Purpose |
|---|---|
| `make font` | Build TTF for both families × all weights |
| `make verify-edge-instances` | Build Thin / ExtraBold and verify InterVariable edge instance + final TTF metadata |
| `make webfont` | Build unicode-range subsets (depends on `font`) |
| `make release` | Build GitHub zips + npm + Pages package (depends on `webfont`) |
| `make webfont-benchmark` | Throttled fetch benchmark of slicing strategy |
| `make npm-pack` | Dry-run npm package inspection |
| `make npm-publish` | Publish to npm |
| `make site` | Build the demo site (`site/dist/` doubles as the GitHub Pages artifact) |
| `make serve` | Local Vite dev server for the site |
| `make clean` | Remove `dist/` and `site/dist/` |
| `python3 -m font.build [family] [weight ...]` | Build a slice (e.g. `normal Regular`) |
| `python3 -m font.verify_edge_instances` | Verify built Thin / ExtraBold edge outputs |
| `python3 -m pytest` | Run the test suite |

CI: `.github/workflows/pages.yml` deploys the demo site to GitHub Pages
on every push to `main`. Release packaging is run locally — see
`src/release/README.md` for the `make release` + `gh release upload`
flow.

## Dependencies

### Python

- `ofl-font-baker` (>= 0.4.5) — Composite font merge engine. Inherits
  base/sub identity records via `metadataMode`. Drives Stage 1 (bake)
  and Stage 3 (merge) of the build pipeline. 0.4.0 added
  `subFont.excludeCodepoints` and glyph-name collision rename, used by
  the merge step to keep CJK-conventional symbols on Noto. 0.4.1
  preserves vertical metrics (`vmtx` / `VORG`) and `vert` / `vrt2`
  GSUB mappings when glyphs are renamed or duplicated during the merge,
  so vertical typesetting of overridden glyphs continues to land at the
  base font's intended position. 0.4.5 adds `output.upm`, used by both
  the Noto bake and final merge stages to keep the pipeline on the 2048 UPM
  Inter grid.
- `fonttools` (>= 4.47.0) — Font parsing, instancer, subsetter, GPOS / GSUB
  table editing.
- `freetype-py` — Used by tooling around metrics inspection.
- `brotli` — WOFF2 compression (transitive via fontTools).
- `pytest` — Test runner.

### Node.js (site only)

- `vite` — Build tool / dev server.
- The site has no dependency on the webfont source — it loads the
  published npm package via jsDelivr.

## Maintaining This Document

Update this file (and `ARCHITECTURE.ja.md`) whenever a change touches:

- the build pipeline shape (stage boundaries, file outputs, intermediate
  artefacts)
- the proportional / tracking / bbox-strip policy
- the webfont subsetting strategy or output layout
- the release packaging surface (zip names, npm package shape, manifest
  fields)
- test infrastructure (fixtures, file split)
- vendor dependencies or CI workflow steps

Keeping the doc in sync with the code is part of the change, not a
follow-up.
