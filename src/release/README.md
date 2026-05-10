# Release Packaging

This pipeline packages generated outputs for distribution.

It creates a GitHub Release zip of the TTFs (for users installing the
font into design tools, OS font folders, or bitmap conversion utilities)
and prepares npm and Pages-hosted web font directories that deliver the
font on the web via CSS + unicode-range subset WOFF2.

## Build

```bash
make release
```

This target runs:

```bash
PYTHONPATH=src python3 -m font.build
PYTHONPATH=src python3 -m webfont.build --all --clean --strategy google-japanese
PYTHONPATH=src python3 -m release.build
```

Direct module execution:

```bash
PYTHONPATH=src python3 -m release.build
```

## npm Publishing

The publishable npm package is `dist/release/npm/`. During `make release`,
the `[project].version` from `pyproject.toml` is written into
`dist/release/npm/package.json`.

Before publishing:

```bash
make npm-pack
make npm-publish-dry-run
```

Publish:

```bash
make npm-publish
```

Override npm publish flags with `NPM_PUBLISH_FLAGS`:

```bash
make npm-publish NPM_PUBLISH_FLAGS="--access public --tag next"
```

The npm cache defaults to the repository-local `.npm-cache/` directory. Override
it with `NPM_CACHE` if needed:

```bash
make npm-pack NPM_CACHE=/tmp/gen-interface-jp-npm-cache
```

## GitHub Release Assets

GitHub Release assets are written to:

```text
dist/release/github/
  GenInterfaceJP-<version>.zip   # TTF, all weights × both families
```

The asset filename embeds the version so each release can be linked
unambiguously even after a newer "latest" lands.

Web delivery flows through the npm package below; full single-file WOFF2
is intentionally not redistributed.

### Publishing

Run `make release` to produce the zip, then upload it to a GitHub
Release with the `gh` CLI:

```bash
# Create a new release
gh release create v<version> dist/release/github/GenInterfaceJP-<version>.zip \
  --title "Gen Interface JP v<version>" \
  --notes "Release notes..."

# Or attach to an existing (e.g. draft) release
gh release upload v<version> dist/release/github/*.zip --clobber
```

A draft can be created up front and assets attached afterwards:

```bash
gh release create v<version> --draft --target main \
  --title "Gen Interface JP v<version>" --notes "..."
make release
gh release upload v<version> dist/release/github/*.zip --clobber
# Review on the GitHub UI, then "Publish release"
```

The site download button defaults to:

```text
https://github.com/yamatoiizuka/gen-interface-jp/releases/download/v<version>/GenInterfaceJP-<version>.zip
```

Use `VITE_DOWNLOAD_URL` and `VITE_DOWNLOAD_LABEL` when building the site to
override the URL or label.

## Web Font Hosting

Web font files are written to an npm package root and to a mirrored Pages
directory:

```text
dist/release/npm/
  package.json
  README.md
  OFL.txt
  all.css
  100.css ... 800.css
  display-100.css ... display-800.css
  cdn/all.css
  cdn/100.css ... cdn/800.css
  cdn/display-100.css ... cdn/display-800.css
  w/normal/.../*.woff2
  w/display/.../*.woff2

dist/release/webfonts/
  gen-interface-jp/
    all.css
    100.css ... 800.css
    display-100.css ... display-800.css
    cdn/all.css
    cdn/100.css ... cdn/800.css
    cdn/display-100.css ... cdn/display-800.css
    w/normal/.../*.woff2
    w/display/.../*.woff2
```

For non-npm static hosting, copy `dist/release/webfonts/gen-interface-jp/` to
the public directory. Example CSS path:

```text
/webfonts/gen-interface-jp/all.css
```

Publishing `dist/release/npm/` to npm makes the jsDelivr URL:

```html
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/all.css"
/>
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/400.css"
/>
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/display-400.css"
/>
```

The plain `.css` files keep relative WOFF2 URLs, so npm installs and
self-hosted copies only need to keep the CSS files and `w/` together at the
package root. The `cdn/*.css` files rewrite those WOFF2 URLs to absolute,
version-pinned jsDelivr URLs for direct CDN linking and for crawlers that
resolve CSS relative URLs incorrectly.
