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
│    vendor/fonts/Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  font/build.py  (per family × per weight)       │
        │                                                 │
        │   [1/3] Bake — font-baker, base-only            │
        │         Noto wght axis → static TTF             │
        │         metadataMode=inheritBase                │
        │             ↓                                   │
        │   [2/3] Proportionalise — proportional.py       │
        │         palt → hmtx (three-bucket policy)       │
        │         tracking + strip extreme bbox           │
        │             ↓                                   │
        │   [3/3] Merge — font-baker                      │
        │         Inter (sub) + proportional Noto (base)  │
        │         subFont.excludeCodepoints keeps         │
        │         CJK-conventional symbols on Noto        │
        │         metricsSource=sub, manufacturer stamp   │
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
  → font-baker bake: variable Noto → static TTF
                    inheritBase passes designer/OFL/version
                    through; only weight is stamped
  → reload inst, read palt from cached variable font
  → split glyphs into kana letters / reduced palt / squeeze SB
    (CJK ideographs and vert-only glyphs excluded)
  → make_proportional bakes palt → hmtx, strips palt/vpal/halt/vhal
  → _apply_tracking widens advances + half-balances LSB
  → _apply_glyph_spacing applies family["glyphSpacing"] sidebearing tweaks
  → _strip_extreme_glyphs neutralises iteration marks 〱〲
    (yMax > 1200 / yMin < -400)
  → font-baker merge: Inter + proportional Noto
                     subFont.excludeCodepoints = SUB_EXCLUDE_CODEPOINTS
                     keeps CJK-conventional symbols on Noto;
                     font-baker also auto-renames glyph-name collisions
                     (e.g. Inter U+0298 vs Noto U+25CE both `uni25CE`)
                     family/weight stamped to "Gen Interface JP …"
                     metricsSource=sub anchors hhea on Inter
                     manufacturer / manufacturerURL stamped
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

### Stage 2 — Proportionalise + tune metrics

Four sub-passes, all in-place on the inst:

1. **palt baking** (`proportional.make_proportional`) — palt values are
   read from the cached variable font (instantiation can corrupt palt's
   ValueRecords at non-default axis positions). XPlacement/XAdvance pairs
   are added to LSB / advance, outlines shifted. Three buckets:
   - **kana letters** — full palt shrink
   - **reduced palt** (punctuation, by default ⅓ scale) — palt glyphs
     that aren't kana letters
   - **squeeze SB** — non-palt, non-kana, non-CJK, non-vertical glyphs;
     LSB and RSB each shrink by `1 - squeeze_sb_scale`
   CJK ideographs and `vert`/`vrt2` alternates are excluded — they keep
   full-width metrics.
2. **Tracking** (`_apply_tracking`) — advance grows by `tracking`;
   `tracking // 2` is added to LSB so the outline sits centred in the
   wider slot. Kana / punctuation get a separate `trackingKana` value
   when set on the family.
3. **Per-glyph spacing** (`_apply_glyph_spacing`) — manual fallback for
   the rare glyph whose sidebearings still read off after palt + uniform
   tracking. The family's `glyphSpacing` dict maps a codepoint or
   character to a `(lsb_delta, rsb_delta)` pair: `lsb_delta` shifts the
   outline right within the slot and grows advance by the same amount,
   `rsb_delta` only grows advance on the right. Outline coordinates are
   never touched. Populate sparingly — each entry hand-tuned for one
   glyph against a specific neighbour rhythm. Refer to `FAMILIES` in
   `font/build.py` for the current set of adjustments.
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

`output.metricsSource = "sub"` anchors the merged hhea / OS/2 envelope on Inter
so Latin metrics drive line height. `BASELINE_OFFSET = 25` nudges Noto up so CJK
ideographs share an optical baseline with Latin caps; `SCALE = 0.925` shrinks
Noto so a CJK character lines up in width with Inter's cap-height — a
typographic convention for Latin/CJK pairing where CJK is slightly down-scaled
to feel proportionate.

`output.manufacturer = "Yamato Iizuka"`, `output.manufacturerURL =
"https://yamatoiizuka.com"` stamp nameID 8 / 11 on every released TTF.

## Proportional Metrics (`font/proportional.py`)

CJK fonts ship full-width: every glyph occupies the same em-square
regardless of outline width, with `palt` GPOS narrowing kana / Latin
optically at runtime. Apps that don't enable `palt` (Adobe's Japanese
composer, browser fallbacks, anything treating CJK as monospaced for
layout) miss those adjustments and lay out at full-width spacing.

`make_proportional` bakes `palt` into the static `hmtx` so the font reads
proportional everywhere, then strips `palt` / `vpal` / `halt` / `vhal` to
prevent apps that *do* honour them from double-applying. Only TrueType
outlines are supported — palt baking writes back to `glyf`, not CFF.

`_remove_prop_features` walks GPOS in two coordinated passes:
FeatureRecord deletions and the corresponding LangSys index remap.
Removing a record changes the indices of every later record, so each
LangSys's `FeatureIndex` array is re-keyed against the surviving records.
Lookup tables themselves are untouched — palt's lookups may also be
referenced by other features we keep, and orphaned lookups are harmless.

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

`_strip_extreme_glyphs` neutralises any glyph with `yMax > 1200` or
`yMin < -400` (em = 1000 baseline). In Noto Sans JP these are exclusively
the vertical-text iteration marks and their `vert` / `vrt2` alternates.

| Glyph | Codepoint | Reason |
|---|---|---|
| `uni3031` 〱 | U+3031 | Vertical kana repeat mark |
| `uni3032` 〲 | U+3032 | Vertical voiced repeat mark |
| (vert alternate) | (unmapped) | `uni3031` rotated form |
| (vert alternate) | (unmapped) | `uni3032` rotated form |

Outlines are emptied rather than the slots removed, so GSUB / GPOS
indices stay valid; cmap entries are dropped so typing the codepoints
falls through to .notdef.

| | yMin / yMax | span |
|---|---|---|
| Before (vanilla Noto) | -1047 / +1807 | 2.85×em |
| After | ~-319 / +1108 | ~1.43×em (Inter-equivalent) |

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
`manifest.json` next to the github / npm / webfonts dirs records release
URLs for downstream tooling.

## Site (`site/`)

Vite static site under `site/`. Loads the published webfont package via
jsDelivr's npm CDN at runtime — i.e. the live site uses the same npm
artifact a third-party consumer would, exercising the package end-to-end.
It uses the `cdn/*.css` entrypoints because the site links directly to
jsDelivr.
GitHub Pages deploys the build via `.github/workflows/pages.yml`.

## Tests

```bash
PYTHONPATH=src python3 -m pytest        # full suite (~0.6s)
```

Tests live under `tests/`, split by surface:

- **`tests/conftest.py`** — shared fixtures: a session-cached subset of
  Noto Variable for tests that need real palt / vert / cmap data, and a
  hand-built minimal TrueType (`FontBuilder`) for whole-font mutation
  tests where 17 000 Noto glyphs would be wasteful.
- **`tests/test_font_build.py`** — `_glyph_codepoint`, `_is_kana_or_punct`,
  `_is_cjk_codepoint`, `_is_kana_letter`, `_get_cjk_glyphs`,
  `_get_vert_alternates`, `_apply_x_scale`, `_strip_extreme_glyphs`,
  `_apply_tracking`, `_get_variable_palt`.
- **`tests/test_proportional.py`** — `_read_palt`, `_shift_glyph_x`,
  `_remove_prop_features`, `make_proportional` (palt baking, reduced
  palt scaling, squeeze SB sidebearing math, palt_override precedence,
  CFF rejection, LangSys index validity post-removal).
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
| `test_font_build.py` | 55 | Glyph-name parsing, kana / CJK classification, GSUB walk, x-scale, bbox strip, tracking |
| `test_proportional.py` | 19 | palt extraction, glyph translation, GPOS feature removal, three-bucket policy |
| `test_release.py` | 2 | GitHub asset URL contract, npm package layout (files glob, license, README, self-host/CDN CSS entrypoints at root) |
| `test_webfont_build.py` | 42 | Range merge / dedup, unicode-range formatting incl. 5-digit, JIS row mapping, subset plan placement / non-overlap / coverage, strategy parser edge cases |

## Commands

| Command | Purpose |
|---|---|
| `make font` | Build TTF for both families × all weights |
| `make webfont` | Build unicode-range subsets (depends on `font`) |
| `make release` | Build GitHub zips + npm + Pages package (depends on `webfont`) |
| `make webfont-benchmark` | Throttled fetch benchmark of slicing strategy |
| `make npm-pack` | Dry-run npm package inspection |
| `make npm-publish` | Publish to npm |
| `make site` | Build the demo site (`site/dist/` doubles as the GitHub Pages artifact) |
| `make serve` | Local Vite dev server for the site |
| `make clean` | Remove `dist/` and `site/dist/` |
| `python3 -m font.build [family] [weight ...]` | Build a slice (e.g. `normal Regular`) |
| `python3 -m pytest` | Run the test suite |

CI: `.github/workflows/pages.yml` deploys the demo site to GitHub Pages
on every push to `main`. Release packaging is run locally — see
`src/release/README.md` for the `make release` + `gh release upload`
flow.

## Dependencies

### Python

- `ofl-font-baker` (>= 0.4.1) — Composite font merge engine. Inherits
  base/sub identity records via `metadataMode`. Drives Stage 1 (bake)
  and Stage 3 (merge) of the build pipeline. 0.4.0 added
  `subFont.excludeCodepoints` and glyph-name collision rename, used by
  the merge step to keep CJK-conventional symbols on Noto. 0.4.1
  preserves vertical metrics (`vmtx` / `VORG`) and `vert` / `vrt2`
  GSUB mappings when glyphs are renamed or duplicated during the merge,
  so vertical typesetting of overridden glyphs continues to land at the
  base font's intended position.
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
