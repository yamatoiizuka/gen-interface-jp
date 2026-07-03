#!/usr/bin/env python3
"""Build unicode-range web font subsets for Gen Interface JP Regular.

The output is one stylesheet with many @font-face rules, each pointing at a
WOFF2 subset guarded by unicode-range. Browsers only fetch the subset files
needed by the page text.

The subset definition files written to ``nam/`` intentionally use the same
machine-readable style as googlefonts/nam-files: one ``0x...`` codepoint per
line, with comments allowed.

Subsetting is deliberately cmap/codepoint-driven: unicode-range is a
user-facing character policy, and fontTools.subset performs the layout closure
needed to keep referenced glyphs reachable inside each WOFF2.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import gzip
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fontTools import subset
from fontTools.ttLib import TTFont

logging.getLogger("fontTools.ttLib.tables.otTables").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TTF = ROOT / "dist" / "ttf" / "Gen Interface JP" / "GenInterfaceJP-Regular.ttf"
DEFAULT_OUT = ROOT / "dist" / "webfont" / "GenInterfaceJP-Regular"
DEFAULT_ALL_OUT = ROOT / "dist" / "webfont" / "gen-interface-jp"
DEFAULT_GOOGLE_JAPANESE_SLICE = ROOT / "vendor" / "nam-files" / "slices" / "japanese_default.txt"

FAMILY_NAME = "Gen Interface JP"
WEIGHT = 400
STYLE = "normal"
DISPLAY = "swap"

WEIGHTS = (
    (100, "Thin"),
    (200, "ExtraLight"),
    (300, "Light"),
    (400, "Regular"),
    (500, "Medium"),
    (600, "SemiBold"),
    (700, "Bold"),
    (800, "ExtraBold"),
)


@dataclass(frozen=True)
class WebFontFamily:
    key: str
    css_family: str
    dist_folder: str
    file_prefix: str


WEBFONT_FAMILIES = (
    WebFontFamily(
        key="normal",
        css_family="Gen Interface JP",
        dist_folder="Gen Interface JP",
        file_prefix="GenInterfaceJP",
    ),
    WebFontFamily(
        key="display",
        css_family="Gen Interface JP Display",
        dist_folder="Gen Interface JP Display",
        file_prefix="GenInterfaceJPDisplay",
    ),
)

LATIN_RANGES = (
    (0x0000, 0x00FF),
    (0x0131, 0x0131),
    (0x0152, 0x0153),
    (0x02BB, 0x02BC),
    (0x02C6, 0x02C6),
    (0x02DA, 0x02DA),
    (0x02DC, 0x02DC),
    (0x0304, 0x0304),
    (0x0308, 0x0308),
    (0x0329, 0x0329),
    (0x2000, 0x206F),
    (0x20AC, 0x20AC),
    (0x2122, 0x2122),
    (0x2191, 0x2193),
    (0x2212, 0x2215),
    (0xFEFF, 0xFEFF),
    (0xFFFD, 0xFFFD),
)

JP_KANA_RANGES = (
    (0x3000, 0x303F),  # CJK punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x31F0, 0x31FF),  # Katakana phonetic extensions
    (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
)

JP_SYMBOL_RANGES = (
    (0x2E80, 0x2EFF),  # CJK radicals supplement
    (0x2F00, 0x2FDF),  # Kangxi radicals
    (0x3100, 0x312F),  # Bopomofo
    (0x3190, 0x319F),  # Kanbun
    (0x3200, 0x32FF),  # Enclosed CJK letters/months
    (0x3300, 0x33FF),  # CJK compatibility
)


@dataclass(frozen=True)
class WebFontSubset:
    name: str
    codepoints: tuple[int, ...]
    note: str


def codepoints_from_ranges(ranges: Iterable[tuple[int, int]]) -> set[int]:
    cps: set[int] = set()
    for start, end in ranges:
        cps.update(range(start, end + 1))
    return cps


def is_han_codepoint(cp: int) -> bool:
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2FA1F
    )


def jis_row_codepoints(row: int) -> set[int]:
    """Return Unicode codepoints for one JIS X 0208 row.

    Rows 16-47 are first-level kanji, rows 48-84 are second-level kanji. Python's
    EUC-JP codec gives us a portable mapping without vendoring a large table.
    """
    cps: set[int] = set()
    for cell in range(1, 95):
        try:
            char = bytes([row + 0xA0, cell + 0xA0]).decode("euc_jp")
        except UnicodeDecodeError:
            continue
        if len(char) == 1:
            cps.add(ord(char))
    return cps


def _chunk_evenly(values: list[int], chunks: int) -> list[list[int]]:
    if not values:
        return []
    chunk_size = max(1, math.ceil(len(values) / chunks))
    return [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]


def build_subset_plan(font_codepoints: Iterable[int], extra_han_slices: int = 24) -> list[WebFontSubset]:
    """Build non-overlapping unicode-range subsets from the font cmap."""
    supported = set(font_codepoints)
    assigned: set[int] = set()
    subsets: list[WebFontSubset] = []

    def add(name: str, codepoints: Iterable[int], note: str) -> None:
        usable = tuple(sorted((set(codepoints) & supported) - assigned))
        if not usable:
            return
        subsets.append(WebFontSubset(name=name, codepoints=usable, note=note))
        assigned.update(usable)

    add("latin", codepoints_from_ranges(LATIN_RANGES), "Latin, Latin punctuation, and shared symbols")
    add("jp-kana", codepoints_from_ranges(JP_KANA_RANGES), "Japanese punctuation, kana, and fullwidth forms")
    add("jp-symbols", codepoints_from_ranges(JP_SYMBOL_RANGES), "Japanese radicals, enclosed forms, and CJK symbols")

    for row in range(16, 48):
        add(
            f"jp-kanji-jis1-{row:02d}",
            jis_row_codepoints(row),
            f"JIS X 0208 first-level kanji row {row}",
        )

    for row in range(48, 85):
        add(
            f"jp-kanji-jis2-{row:02d}",
            jis_row_codepoints(row),
            f"JIS X 0208 second-level kanji row {row}",
        )

    remaining_han = sorted(cp for cp in supported - assigned if is_han_codepoint(cp))
    for index, codepoints in enumerate(_chunk_evenly(remaining_han, extra_han_slices)):
        add(f"jp-kanji-extra-{index:02d}", codepoints, "CJK codepoints outside JIS X 0208 rows")

    remaining = sorted(supported - assigned)
    for index, codepoints in enumerate(_chunk_evenly(remaining, 8)):
        add(f"other-{index:02d}", codepoints, "Non-Japanese fallback coverage")

    return subsets


def parse_slicing_strategy(path: Path) -> list[set[int]]:
    """Parse googlefonts/nam-files textproto slicing strategy files.

    The format is intentionally simple for our needs:

        subsets {
          codepoints: 12354 # あ
          ...
        }

    Only ``codepoints:`` lines are interpreted, so comment text can contain
    braces like ``# } RIGHT CURLY BRACKET``.
    """
    import re

    text = path.read_text(encoding="utf-8")
    parsed: list[set[int]] = []
    current: set[int] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if re.fullmatch(r"subsets\s*\{", line):
            if current is not None:
                raise ValueError(f"Nested subsets block in slicing strategy: {path}")
            current = set()
            continue
        if line == "}":
            if current is not None and current:
                parsed.append(current)
            current = None
            continue
        if current is None:
            continue
        match = re.match(r"codepoints:\s*(0x[0-9A-Fa-f]+|\d+)", line)
        if match:
            current.add(int(match.group(1), 0))
    if current is not None:
        raise ValueError(f"Unclosed subsets block in slicing strategy: {path}")
    if not parsed:
        raise ValueError(f"No subsets were found in slicing strategy: {path}")
    return parsed


def build_google_japanese_subset_plan(
    font_codepoints: Iterable[int],
    slice_path: Path = DEFAULT_GOOGLE_JAPANESE_SLICE,
    include_remaining: bool = True,
    remaining_slices: int = 8,
) -> list[WebFontSubset]:
    """Build subsets from googlefonts/nam-files' Japanese slicing strategy.

    The strategy file is ordered the same way as Google Fonts' unicode-range
    prioritization. We preserve that order, intersect each slice with this
    font's cmap, then optionally add any cmap codepoints not covered by the
    Japanese strategy so the self-hosted build can still serve the full font.
    """
    supported = set(font_codepoints)
    assigned: set[int] = set()
    subsets: list[WebFontSubset] = []

    for index, codepoints in enumerate(parse_slicing_strategy(slice_path)):
        usable = tuple(sorted((codepoints & supported) - assigned))
        if not usable:
            continue
        subsets.append(
            WebFontSubset(
                name=f"google-japanese-{index:03d}",
                codepoints=usable,
                note=f"googlefonts/nam-files slices/japanese_default.txt subset {index}",
            )
        )
        assigned.update(usable)

    if include_remaining:
        remaining = sorted(supported - assigned)
        for index, codepoints in enumerate(_chunk_evenly(remaining, remaining_slices)):
            subsets.append(
                WebFontSubset(
                    name=f"google-japanese-extra-{index:02d}",
                    codepoints=tuple(codepoints),
                    note="Codepoints supported by Gen Interface JP but not covered by googlefonts/nam-files Japanese slicing strategy",
                )
            )

    return subsets


def select_subset_plan(args: argparse.Namespace, font_codepoints: Iterable[int]) -> list[WebFontSubset]:
    if args.strategy == "jis-row":
        return build_subset_plan(font_codepoints, extra_han_slices=args.extra_han_slices)
    if args.strategy == "google-japanese":
        return build_google_japanese_subset_plan(
            font_codepoints,
            slice_path=args.google_japanese_slice.resolve(),
            include_remaining=not args.no_remaining,
            remaining_slices=args.remaining_slices,
        )
    raise ValueError(f"Unknown strategy: {args.strategy}")


def merge_codepoints_to_ranges(codepoints: Iterable[int]) -> list[tuple[int, int]]:
    values = sorted(set(codepoints))
    if not values:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = values[0]
    for cp in values[1:]:
        if cp == prev + 1:
            prev = cp
            continue
        ranges.append((start, prev))
        start = prev = cp
    ranges.append((start, prev))
    return ranges


def format_unicode_range(codepoints: Iterable[int]) -> str:
    parts = []
    for start, end in merge_codepoints_to_ranges(codepoints):
        if start == end:
            parts.append(f"U+{start:04X}")
        else:
            parts.append(f"U+{start:04X}-{end:04X}")
    return ", ".join(parts)


def _subset_options() -> subset.Options:
    options = subset.Options()
    options.flavor = "woff2"
    options.retain_gids = False
    options.glyph_names = False
    options.layout_features = ["*"]
    options.name_IDs = [1, 2, 3, 4, 5, 6, 16, 17]
    options.name_legacy = False
    options.name_languages = ["*"]
    options.drop_tables = ["DSIG"]
    return options


def build_woff2_subset(src_ttf: Path, out_path: Path, codepoints: Iterable[int]) -> None:
    options = _subset_options()
    subsetter = subset.Subsetter(options=options)
    font = subset.load_font(str(src_ttf), options)
    subsetter.populate(unicodes=sorted(set(codepoints)))
    subsetter.subset(font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subset.save_font(font, str(out_path), options)


def _subset_worker(task: tuple[str, str, tuple[int, ...]]) -> tuple[str, int]:
    src, out, codepoints = task
    out_path = Path(out)
    build_woff2_subset(Path(src), out_path, codepoints)
    return out, out_path.stat().st_size


def build_full_woff2(src_ttf: Path, out_path: Path) -> None:
    """Convert a TTF to a single full-cmap WOFF2.

    Used by the single-Regular benchmark pipeline to produce the
    "everything in one file" baseline that subset-chunk delivery is
    compared against. The main release pipeline does NOT generate this
    file — full-WOFF2 single-file delivery is not a recommended web
    practice, so we only materialise it on demand for benchmarking.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    font = TTFont(src_ttf)
    font.flavor = "woff2"
    font.save(out_path)


def write_nam(out_path: Path, codepoints: Iterable[int], note: str) -> None:
    lines = [f"# {note}", "# One codepoint per line, nam-files style."]
    lines.extend(f"0x{cp:04X}" for cp in sorted(set(codepoints)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def font_face_css(src: str, unicode_range: str | None = None) -> str:
    lines = [
        "@font-face {",
        f'  font-family: "{FAMILY_NAME}";',
        f"  font-style: {STYLE};",
        f"  font-weight: {WEIGHT};",
        f"  font-display: {DISPLAY};",
        f'  src: url("{src}") format("woff2");',
    ]
    if unicode_range:
        lines.append(f"  unicode-range: {unicode_range};")
    lines.append("}")
    return "\n".join(lines)


def font_face_css_minified(family: str, weight: int, src: str, unicode_range: str) -> str:
    return (
        f'@font-face{{font-family:"{family}";font-style:normal;font-weight:{weight};'
        f'font-display:{DISPLAY};src:url("{src}") format("woff2");unicode-range:{unicode_range};}}'
    )


def _relative_to_root(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(resolved)


def _source_font_path(family: WebFontFamily, weight_name: str) -> Path:
    base_dir = ROOT / "dist" / "ttf"
    return base_dir / family.dist_folder / f"{family.file_prefix}-{weight_name}.ttf"


def _brotli_size(data: bytes) -> int | None:
    try:
        import brotli
    except ImportError:
        return None
    return len(brotli.compress(data, quality=11))


def _verify_matching_cmaps(source_paths: list[Path]) -> set[int]:
    base_path = source_paths[0]
    base_cmap = set(TTFont(base_path).getBestCmap().keys())
    mismatches = []
    for path in source_paths[1:]:
        cmap = set(TTFont(path).getBestCmap().keys())
        if cmap != base_cmap:
            mismatches.append((path, len(cmap), len(base_cmap)))
    if mismatches:
        lines = "\n".join(f"  - {_relative_to_root(path)}: {count} cps, expected {expected}" for path, count, expected in mismatches)
        raise ValueError(f"All webfont sources must have the same cmap for shared CSS entrypoints:\n{lines}")
    return base_cmap


def weight_css_filename(family_key: str, weight: int) -> str:
    if family_key == "normal":
        return f"{weight}.css"
    return f"{family_key}-{weight}.css"


def css_size_info(path: Path) -> dict:
    data = path.read_bytes()
    return {
        "path": path.name,
        "bytes": len(data),
        "gzipBytes": len(gzip.compress(data, compresslevel=9)),
        "brotliBytes": _brotli_size(data),
    }


def write_minified_css(path: Path, entries: list[dict]) -> dict:
    css = "".join(
        font_face_css_minified(entry["family"], entry["weight"], f"./{entry['path']}", entry["unicodeRange"])
        for entry in entries
    )
    path.write_text(css, encoding="utf-8")
    return css_size_info(path)


def write_css(out_dir: Path, subset_entries: list[dict], full_entry: dict) -> None:
    subset_css = [
        "/* Generated by webfont.build. */",
        "/* Load with: <link rel=\"stylesheet\" href=\"/webfonts/gen-interface-jp/regular/gen-interface-jp-regular.css\"> */",
        "",
    ]
    for entry in subset_entries:
        subset_css.append(font_face_css(f"./{entry['path']}", entry["unicodeRange"]))
        subset_css.append("")
    (out_dir / "gen-interface-jp-regular.css").write_text("\n".join(subset_css), encoding="utf-8")

    full_css = [
        "/* Generated by webfont.build. */",
        "/* Full, unsubsetted WOFF2 fallback/benchmark stylesheet. */",
        "",
        font_face_css(f"./{full_entry['path']}"),
        "",
    ]
    (out_dir / "gen-interface-jp-regular-full.css").write_text("\n".join(full_css), encoding="utf-8")


def build_all(args: argparse.Namespace) -> dict:
    out_dir = args.output.resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    source_paths: dict[tuple[str, int], Path] = {}
    for family in WEBFONT_FAMILIES:
        for weight, weight_name in WEIGHTS:
            path = _source_font_path(family, weight_name).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Missing source font: {path}\nRun: make font")
            source_paths[(family.key, weight)] = path

    base_codepoints = sorted(_verify_matching_cmaps(list(source_paths.values())))
    plan = select_subset_plan(args, base_codepoints)
    if not plan:
        raise ValueError("Subset plan is empty")

    for index, item in enumerate(plan):
        write_nam(out_dir / "nam" / f"{index:03d}.nam", item.codepoints, item.note)

    tasks: list[tuple[str, str, tuple[int, ...]]] = []
    file_entries: dict[str, dict] = {}
    face_entries: list[dict] = []

    for subset_index, item in enumerate(plan):
        unicode_range = format_unicode_range(item.codepoints)
        for family in WEBFONT_FAMILIES:
            for weight, _ in WEIGHTS:
                relative_path = Path("w") / family.key / str(weight) / f"{subset_index:03d}.woff2"
                out_path = out_dir / relative_path
                source_path = source_paths[(family.key, weight)]
                task = (str(source_path), str(out_path), item.codepoints)
                tasks.append(task)
                file_entries[str(relative_path)] = {
                    "family": family.css_family,
                    "familyKey": family.key,
                    "weight": weight,
                    "subset": f"{subset_index:03d}",
                    "path": str(relative_path),
                    "source": _relative_to_root(source_path),
                    "codepoints": len(item.codepoints),
                    "unicodeRange": unicode_range,
                    "bytes": 0,
                }
                face_entries.append(
                    {
                        "family": family.css_family,
                        "familyKey": family.key,
                        "weight": weight,
                        "path": str(relative_path),
                        "unicodeRange": unicode_range,
                    }
                )

    total_tasks = len(tasks)
    completed = 0
    jobs = max(1, args.jobs)
    print(f"Building {total_tasks} subset WOFF2 files with {jobs} worker(s)...", flush=True)
    if jobs == 1:
        for task in tasks:
            out, size = _subset_worker(task)
            file_entries[str(Path(out).relative_to(out_dir))]["bytes"] = size
            completed += 1
            print(f"[{completed:04d}/{total_tasks:04d}] {Path(out).relative_to(out_dir)} {size / 1024:.1f} KB", flush=True)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
            futures = [executor.submit(_subset_worker, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                out, size = future.result()
                file_entries[str(Path(out).relative_to(out_dir))]["bytes"] = size
                completed += 1
                print(f"[{completed:04d}/{total_tasks:04d}] {Path(out).relative_to(out_dir)} {size / 1024:.1f} KB", flush=True)

    legacy_index_css = out_dir / "index.css"
    if legacy_index_css.exists():
        legacy_index_css.unlink()

    css_entries: dict[str, dict] = {
        "all": write_minified_css(out_dir / "all.css", face_entries),
        "weights": {},
        "displayWeights": {},
    }
    for weight, _ in WEIGHTS:
        normal_entries = [entry for entry in face_entries if entry["familyKey"] == "normal" and entry["weight"] == weight]
        display_entries = [entry for entry in face_entries if entry["familyKey"] == "display" and entry["weight"] == weight]
        css_entries["weights"][str(weight)] = write_minified_css(out_dir / weight_css_filename("normal", weight), normal_entries)
        css_entries["displayWeights"][str(weight)] = write_minified_css(
            out_dir / weight_css_filename("display", weight),
            display_entries,
        )

    subset_bytes = sum(entry["bytes"] for entry in file_entries.values())
    manifest = {
        "family": "Gen Interface JP",
        "style": STYLE,
        "fontDisplay": DISPLAY,
        "generatedAt": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "source": {
            "format": "ttf",
            "strategy": args.strategy,
        },
        "css": css_entries,
        "families": [
            {
                "key": family.key,
                "family": family.css_family,
                "weights": [weight for weight, _ in WEIGHTS],
            }
            for family in WEBFONT_FAMILIES
        ],
        "totals": {
            "fontCodepoints": len(base_codepoints),
            "subsetCount": len(plan),
            "fontFaceCount": len(face_entries),
            "subsetFileCount": len(file_entries),
            "subsetBytes": subset_bytes,
        },
        "files": {
            "subsets": list(file_entries.values()),
        },
    }
    if args.strategy == "google-japanese":
        manifest["source"]["googleJapaneseSlice"] = _relative_to_root(args.google_japanese_slice.resolve())
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    all_css = css_entries["all"]
    print(f"\nCSS: {out_dir / all_css['path']}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    print(f"Subset WOFF2: {subset_bytes / 1024 / 1024:.2f} MB")
    print(f"all.css: {all_css['bytes'] / 1024:.1f} KB raw, {all_css['gzipBytes'] / 1024:.1f} KB gzip")
    if all_css["brotliBytes"] is not None:
        print(f"all.css: {all_css['brotliBytes'] / 1024:.1f} KB brotli")
    return manifest


def build(args: argparse.Namespace) -> dict:
    """Single-Regular pipeline used by the benchmark.

    Produces both the subset chunks and a full single-file WOFF2 from
    the same Regular TTF, plus the legacy manifest shape that
    `benchmark.mjs` reads (`files.subsets[]` / `files.full`). The main
    release pipeline goes through `build_all()` instead and does NOT
    use this code path.
    """
    src_ttf = args.ttf.resolve()
    out_dir = args.output.resolve()
    if not src_ttf.is_file():
        raise FileNotFoundError(f"Missing source TTF: {src_ttf}\nRun: make font")

    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    font = TTFont(src_ttf)
    font_codepoints = sorted(font.getBestCmap().keys())
    plan = select_subset_plan(args, font_codepoints)

    full_out = out_dir / "full" / "GenInterfaceJP-Regular.woff2"
    build_full_woff2(src_ttf, full_out)
    full_entry = {
        "path": str(full_out.relative_to(out_dir)),
        "bytes": full_out.stat().st_size,
    }

    subset_entries: list[dict] = []
    for index, item in enumerate(plan, 1):
        filename = f"GenInterfaceJP-Regular-{item.name}.woff2"
        out_path = out_dir / "subsets" / filename
        build_woff2_subset(src_ttf, out_path, item.codepoints)
        write_nam(out_dir / "nam" / f"{item.name}.nam", item.codepoints, item.note)
        size = out_path.stat().st_size
        print(f"[{index:03d}/{len(plan):03d}] {item.name}: {len(item.codepoints)} cps, {size / 1024:.1f} KB", flush=True)
        subset_entries.append(
            {
                "name": item.name,
                "path": str(out_path.relative_to(out_dir)),
                "nam": f"nam/{item.name}.nam",
                "bytes": size,
                "codepoints": len(item.codepoints),
                "unicodeRange": format_unicode_range(item.codepoints),
                "note": item.note,
            }
        )

    write_css(out_dir, subset_entries, full_entry)

    manifest = {
        "family": FAMILY_NAME,
        "style": STYLE,
        "weight": WEIGHT,
        "fontDisplay": DISPLAY,
        "generatedAt": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "source": {
            "ttf": str(src_ttf.relative_to(ROOT)) if src_ttf.is_relative_to(ROOT) else str(src_ttf),
            "strategy": args.strategy,
        },
        "css": {
            "subset": "gen-interface-jp-regular.css",
            "full": "gen-interface-jp-regular-full.css",
        },
        "files": {
            "full": full_entry,
            "subsets": subset_entries,
        },
        "totals": {
            "fontCodepoints": len(font_codepoints),
            "subsetCount": len(subset_entries),
            "subsetBytes": sum(entry["bytes"] for entry in subset_entries),
            "fullBytes": full_entry["bytes"],
            "coveredCodepoints": len(set().union(*(set(item.codepoints) for item in plan))) if plan else 0,
        },
    }
    if args.strategy == "google-japanese":
        manifest["source"]["googleJapaneseSlice"] = (
            str(args.google_japanese_slice.resolve().relative_to(ROOT))
            if args.google_japanese_slice.resolve().is_relative_to(ROOT)
            else str(args.google_japanese_slice.resolve())
        )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\nCSS: {out_dir / 'gen-interface-jp-regular.css'}")
    print(f"Full CSS: {out_dir / 'gen-interface-jp-regular-full.css'}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Build Text + Display subset WOFF2 for all weights and CSS entrypoints")
    parser.add_argument("--ttf", type=Path, default=DEFAULT_TTF, help="Source Gen Interface JP Regular TTF")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT, help="Output directory")
    parser.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)), help="Parallel workers for --all subset generation")
    parser.add_argument("--strategy", choices=["google-japanese", "jis-row"], default="google-japanese", help="Subset partitioning strategy")
    parser.add_argument("--google-japanese-slice", type=Path, default=DEFAULT_GOOGLE_JAPANESE_SLICE, help="googlefonts/nam-files slices/japanese_default.txt")
    parser.add_argument("--no-remaining", action="store_true", help="Do not add extra subsets for cmap codepoints outside the selected strategy")
    parser.add_argument("--remaining-slices", type=int, default=8, help="Number of extra subsets for codepoints outside the selected strategy")
    parser.add_argument("--extra-han-slices", type=int, default=24, help="Slices for CJK codepoints outside JIS X 0208")
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before building")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.all:
        if args.output == DEFAULT_OUT:
            args.output = DEFAULT_ALL_OUT
        build_all(args)
    else:
        build(args)


if __name__ == "__main__":
    main()
