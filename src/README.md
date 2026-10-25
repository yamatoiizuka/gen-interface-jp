# Source Layout

Repository-local source code is grouped under `src/`.

```text
src/
  font/       # core font baking: Inter + Noto Sans JP, spacing, TTF output
  webfont/    # derived web font delivery: CSS + unicode-range subset WOFF2
  release/    # release packaging: GitHub Release zips + npm webfont layout
  parallel.py          # shared child-interpreter job runner (font / webfont)
  project_metadata.py  # reads the canonical version from pyproject.toml
```

The main product of this repository is the baked font family. Web font delivery
and release packaging are built from those font outputs.

Common entry points:

```bash
make font
make webfont
make webfont-benchmark
make release
make site
```

Direct module execution is also possible when `src/` is on `PYTHONPATH`:

```bash
PYTHONPATH=src python3 -m font.build
PYTHONPATH=src python3 -m webfont.build --all
PYTHONPATH=src python3 -m release.build
```

Generated outputs stay under `dist/` and are not committed.
