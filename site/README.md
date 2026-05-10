# Site

This is the landing and font inspection site for Gen Interface JP. It covers
weight and family comparisons, reading samples, HarfBuzz shaping checks, and
the GitHub Release download entry point.

The site loads the published web font CSS from npm through jsDelivr:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/all.css">
```

Local `dist/ttf/` outputs are not required for site development or
builds. The tiny TTF files in `site/public/_font-subsets/` are static
assets used as build-time inputs for the Composition section's generated
SVG shape data. HarfBuzz/WASM is not loaded by the browser.

## Development

Run from the repository root:

```bash
make serve
```

This only starts the Vite development server. Font display depends on the
published npm CSS.

To run directly inside `site/`:

```bash
npm run dev
```

Local `dist/` outputs are not required.

## Build

```bash
make site
```

`npm run build` regenerates `site/src/generated/compositionShapes.ts` from the
small checked-in TTF subsets before Vite bundles the site.

`make site` writes the Vite static build to `site/dist/`, which is also the
GitHub Pages artifact uploaded by `.github/workflows/pages.yml`. It does not
bundle font files for site display.

## Download URL

The download button defaults to the latest GitHub Release asset:

```text
https://github.com/yamatoiizuka/gen-interface-jp/releases/latest/download/GenInterfaceJP.zip
```

Override it at build time:

```bash
VITE_DOWNLOAD_URL="https://example.com/GenInterfaceJP.zip" \
VITE_DOWNLOAD_LABEL="Download" \
npm run build
```
