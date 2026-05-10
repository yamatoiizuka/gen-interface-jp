# Web Font Delivery

This is a derived pipeline built from the baked Gen Interface JP and Gen
Interface JP Display weights.

It generates a CSS file with `unicode-range` guarded `@font-face` rules and the
corresponding WOFF2 subset files for Japanese web font delivery. The default
Japanese slicing strategy uses the vendored `googlefonts/nam-files` data in
`vendor/nam-files/slices/japanese_default.txt`; it does not inspect Google
Fonts live CSS.

## Build

```bash
make webfont
```

Direct module execution:

```bash
PYTHONPATH=src python3 -m webfont.build --all --clean --jobs 8
```

The default strategy is `google-japanese`. The older JIS row based strategy is
still available for comparison:

```bash
PYTHONPATH=src python3 -m webfont.build --all --clean --strategy jis-row --jobs 8
```

Required inputs:

```text
dist/ttf/Gen Interface JP/*.ttf
dist/ttf/Gen Interface JP Display/*.ttf
```

The published assets are subset WOFF2 files only, but the generator uses TTF
inputs because subsetting from already-compressed WOFF2 is much slower.

Outputs:

```text
dist/webfont/gen-interface-jp/
  all.css
  100.css ... 800.css
  display-100.css ... display-800.css
  w/normal/{100,200,300,400,500,600,700,800}/*.woff2
  w/display/{100,200,300,400,500,600,700,800}/*.woff2
  nam/*.nam
  manifest.json
```

`nam/*.nam` follows the `googlefonts/nam-files` machine-readable style: one
`0x...` codepoint per line. With `google-japanese`, the build intersects the
120 slices from `japanese_default.txt` with the Gen Interface JP cmap, then
places supported codepoints outside that strategy into `google-japanese-extra-*`
subsets.

## Loading

For direct jsDelivr loading, use the `cdn/*.css` entrypoints emitted by
the release package. They contain absolute WOFF2 URLs pinned to the
package version:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/all.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/400.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/display-400.css">
```

For npm installs and self-hosting, use the plain `.css` files. They keep
relative `./w/...` URLs so the CSS and `w/` directory can be copied or
bundled together without depending on jsDelivr.

Do not preload individual subset WOFF2 files. That would bypass the
`unicode-range` behavior that lets the browser fetch only the ranges used by
the page text. If preloading is needed, preload the CSS only:

```html
<link rel="preload" as="style" href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/all.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/all.css">
```

## Benchmark

The benchmark starts a local HTTP server and a headless Chrome instance via
CDP. The server adds configurable latency and bandwidth throttling to font
responses. Results are written to `dist/webfont/benchmark/*.json`.

```bash
node src/webfont/benchmark.mjs --runs 3 --latency 80 --kbps 1600
```

Profiles:

- `site`: short Japanese text similar to a typical website
- `novel`: long-form Japanese text that touches many kanji subsets

Modes:

- `subset`: subset CSS with `unicode-range`
- `full`: one unsubsetted WOFF2

If Chrome cannot be detected automatically:

```bash
CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
node src/webfont/benchmark.mjs
```

References:

- https://github.com/googlefonts/nam-files
- https://github.com/googlefonts/nam-files/tree/main/slices
- https://developer.mozilla.org/docs/Web/CSS/%40font-face/unicode-range
