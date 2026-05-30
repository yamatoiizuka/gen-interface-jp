#!/usr/bin/env python3
"""
Build Gen Interface JP font families.

Shared pipeline per weight:
  1. Bake Noto Sans JP variable → static TTF  (font-baker, base-only,
                                                metadataMode=inheritBase,
                                                output.upm=2048)
  2. Convert to proportional metrics           (palt-based, proportional.py)
  3. Apply tracking                            (horizontal hmtx + vertical vmtx)
  4. Merge Inter/InterDisplay + proportional Noto  (font-baker, with
     subFont.excludeCodepoints to keep CJK-conventional symbols on Noto)

Families:
  - Gen Interface JP         : Inter       + proportional Noto, tracking +30 (kana/punct +45, vertical JP +9) at 1000 UPM
  - Gen Interface JP Display : InterDisplay + proportional Noto, tracking +0

Outputs TTF into dist/ttf/. Web delivery (subset WOFF2 chunks served via
unicode-range) is generated separately by the webfont module from these
TTF outputs — see src/webfont/.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import logging
import os
import subprocess
import sys
import time

from fontTools.ttLib import TTFont
from fontTools.ttLib.reorderGlyphs import reorderGlyphs
from fontTools.varLib import instancer
from merge_fonts import merge_fonts, parse_codepoint_list
from project_metadata import project_version as read_project_version
from .proportional import (
    _install_ss09_punctuation_feature,
    _read_palt,
    _remove_prop_features,
    _scale_position_adjustment,
    make_proportional,
)


# ---------------------------------------------------------------------------
# UPM policy
# ---------------------------------------------------------------------------

# Noto Sans JP's source data is 1000 UPM, but Inter / Inter Display are 2048
# UPM. Bake Noto intermediates and final merged fonts at Inter's native UPM so
# Latin outlines and metrics do not get rounded down to the Noto grid.
SOURCE_UPM = 1000
TARGET_UPM = 2048


def _scale_design_unit(value: int, target_upm: int = TARGET_UPM) -> int:
    """Scale a project design-unit value from Noto's 1000 UPM grid."""
    return round(value * target_upm / SOURCE_UPM)


def _scale_design_adjustment(
    adjustment: tuple[int, int],
    target_upm: int = TARGET_UPM,
) -> tuple[int, int]:
    """Scale a two-value OpenType adjustment from the 1000 UPM design grid."""
    first, second = adjustment
    return (_scale_design_unit(first, target_upm), _scale_design_unit(second, target_upm))


def _scale_design_adjustments(
    adjustments: dict[str, tuple[int, int]],
    target_upm: int = TARGET_UPM,
) -> dict[str, tuple[int, int]]:
    """Scale glyph-keyed OpenType adjustment records from 1000 UPM."""
    return {
        glyph_name: _scale_design_adjustment(adjustment, target_upm)
        for glyph_name, adjustment in adjustments.items()
    }


def _scale_glyph_spacing(
    spacing: dict | None,
    target_upm: int = TARGET_UPM,
) -> dict | None:
    """Scale codepoint-keyed sidebearing adjustments from 1000 UPM."""
    if not spacing:
        return spacing
    return {
        key: _scale_design_adjustment(deltas, target_upm)
        for key, deltas in spacing.items()
    }


def _assert_target_upm(font: TTFont, path: str) -> None:
    """Fail clearly if a build stage did not produce Inter-native UPM."""
    upm = font["head"].unitsPerEm
    if upm != TARGET_UPM:
        raise RuntimeError(
            f"Expected {path} to be {TARGET_UPM} UPM, got {upm}. "
            "Check that the installed ofl-font-baker supports output.upm."
        )


# ---------------------------------------------------------------------------
# Family / weight matrix
# ---------------------------------------------------------------------------

# (output_weight, weight_name, noto_wght_axis_value)
#
# The third column is the wght-axis location used to instantiate Noto Sans JP.
# Most Latin sources use Inter's discrete static masters. Thin and ExtraBold
# are special-cased below to use InterVariable instances at wght=125/775 while
# keeping public metadata at 100/800. Noto's variable axis is non-linear:
# pulling the axis at 400 yields a CJK weight that visually reads lighter than
# Inter Regular. The values below were tuned by eye-matching CJK stem density
# to each Latin master, hence the off-grid numbers (e.g. 465 for Regular,
# 800 for Bold).
WEIGHTS = [
    (100, "Thin",        100),
    (200, "ExtraLight",  270),
    (300, "Light",       355),
    (400, "Regular",     465),
    (500, "Medium",      575),
    (600, "SemiBold",    690),
    (700, "Bold",        800),
    (800, "ExtraBold",   900),
]

# Noto-sourced glyphs that should keep their original advance during the
# tracking pass. These characters are normally repeated or tiled, so adding
# artificial sidebearings breaks the intended no-gap rhythm.
TRACKING_IGNORE_CODEPOINTS = (
    "U+2500-U+257F",   # Box Drawing
    "U+2580-U+259F",   # Block Elements
    "U+2025",          # ‥ TWO DOT LEADER
    "U+22EF",          # ⋯ MIDLINE HORIZONTAL ELLIPSIS
    "U+3030",          # 〰 WAVY DASH
    "U+FE19",          # ︙ PRESENTATION FORM FOR VERTICAL HORIZONTAL ELLIPSIS
    "U+FE30-U+FE34",   # ︰ ︱ ︲ ︳ ︴
    "U+FE49-U+FE4F",   # ﹉ ﹊ ﹋ ﹌ ﹍ ﹎ ﹏
    "U+FF65",          # ･ HALFWIDTH KATAKANA MIDDLE DOT
    "U+2E3A",          # ⸺ TWO-EM DASH
    "U+2E3B",          # ⸻ THREE-EM DASH
    "U+FF3F",          # ＿ FULLWIDTH LOW LINE
    "U+FFE3",          # ￣ FULLWIDTH MACRON
)

# These yakumono punctuation/symbol glyphs keep a reduced baked base metric
# and expose the former palt residual through the ss09 "約物半角" stylistic set
# instead of receiving hand-tuned hmtx spacing. They still receive tracking.
PALT_FEATURE_CHARS = (
    "、", "。", "，", "．",
    "〈", "〉", "《", "》",
    "「", "」", "『", "』",
    "【", "】", "〔", "〕",
    "〖", "〗", "〘", "〙",
    "〚", "〛", "（", "）",
    "｛", "｝", "｟", "｠",
    "〝", "〞", "〟", "［", "］",
    "！", "？", "・", "：", "；",
    "〒", "＂", "＃", "＄", "＆",
    "＇", "＊", "＾", "｀", "￥",
)

# Optional ss09 yakumono still need a reasonably tight default when callers do
# not enable the feature. Bake this fraction into hmtx, then expose only the
# remaining palt delta through ss09 alternates.
RUNTIME_PALT_BASE_SCALE = 0.34

# Noto's weight-dependent palt FeatureVariation changes Bold/ExtraBold spacing
# abruptly. Use the baseline vendor palt as the source of truth for every
# weight, matching the historical Gen Interface JP spacing policy.
_baseline_palt_cache: dict[str, tuple[int, int]] | None = None

# Vertical writing is treated as a fallback path for this UI-focused family.
# Keep it on basic full-width metrics: do not expose Noto vpal/vkrn behavior
# through runtime features, and do not add vertical ss09 alternates.
SS09_VERTICAL_FEATURE_CHARS: tuple[str, ...] = ()
SS09_VERTICAL_FEATURE_GLYPHS: tuple[str, ...] = ()
SS09_SYNTHETIC_VERTICAL_ADJUSTMENTS: dict[str, tuple[int, int]] = {}

# Glyphs listed here get full Noto palt baked like every other non-optional
# palt glyph, then receive explicit hmtx spacing after tracking. Keep this
# list limited to small kana that need breathing room after palt; yakumono
# belongs in PALT_FEATURE_CHARS so optional tightening stays in ss09.
PALT_SPACE_ADJUSTMENTS = {
    "ぁ": (15, 15),
    "ぃ": (15, 15),
    "ぅ": (15, 15),
    "ぇ": (15, 15),
    "ぉ": (15, 15),
    "っ": (15, 15),
    "ゃ": (15, 15),
    "ゅ": (15, 15),
    "ょ": (30, 35),
    "ゎ": (15, 15),
    "ゕ": (15, 15),
    "ゖ": (15, 15),
    "ァ": (15, 15),
    "ィ": (10, 10),
    "ゥ": (15, 15),
    "ェ": (15, 15),
    "ォ": (15, 15),
    "ッ": (15, 15),
    "ャ": (10, 15),
    "ュ": (15, 15),
    "ョ": (15, 15),
    "ヮ": (15, 15),
    "ヵ": (15, 15),
    "ヶ": (15, 15),
}


NORMAL_TRACKING = 30
NORMAL_TRACKING_KANA = 45
NORMAL_VERTICAL_TRACKING = 9
DISPLAY_TRACKING = 0
DISPLAY_TRACKING_KANA = 0
DISPLAY_VERTICAL_TRACKING = 0

NORMAL_PALT_SPACE_ADJUSTMENTS = {
    **PALT_SPACE_ADJUSTMENTS,
}

DISPLAY_PALT_SPACE_ADJUSTMENTS = PALT_SPACE_ADJUSTMENTS


FAMILIES = {
    "normal": {
        "familyName": "Gen Interface JP",
        "interPrefix": "Inter",
        "interFamilyName": "Inter",
        "interOpsz": 14,
        "tracking": NORMAL_TRACKING,
        "trackingKana": NORMAL_TRACKING_KANA,
        "verticalTracking": NORMAL_VERTICAL_TRACKING,
        "trackingIgnore": TRACKING_IGNORE_CODEPOINTS,
        "runtimePalt": PALT_FEATURE_CHARS,
        "folderPrefix": "GenInterfaceJP",
        # Per-glyph sidebearing tweaks applied after tracking. Map a
        # codepoint (int) or single-char string to a (lsb_delta, rsb_delta)
        # pair in design units. Positive deltas add whitespace, negative
        # tighten. Populate when a specific glyph needs a manual margin
        # nudge that palt + tracking alone can't reach.
        "glyphSpacing": {
            **NORMAL_PALT_SPACE_ADJUSTMENTS,
            "く": (30, 0),
        },
    },
    "display": {
        "familyName": "Gen Interface JP Display",
        "interPrefix": "InterDisplay",
        "interFamilyName": "Inter Display",
        "interOpsz": 32,
        "tracking": DISPLAY_TRACKING,
        "trackingKana": DISPLAY_TRACKING_KANA,
        "verticalTracking": DISPLAY_VERTICAL_TRACKING,
        "trackingIgnore": TRACKING_IGNORE_CODEPOINTS,
        "runtimePalt": PALT_FEATURE_CHARS,
        "folderPrefix": "GenInterfaceJPDisplay",
        "glyphSpacing": {
            **DISPLAY_PALT_SPACE_ADJUSTMENTS,
            "く": (30, 0),
        },
    },
}

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENDOR_FONTS = os.path.join(ROOT, "vendor", "fonts")
INTER_DIR = os.path.join(VENDOR_FONTS, "Inter-4.1", "extras", "ttf")
INTER_VARIABLE = os.path.join(VENDOR_FONTS, "Inter-4.1", "InterVariable.ttf")
NOTO_VARIABLE = os.path.join(VENDOR_FONTS, "Noto_Sans_JP", "NotoSansJP-VariableFont_wght.ttf")
DIST = os.path.join(ROOT, "dist")
DIST_TTF = os.path.join(DIST, "ttf")
INTERMEDIATE = os.path.join(DIST, "intermediate")


INTER_VARIABLE_EDGE_WEIGHTS = {
    "Thin": {
        "wght": 125,
        "outputWeight": 100,
    },
    "ExtraBold": {
        "wght": 775,
        "outputWeight": 800,
    },
}

STATIC_INSTANCE_VARIATION_TABLES = (
    "fvar",
    "gvar",
    "avar",
    "HVAR",
    "MVAR",
    "VVAR",
    "STAT",
)


def _axis_value_slug(value: float) -> str:
    """Return a stable filename fragment for an axis coordinate."""
    if float(value).is_integer():
        return str(int(value))
    return str(value).replace(".", "p")


def _default_inter_static_path(family: dict, weight_name: str) -> str:
    """Return the vendor static Inter path for a family/weight."""
    return os.path.join(INTER_DIR, f"{family['interPrefix']}-{weight_name}.ttf")


def _name_record_value(record, value: str) -> None:
    """Replace a name record while preserving its platform encoding."""
    record.string = value.encode(record.getEncoding(), errors="replace")


def _set_inter_static_names(font: TTFont, family_name: str, weight_name: str) -> None:
    """Stamp static Inter instance name records to match vendor statics."""
    legacy_family = f"{family_name} {weight_name}"
    postscript_family = family_name.replace(" ", "")
    replacements = {
        1: legacy_family,
        2: "Regular",
        4: legacy_family,
        6: f"{postscript_family}-{weight_name}",
        16: family_name,
        17: weight_name,
    }
    name_table = font["name"]
    name_table.names = [
        record for record in name_table.names
        if record.nameID != 25
    ]
    for record in name_table.names:
        value = replacements.get(record.nameID)
        if value is not None:
            _name_record_value(record, value)
    for name_id, value in replacements.items():
        name_table.setName(value, name_id, 3, 1, 0x409)
        name_table.setName(value, name_id, 1, 0, 0)


def _inter_variable_instance_path(
    family: dict,
    weight_name: str,
    axes: dict[str, float],
    out_dir: str = INTERMEDIATE,
) -> str:
    """Return the generated static Inter instance path."""
    instance_dir = os.path.join(out_dir, "InterVariable")
    return os.path.join(
        instance_dir,
        (
            f"{family['interPrefix']}-{weight_name}"
            f"-wght{_axis_value_slug(axes['wght'])}"
            f"-opsz{_axis_value_slug(axes['opsz'])}.ttf"
        ),
    )


def _build_inter_variable_instance(
    family: dict,
    weight_num: int,
    weight_name: str,
    out_dir: str = INTERMEDIATE,
) -> str:
    """Instantiate InterVariable for the tuned Thin/ExtraBold Latin masters."""
    config = INTER_VARIABLE_EDGE_WEIGHTS[weight_name]
    expected_weight = config["outputWeight"]
    if weight_num != expected_weight:
        raise ValueError(
            f"{weight_name} variable instance must be stamped as "
            f"usWeightClass {expected_weight}, got {weight_num}."
        )
    axes = {
        "wght": config["wght"],
        "opsz": family["interOpsz"],
    }
    if not os.path.isfile(INTER_VARIABLE):
        raise FileNotFoundError(f"Inter variable font not found: {INTER_VARIABLE}")
    out_path = _inter_variable_instance_path(family, weight_name, axes, out_dir)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    variable_font = TTFont(INTER_VARIABLE)
    try:
        font = instancer.instantiateVariableFont(variable_font, axes, inplace=False)
    finally:
        variable_font.close()

    try:
        font["OS/2"].usWeightClass = weight_num
        _set_inter_static_names(font, family["interFamilyName"], weight_name)
        for table_tag in STATIC_INSTANCE_VARIATION_TABLES:
            if table_tag in font:
                del font[table_tag]
        font.save(out_path)
    finally:
        font.close()
    return out_path


def _inter_source_path(
    family: dict,
    weight_num: int,
    weight_name: str,
    out_dir: str = INTERMEDIATE,
) -> str:
    """Return the Inter sub-font source, generating edge instances as needed."""
    if weight_name in INTER_VARIABLE_EDGE_WEIGHTS:
        return _build_inter_variable_instance(family, weight_num, weight_name, out_dir)
    return _default_inter_static_path(family, weight_name)


def _project_version() -> str:
    """Read the project version used for final generated font metadata."""
    return read_project_version(ROOT)


def _final_output_metadata(
    family_name: str,
    weight_num: int,
    version: str,
) -> dict[str, object]:
    """Return font-baker output metadata for the final Gen Interface JP TTF."""
    return {
        "familyName": family_name,
        "weight": weight_num,
        "italic": False,
        "width": 5,
        "metricsSource": "sub",
        "upm": TARGET_UPM,
        "version": version,
        "manufacturer": "Yamato Iizuka",
        "manufacturerURL": "https://yamatoiizuka.com",
    }


# Codepoints whose glyphs should stay sourced from the base Noto font even
# when Inter/InterDisplay also encodes them. Forwarded as
# subFont.excludeCodepoints to font-baker, which strips them from the sub
# cmap before merge so the base outline survives. Edit to tune merge policy.
#
# ◎ (U+25CE) is intentionally absent: Inter does not encode U+25CE itself,
# but encodes U+0298 with glyph name ``uni25CE`` which used to silently
# overwrite Noto's bullseye. font-baker now detects that glyph-name
# collision and renames the sub glyph to ``uni25CE.sub``, so excluding the
# codepoint here is unnecessary.
SUB_EXCLUDE_CODEPOINTS = [
    "U+2460-U+2469",   # ① ② ③ ④ ⑤ ⑥ ⑦ ⑧ ⑨
    "U+24EA",          # ⓪
    "U+2780-U+2788",   # ➀-➈ (Dingbat Sans-Serif Circled aliases of ①-⑨)
    "U+24B6-U+24CF",   # Ⓐ-Ⓩ
    "U+1F130-U+1F149", # 🄰-🅉
    "U+203B",          # ※
    "U+2295",          # ⊕
    "U+2296",          # ⊖
    "U+2297",          # ⊗
    "U+2298",          # ⊘
    "U+25EF",          # ◯
]
# Note: Dingbat Sans-Serif Circled has no 0 (Unicode never assigned one).
# ➉ (U+2789) exists but Inter does not encode it, so excluding it is moot.
# Negative-circled families (❶-❿ U+2776-U+277F, ➊-➓ U+278A-U+2793) are
# absent from Inter entirely, so they fall through to Noto without help.

# Vertical alignment between Inter and Noto.
#
# When the merged font hands its baseline to Inter (`metricsSource: "sub"`),
# Noto sits a touch low — its CJK ideographs visually rest below the Latin
# x-height baseline. BASELINE_OFFSET nudges every Noto glyph up by 25 units
# so capitals and ideographs share an optical baseline. SCALE shrinks Noto
# to ~92.5% so a CJK character lines up in width with the cap-height of
# Inter at the same nominal point size — a typographic convention for
# Latin/CJK pairing where CJK is slightly down-scaled to feel proportionate.
BASELINE_OFFSET = 25
SCALE = 0.925


# ---------------------------------------------------------------------------
# Glyph classification (codepoint-driven)
# ---------------------------------------------------------------------------

def _glyph_codepoint(glyph_name: str) -> int | None:
    """Parse the codepoint from an Adobe-style 'uniXXXX' glyph name.

    Glyph names that don't follow the convention return None. The check is
    deliberately tolerant: any prefix matching ``uni<4 hex>`` parses, even
    if extra characters follow (Noto sometimes ships names like
    ``uni3042.alt`` which we still treat as U+3042). Names lacking the
    ``uni`` prefix or with non-hex characters return None — those glyphs
    are excluded from kana/CJK classification rather than misclassified.
    """
    if not glyph_name.startswith("uni"):
        return None
    try:
        return int(glyph_name[3:7], 16)
    except (ValueError, IndexError):
        return None


def _is_kana_or_punct(glyph_name: str) -> bool:
    """Return True for hiragana, katakana, or CJK punctuation glyphs.

    Used by tracking to apply a separate (usually larger) tracking value
    to kana and punctuation, since they read at a wider rhythm than Latin
    when set at the same nominal size.
    """
    cp = _glyph_codepoint(glyph_name)
    if cp is None:
        return False
    return (
        0x3000 <= cp <= 0x303F    # CJK Symbols and Punctuation (。、・「」…)
        or 0x3040 <= cp <= 0x309F  # Hiragana
        or 0x30A0 <= cp <= 0x30FF  # Katakana
        or 0x31F0 <= cp <= 0x31FF  # Katakana Phonetic Extensions
        or 0xFF00 <= cp <= 0xFFEF  # Halfwidth and Fullwidth Forms
    )


# ---------------------------------------------------------------------------
# GSUB feature inspection
# ---------------------------------------------------------------------------

def _get_vert_alternates(font: TTFont) -> set[str]:
    """Return glyph names that appear as targets of ``vert`` / ``vrt2`` lookups.

    These are the rotated / vertical-form variants the OpenType engine picks
    up when set with vertical writing mode. We collect them so the proportional
    pass and the bbox-strip pass can avoid touching them: vertical-only glyphs
    don't contribute to the horizontal rhythm we're tuning, and rewriting
    their metrics would mismatch what the unrotated original expects.

    Only single-substitution lookups are walked (``hasattr(st, "mapping")``).
    Vertical lookups in Noto are exclusively single-subs in practice.
    """
    gsub = font.get("GSUB")
    if gsub is None or gsub.table is None or gsub.table.FeatureList is None:
        return set()
    alts = set()
    for fr in gsub.table.FeatureList.FeatureRecord:
        if fr.FeatureTag in ("vert", "vrt2"):
            for li in fr.Feature.LookupListIndex:
                lookup = gsub.table.LookupList.Lookup[li]
                for st in lookup.SubTable:
                    if hasattr(st, "mapping"):
                        alts.update(st.mapping.values())
    return alts


# ---------------------------------------------------------------------------
# CJK / kana classification
# ---------------------------------------------------------------------------

def _is_cjk_codepoint(cp: int) -> bool:
    """Return True for CJK ideograph / radical / compatibility ranges.

    These are the glyphs we keep at full-width metrics — palt's narrowing
    is for kana/punctuation rhythm, but a Han ideograph squeezed below
    full-width loses its grid alignment with surrounding kanji. The block
    list mirrors what Adobe and Google Noto treat as "ideographic" for
    the purposes of full-width preservation.
    """
    return (
        0x2E80 <= cp <= 0x2EFF      # CJK Radicals Supplement
        or 0x2F00 <= cp <= 0x2FDF   # Kangxi Radicals
        or 0x3020 <= cp <= 0x3029   # Hangzhou-style numerals (〇〡〢…)
        or 0x3038 <= cp <= 0x303B   # CJK Symbols: 〸〹〺〻
        or 0x3100 <= cp <= 0x312F   # Bopomofo
        or 0x3130 <= cp <= 0x318F   # Hangul Compatibility Jamo
        or 0x3190 <= cp <= 0x319F   # Kanbun
        or 0x31A0 <= cp <= 0x31EF   # Bopomofo Extended + CJK Strokes
        or 0x3200 <= cp <= 0x32FF   # Enclosed CJK Letters and Months
        or 0x3300 <= cp <= 0x33FF   # CJK Compatibility
        or 0x3400 <= cp <= 0x4DBF   # CJK Unified Ideographs Extension A
        or 0x4E00 <= cp <= 0x9FFF   # CJK Unified Ideographs
        or 0xF900 <= cp <= 0xFAFF   # CJK Compatibility Ideographs
        or 0x20000 <= cp <= 0x2FA1F  # CJK Extensions B-F + Supplements
    )


def _get_cjk_glyphs(font: TTFont) -> set[str]:
    """Resolve CJK ideograph glyph names through the font's cmap.

    cmap-driven lookup (rather than glyph-name parsing) catches ideographs
    whose names don't follow ``uniXXXX`` — Noto ships some Han glyphs as
    ``cidNNNNN`` or post-substitution names that wouldn't match a
    ``uni``-prefix check.
    """
    cmap = font.getBestCmap()
    if cmap is None:
        return set()
    return {gname for cp, gname in cmap.items() if _is_cjk_codepoint(cp)}


def _split_cmap_codepoint_glyph(
    font: TTFont,
    codepoint: int,
    new_glyph_name: str,
) -> str | None:
    """Give one cmap codepoint its own glyph copy when it shares a glyph.

    Noto maps both U+2027 (‧) and U+30FB (・) to ``uni2027``. The spacing
    policy treats them differently, so U+30FB needs an independent hmtx/glyf
    record before palt and manual spacing are applied. The source glyph name
    is returned so callers can copy palt override data when needed.
    """
    cmap = font.getBestCmap() or {}
    source_glyph = cmap.get(codepoint)
    if source_glyph is None:
        return None
    if source_glyph == new_glyph_name:
        return source_glyph

    glyph_order = font.getGlyphOrder()
    if new_glyph_name not in glyph_order:
        font.setGlyphOrder([*glyph_order, new_glyph_name])
        font["glyf"][new_glyph_name] = copy.deepcopy(font["glyf"][source_glyph])
        font["hmtx"].metrics[new_glyph_name] = font["hmtx"].metrics[source_glyph]
        if "vmtx" in font and source_glyph in font["vmtx"].metrics:
            font["vmtx"].metrics[new_glyph_name] = font["vmtx"].metrics[source_glyph]

    for table in font["cmap"].tables:
        if table.cmap.get(codepoint) == source_glyph:
            table.cmap[codepoint] = new_glyph_name

    return source_glyph


def _is_kana_letter(glyph_name: str) -> bool:
    """Return True for hiragana / katakana *letters*, excluding punctuation.

    Stricter than :func:`_is_kana_or_punct`: this helper answers whether a
    glyph is an actual kana letter rather than CJK punctuation or fullwidth
    symbols.
    Notable exclusion: U+30FB (・) is punctuation, not a letter.
    """
    cp = _glyph_codepoint(glyph_name)
    if cp is None:
        return False
    return (
        0x3041 <= cp <= 0x3096    # Hiragana letters (ぁ-ゖ)
        or 0x3099 <= cp <= 0x309F  # Hiragana combining/iteration marks
        or 0x30A1 <= cp <= 0x30FA  # Katakana letters (ァ-ヺ), excludes ・(30FB)
        or 0x30FC <= cp <= 0x30FF  # Katakana prolonged sound / iteration marks
        or 0x31F0 <= cp <= 0x31FF  # Katakana Phonetic Extensions
    )


# ---------------------------------------------------------------------------
# Horizontal scale (長体 / condensed)
# ---------------------------------------------------------------------------

def _apply_x_scale(font: TTFont, scale: float) -> None:
    """Apply a horizontal-only scale (長体) to glyphs, hmtx, and GPOS.

    font-baker only supports uniform scale during merge, so condensing CJK
    relative to Latin has to happen *before* the merge step on the base
    font. This function squeezes Noto in x only — y stays untouched —
    then font-baker's uniform scale on top preserves the modified x:y
    ratio. GPOS X values (kerning, mark positioning) are scaled to match
    so kerning pairs continue to land where the design intends.
    """
    if scale == 1.0:
        return

    # Scale glyf coordinates and bbox.
    glyf = font["glyf"]
    for gname in font.getGlyphOrder():
        g = glyf[gname]
        if g.isComposite():
            for component in g.components:
                component.x = round(component.x * scale)
        elif g.numberOfContours > 0:
            coords = g.coordinates
            for i in range(len(coords)):
                x, y = coords[i]
                coords[i] = (round(x * scale), y)
            if hasattr(g, "xMin") and g.xMin is not None:
                g.xMin = round(g.xMin * scale)
                g.xMax = round(g.xMax * scale)

    # Scale advance widths and LSBs.
    hmtx = font["hmtx"]
    for gname in list(hmtx.metrics.keys()):
        aw, lsb = hmtx.metrics[gname]
        hmtx.metrics[gname] = (round(aw * scale), round(lsb * scale))

    # Scale GPOS X values (kerning, mark positioning, etc.).
    gpos = font.get("GPOS")
    if gpos is not None and gpos.table and gpos.table.LookupList:
        for lookup in gpos.table.LookupList.Lookup:
            for st in lookup.SubTable:
                _scale_gpos_x(st, scale)


def _scale_gpos_x(st, scale: float) -> None:
    """Scale every X-direction value in a GPOS subtable in place.

    Walks SinglePos (type 1), PairPos formats 1 and 2 (type 2), and the
    mark-anchor families (MarkArray / Mark1Array / Mark2Array, BaseArray).
    Subtable types not listed here — cursive attachment (type 3),
    contextual positioning (types 7 / 8), Extension (type 9 — handled by
    the caller via subtable unwrapping) — are not used by Noto Sans JP
    in any consequential way for our pipeline, so this targeted walk
    suffices.
    """
    def scale_value_record(vr):
        if vr is None:
            return
        if getattr(vr, "XPlacement", None) is not None:
            vr.XPlacement = round(vr.XPlacement * scale)
        if getattr(vr, "XAdvance", None) is not None:
            vr.XAdvance = round(vr.XAdvance * scale)

    def scale_anchor(anchor):
        if anchor is not None and hasattr(anchor, "XCoordinate"):
            anchor.XCoordinate = round(anchor.XCoordinate * scale)

    # SinglePos (type 1)
    if hasattr(st, "Value"):
        v = st.Value
        if isinstance(v, list):
            for vr in v:
                scale_value_record(vr)
        else:
            scale_value_record(v)

    # PairPos format 1
    if hasattr(st, "PairSet") and st.PairSet:
        for ps in st.PairSet:
            for pvr in ps.PairValueRecord:
                scale_value_record(pvr.Value1)
                scale_value_record(pvr.Value2)

    # PairPos format 2
    if hasattr(st, "Class1Record") and st.Class1Record:
        for c1r in st.Class1Record:
            for c2r in c1r.Class2Record:
                scale_value_record(c2r.Value1)
                scale_value_record(c2r.Value2)

    # Mark anchors (MarkArray, BaseArray, LigatureArray)
    for attr in ("MarkArray", "Mark1Array", "Mark2Array"):
        ma = getattr(st, attr, None)
        if ma and hasattr(ma, "MarkRecord"):
            for mr in ma.MarkRecord:
                scale_anchor(mr.MarkAnchor)
    for attr in ("BaseArray",):
        ba = getattr(st, attr, None)
        if ba and hasattr(ba, "BaseRecord"):
            for br in ba.BaseRecord:
                for anchor in br.BaseAnchor or []:
                    scale_anchor(anchor)


# ---------------------------------------------------------------------------
# Bbox / head-table cleanup
# ---------------------------------------------------------------------------

# Threshold for "extreme" glyphs whose bbox dominates head.yMax/yMin. Values
# are kept in the project design grid (1000 UPM) and scaled to the font's
# active UPM before comparison. The legitimate Latin/CJK content of Noto stays
# well within these bounds, so anything past them is the vertical-only
# iteration-mark glyphs we want to neutralise.
_EXTREME_YMAX = 1200
_EXTREME_YMIN = -400
_VERTICAL_REPEAT_MARK_CODEPOINTS = tuple(range(0x3031, 0x3036))


def _strip_extreme_glyphs(font: TTFont) -> None:
    """Neutralise vertical-only repeat marks and bbox outliers.

    Targets vertical-text-only glyphs (kana iteration marks 〱〲〳〴〵 and
    their vert/vrt2 alternates). The extreme full-form glyphs inflate
    head.yMax/yMin, and the upper/lower-half remnants can confuse Adobe's
    Japanese composer when pasted into horizontal text. Acceptable trade-off
    for a horizontal-only UI font: vertical typesetting is out of scope
    (see docs/ARCHITECTURE.md).

    Removing the glyph slots outright would shift every later index in
    GSUB / GPOS lookups. Instead we keep the slot in place and only
    replace the outline with an empty Glyph — the bbox no longer
    contributes to head, and dropping the cmap entry makes the
    codepoint fall through to .notdef when typed.
    """
    from fontTools.ttLib.tables._g_l_y_f import Glyph

    glyf = font["glyf"]
    hmtx = font["hmtx"]
    to_remove = set()
    cmap = font.getBestCmap() or {}
    upm = font["head"].unitsPerEm
    extreme_ymax = _scale_design_unit(_EXTREME_YMAX, upm)
    extreme_ymin = _scale_design_unit(_EXTREME_YMIN, upm)
    to_remove.update(
        glyph_name
        for cp, glyph_name in cmap.items()
        if cp in _VERTICAL_REPEAT_MARK_CODEPOINTS
    )
    for gname in font.getGlyphOrder():
        g = glyf[gname]
        if g.numberOfContours == 0:
            continue
        if not hasattr(g, "yMax") or g.yMax is None:
            continue
        if g.yMax > extreme_ymax or g.yMin < extreme_ymin:
            to_remove.add(gname)

    if not to_remove:
        return

    # Replace each target glyph with an empty outline.
    for gname in to_remove:
        empty = Glyph()
        empty.numberOfContours = 0
        empty.xMin = empty.yMin = empty.xMax = empty.yMax = 0
        glyf[gname] = empty
        if gname in hmtx.metrics:
            hmtx.metrics[gname] = (0, 0)

    # Drop cmap entries so typing these codepoints falls through to .notdef.
    for table in font["cmap"].tables:
        table.cmap = {cp: g for cp, g in table.cmap.items() if g not in to_remove}

    # Tidy GSUB: remove single-substitution mappings touching these glyphs.
    gsub = font.get("GSUB")
    if gsub is not None and gsub.table and gsub.table.LookupList:
        for lookup in gsub.table.LookupList.Lookup:
            for st in lookup.SubTable:
                if hasattr(st, "mapping"):
                    st.mapping = {
                        k: v for k, v in st.mapping.items()
                        if k not in to_remove and v not in to_remove
                    }


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------

def _glyphs_for_codepoints(
    font: TTFont,
    codepoint_entries: list | tuple | set | None,
) -> set[str]:
    """Resolve codepoint / single-character entries to glyph names via cmap."""
    codepoints = _codepoints_for_entries(codepoint_entries)
    cmap = font.getBestCmap() or {}
    return {glyph_name for cp, glyph_name in cmap.items() if cp in codepoints}


def _codepoints_for_entries(codepoint_entries: list | tuple | set | None) -> set[int]:
    """Resolve mixed codepoint entries into integer codepoints."""
    if not codepoint_entries:
        return set()
    entries = (
        tuple(codepoint_entries)
        if isinstance(codepoint_entries, set)
        else codepoint_entries
    )
    codepoints = set()
    parse_entries = []
    for entry in entries:
        if isinstance(entry, str) and len(entry) == 1:
            codepoints.add(ord(entry))
        else:
            parse_entries.append(entry)
    if parse_entries:
        codepoints.update(parse_codepoint_list(parse_entries))
    return codepoints


def _is_japanese_vertical_body_codepoint(cp: int) -> bool:
    """Return True for glyphs whose vertical body should follow Noto scale."""
    return (
        _is_cjk_codepoint(cp)
        or 0x3000 <= cp <= 0x303F    # CJK Symbols and Punctuation
        or 0x3040 <= cp <= 0x309F    # Hiragana
        or 0x30A0 <= cp <= 0x30FF    # Katakana
        or 0x31F0 <= cp <= 0x31FF    # Katakana Phonetic Extensions
        or 0xFE10 <= cp <= 0xFE4F    # Vertical forms / CJK compatibility forms
        or 0xFF00 <= cp <= 0xFFEF    # Halfwidth and Fullwidth Forms
    )


def _vertical_body_extra_codepoints() -> set[int]:
    """Return configured Japanese punctuation/symbol codepoints outside blocks."""
    return _codepoints_for_entries(
        (
            *TRACKING_IGNORE_CODEPOINTS,
            *PALT_FEATURE_CHARS,
            *SS09_VERTICAL_FEATURE_CHARS,
            *PALT_SPACE_ADJUSTMENTS.keys(),
        )
    )


def _vertical_body_glyphs(font: TTFont) -> set[str]:
    """Resolve final glyphs whose vertical advance should match Noto scale."""
    cmap = font.getBestCmap() or {}
    extra_codepoints = _vertical_body_extra_codepoints()
    glyphs = {
        glyph_name
        for cp, glyph_name in cmap.items()
        if _is_japanese_vertical_body_codepoint(cp) or cp in extra_codepoints
    }
    glyphs.update(_get_vert_alternates(font))
    glyphs.update(SS09_VERTICAL_FEATURE_GLYPHS)
    return glyphs


def _glyph_y_max(font: TTFont, glyph_name: str) -> int:
    """Return a glyph's yMax, treating empty glyphs as origin-height."""
    glyph = font["glyf"][glyph_name]
    if glyph.isComposite():
        glyph.recalcBounds(font["glyf"])
    if getattr(glyph, "numberOfContours", 0) == 0:
        return 0
    return getattr(glyph, "yMax", 0) or 0


def _source_glyphs_by_final_glyph(
    final_font: TTFont,
    source_font: TTFont,
) -> dict[str, str]:
    """Map final cmap glyphs back to source cmap glyphs by codepoint."""
    final_cmap = final_font.getBestCmap() or {}
    source_cmap = source_font.getBestCmap() or {}
    source_order = set(source_font.getGlyphOrder())
    mapping = {}
    for cp, final_glyph in final_cmap.items():
        source_glyph = source_cmap.get(cp)
        if source_glyph in source_order:
            mapping[final_glyph] = source_glyph
    return mapping


def _scale_vertical_body_metrics(
    font: TTFont,
    source_font: TTFont,
    scale: float,
    baseline_offset: int,
    glyph_names: set[str] | None = None,
) -> int:
    """Scale Japanese vertical body metrics after Noto outline scaling.

    font-baker scales Noto outlines and applies the baseline offset during the
    final merge, but leaves vmtx advances/top sidebearings on the original
    2048-UPM body. Recompute vmtx from the pre-merge Noto source origin so the
    vertical advance box follows the same baseline-based transform as outlines.
    """
    if "vmtx" not in font or "vmtx" not in source_font:
        return 0
    if "glyf" not in font or "glyf" not in source_font:
        return 0

    final_order = set(font.getGlyphOrder())
    source_order = set(source_font.getGlyphOrder())
    final_to_source = _source_glyphs_by_final_glyph(font, source_font)
    targets = glyph_names if glyph_names is not None else _vertical_body_glyphs(font)
    adjusted = 0

    for glyph_name in sorted(targets):
        if glyph_name not in final_order:
            continue
        if glyph_name not in font["vmtx"].metrics:
            continue

        source_glyph_name = glyph_name
        if source_glyph_name not in source_order:
            source_glyph_name = final_to_source.get(glyph_name)
        if source_glyph_name is None or source_glyph_name not in source_order:
            continue
        if source_glyph_name not in source_font["vmtx"].metrics:
            continue

        source_advance, source_tsb = source_font["vmtx"][source_glyph_name]
        source_origin_y = _glyph_y_max(source_font, source_glyph_name) + source_tsb
        final_origin_y = round(source_origin_y * scale + baseline_offset)
        final_advance = round(source_advance * scale)
        final_tsb = final_origin_y - _glyph_y_max(font, glyph_name)
        next_metric = (final_advance, final_tsb)

        if font["vmtx"][glyph_name] != next_metric:
            font["vmtx"][glyph_name] = next_metric
            adjusted += 1

    return adjusted


def _scale_vertical_body_metrics_after_merge(
    final_path: str,
    source_path: str,
    scale: float,
    baseline_offset: int,
) -> int:
    """Apply Japanese vmtx body scaling to a merged final font on disk."""
    font = TTFont(final_path)
    source_font = TTFont(source_path)
    try:
        adjusted = _scale_vertical_body_metrics(
            font,
            source_font,
            scale,
            baseline_offset,
        )
        if adjusted:
            font.save(final_path)
        return adjusted
    finally:
        font.close()
        source_font.close()


def _apply_vertical_tracking(
    font: TTFont,
    tracking: int,
    glyph_names: set[str] | None = None,
) -> int:
    """Add vertical advance to Japanese body glyphs without moving origins."""
    if tracking == 0:
        return 0
    if "vmtx" not in font:
        return 0

    glyph_order = set(font.getGlyphOrder())
    targets = glyph_names if glyph_names is not None else _vertical_body_glyphs(font)
    adjusted = 0
    for glyph_name in sorted(targets):
        if glyph_name not in glyph_order:
            continue
        if glyph_name not in font["vmtx"].metrics:
            continue
        advance_height, top_side_bearing = font["vmtx"][glyph_name]
        if advance_height == 0:
            continue
        font["vmtx"][glyph_name] = (
            advance_height + tracking,
            top_side_bearing,
        )
        adjusted += 1
    return adjusted


def _feature_adjustments_for_codepoints(
    font: TTFont,
    codepoint_entries: list | tuple | set | None,
    source_adjustments: dict[str, tuple[int, int]],
) -> dict[int, tuple[int, int]]:
    """Capture feature records by codepoint before merge-time glyph renames.

    font-baker can rename base glyphs when Inter and Noto both contain the
    same glyph name. Optional ss09 targets must therefore survive by Unicode
    scalar, then be retargeted to the final cmap glyph after merge.
    """
    if not source_adjustments:
        return {}
    codepoints = _codepoints_for_entries(codepoint_entries)
    if not codepoints:
        return {}
    cmap = font.getBestCmap() or {}
    return {
        cp: source_adjustments[glyph_name]
        for cp, glyph_name in cmap.items()
        if cp in codepoints and glyph_name in source_adjustments
    }


def _get_baseline_palt() -> dict[str, tuple[int, int]]:
    """Read Noto's baseline palt feature once and reuse it for every weight."""
    global _baseline_palt_cache
    if _baseline_palt_cache is None:
        source = TTFont(NOTO_VARIABLE)
        try:
            _baseline_palt_cache = dict(_read_palt(source))
        finally:
            source.close()
    return _baseline_palt_cache


def _runtime_palt_residual_adjustment(
    adjustment: tuple[int, int],
) -> tuple[int, int]:
    """Return the residual palt delta after the default base fraction is baked."""
    base_placement, base_advance = _scale_position_adjustment(
        adjustment,
        RUNTIME_PALT_BASE_SCALE,
    )
    placement, advance = adjustment
    return (placement - base_placement, advance - base_advance)


def _runtime_palt_residuals_by_codepoint(
    adjustments: dict[int, tuple[int, int]],
) -> dict[int, tuple[int, int]]:
    """Convert full palt records to residual records for final ss09 install."""
    return {
        cp: _runtime_palt_residual_adjustment(adjustment)
        for cp, adjustment in adjustments.items()
    }


def _scale_feature_adjustments(
    adjustments: dict[int | str, tuple[int, int]],
    scale: float,
) -> dict[int | str, tuple[int, int]]:
    """Scale codepoint- or glyph-keyed OpenType placement/advance records."""
    return {
        key: _scale_position_adjustment(value, scale)
        for key, value in adjustments.items()
    }


def _retarget_feature_adjustments(
    font: TTFont,
    adjustments_by_codepoint: dict[int, tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    """Map codepoint-keyed feature records onto the final font's glyph names."""
    if not adjustments_by_codepoint:
        return {}
    cmap = font.getBestCmap() or {}
    glyph_order = set(font.getGlyphOrder())
    return {
        glyph_name: value
        for cp, value in adjustments_by_codepoint.items()
        if (glyph_name := cmap.get(cp)) in glyph_order
    }


def _retarget_named_adjustments(
    font: TTFont,
    adjustments_by_glyph: dict[str, tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    """Map glyph-name fallback records onto the final font when possible."""
    if not adjustments_by_glyph:
        return {}
    cmap = font.getBestCmap() or {}
    glyph_order = set(font.getGlyphOrder())
    retargeted = {}
    for glyph_name, value in adjustments_by_glyph.items():
        if glyph_name in glyph_order:
            retargeted[glyph_name] = value
            continue
        cp = _glyph_codepoint(glyph_name)
        if cp is not None and (target_glyph := cmap.get(cp)) in glyph_order:
            retargeted[target_glyph] = value
    return retargeted


def _refresh_ss09_feature_after_merge(
    final_path: str,
    ss09_punctuation_by_codepoint: dict[int, tuple[int, int]],
    ss09_vertical_by_codepoint: dict[int, tuple[int, int]],
    ss09_vertical_by_glyph: dict[str, tuple[int, int]],
) -> None:
    """Install yakumono-only ss09 after merge.

    font-baker may rename colliding base glyphs in the final font. Rebuild the
    codepoint-keyed optional punctuation behavior against the final cmap so
    characters such as U+FF40 (｀), which shares Noto's ``uni2035`` glyph before
    merge, keep their ss09 adjustment on the renamed final glyph. Horizontal
    palt is intentionally not reinstalled here. Production builds also pass
    no vertical adjustments, so vertical ss09 remains disabled.
    """
    font = TTFont(final_path)
    ss09_adjustments = _retarget_feature_adjustments(
        font,
        ss09_punctuation_by_codepoint,
    )
    ss09_vertical_adjustments = _retarget_feature_adjustments(
        font,
        ss09_vertical_by_codepoint,
    )
    ss09_vertical_adjustments.update(
        _retarget_named_adjustments(font, ss09_vertical_by_glyph)
    )

    _remove_prop_features(font)
    _install_ss09_punctuation_feature(
        font,
        ss09_adjustments,
        ss09_vertical_adjustments,
    )
    font.save(final_path)


def _tracking_ignore_glyphs(
    font: TTFont,
    tracking_ignore: list | tuple | set | None,
) -> set[str]:
    """Resolve tracking-ignore codepoints to glyph names via cmap."""
    return _glyphs_for_codepoints(font, tracking_ignore)


def _apply_tracking(
    font: TTFont,
    tracking: int,
    tracking_kana: int | None = None,
    tracking_ignore: list | tuple | set | None = None,
) -> None:
    """Widen every glyph's advance width and split the gap evenly L/R.

    Adding tracking to a glyph means growing its advance by ``t`` and
    nudging the LSB by ``t // 2`` so that the same outline sits centred
    in the new wider slot — half the new whitespace ends up on the left
    sidebearing, the other half on the right. This matches how design
    apps interpret tracking in Latin typography, applied per-glyph
    rather than as a global text-engine setting.

    Zero-width glyphs (combining marks, mark-positioning anchors) are
    skipped so they keep their placement-only role intact.

    When *tracking_kana* is set, hiragana / katakana / punctuation glyphs
    receive that value instead of *tracking*. The Gen Interface JP
    families use this to give kana and punctuation a slightly looser
    rhythm than Latin — kana need more breathing room at small sizes
    to remain legible against the denser Han ideographs.

    *tracking_ignore* accepts the same codepoint entry forms as font-baker's
    excludeCodepoints config (e.g. ``"U+2500-U+257F"`` or ``0x3030``).
    Matching cmap glyphs are skipped entirely, preserving no-gap rhythm for
    box drawing, block elements, leaders, and similar repeatable symbols.
    """
    hmtx = font["hmtx"]
    ignore_glyphs = _tracking_ignore_glyphs(font, tracking_ignore)
    for glyph_name in font.getGlyphOrder():
        aw, lsb = hmtx[glyph_name]
        if aw == 0:
            continue
        if glyph_name in ignore_glyphs:
            continue
        t = tracking
        if tracking_kana is not None and _is_kana_or_punct(glyph_name):
            t = tracking_kana
        half = t // 2
        hmtx[glyph_name] = (aw + t, lsb + half)


# ---------------------------------------------------------------------------
# Per-glyph sidebearing tweaks
# ---------------------------------------------------------------------------

def _apply_glyph_spacing(font: TTFont, spacing: dict | None) -> int:
    """Adjust per-glyph left / right sidebearings.

    *spacing* maps a codepoint (``int``) or single-character string to a
    ``(lsb_delta, rsb_delta)`` pair in design units. Positive deltas add
    whitespace, negative deltas tighten. The two deltas are independent:

    - ``lsb_delta`` shifts the outline ``lsb_delta`` units to the right
      *inside* the slot and grows advance by the same amount, so the
      whitespace between the slot's left edge and the outline grows by
      ``lsb_delta`` while the right side stays untouched.
    - ``rsb_delta`` extends the slot on the right by ``rsb_delta`` units
      without moving the outline, so the whitespace between the outline
      and the slot's right edge grows by ``rsb_delta``.

    Combined effect: ``advance += lsb_delta + rsb_delta``,
    ``lsb += lsb_delta``. Outline coordinates are never touched, only
    the hmtx record is rewritten.

    Designed as a manual fallback for glyphs whose proportional palt
    plus uniform tracking still leave the sidebearings off — e.g. a
    bracket whose right side reads too tight against following kana.
    Apply sparingly and after ``_apply_tracking``, so the deltas layer
    on top of the canonical proportional metrics.

    Glyphs whose codepoint is absent from cmap and zero-advance glyphs
    (combining marks, mark anchors) are skipped silently. Returns the
    number of glyphs actually adjusted.
    """
    if not spacing:
        return 0
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"]
    adjusted = 0
    for key, deltas in spacing.items():
        if isinstance(key, str):
            if len(key) != 1:
                raise ValueError(
                    f"glyphSpacing key {key!r}: expected a single character "
                    f"or an integer codepoint"
                )
            cp = ord(key)
        else:
            cp = int(key)
        try:
            lsb_delta, rsb_delta = deltas
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"glyphSpacing for U+{cp:04X}: expected (lsb_delta, rsb_delta), "
                f"got {deltas!r}"
            ) from exc
        if lsb_delta == 0 and rsb_delta == 0:
            continue
        glyph_name = cmap.get(cp)
        if glyph_name is None:
            continue
        aw, lsb = hmtx[glyph_name]
        if aw == 0:
            continue
        hmtx[glyph_name] = (aw + lsb_delta + rsb_delta, lsb + lsb_delta)
        adjusted += 1
    return adjusted


@contextmanager
def _suppress_fonttools_coverage_warnings():
    """Suppress known coverage-order warnings around font-baker merge output.

    font-baker can reorder glyphs while carrying layout lookups through the
    merge. fontTools warns while writing that intermediate final font, then a
    normal TTFont load/save sorts the coverage tables by the final glyph IDs.
    """
    logger = logging.getLogger("fontTools.ttLib.tables.otTables")
    old_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(old_level)


def _normalize_layout_coverage_order(font_path: str) -> None:
    """Rewrite a TTF so GSUB/GPOS coverage tables follow final glyph order."""
    with _suppress_fonttools_coverage_warnings():
        font = TTFont(font_path)
        reorderGlyphs(font, font.getGlyphOrder())
        font.save(font_path)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _intermediate_paths(family: dict, weight_name: str) -> tuple[str, str]:
    """Return family-scoped intermediates so parallel builds never collide."""
    prefix = family["folderPrefix"]
    return (
        os.path.join(INTERMEDIATE, f"{prefix}-{weight_name}-NotoSansJP-Inst.ttf"),
        os.path.join(INTERMEDIATE, f"{prefix}-{weight_name}-NotoSansJP-Prop.ttf"),
    )


def _final_ttf_path(family: dict, weight_name: str) -> str:
    family_name = family["familyName"]
    file_name = f"{family['folderPrefix']}-{weight_name}"
    return os.path.join(DIST_TTF, family_name, f"{file_name}.ttf")


def build_one(family: dict, weight_num: int, weight_name: str, noto_wght: int) -> dict:
    """Build a single weight of a Gen Interface JP family.

    Pipeline:

    1. **Bake** Noto variable → static TTF at the chosen wght axis location,
       passed through font-baker with ``metadataMode: inheritBase`` and
       ``output.upm = 2048`` so the Noto identity records survive into the inst.
    2. **Proportionalise** the inst — read baseline palt from Noto Variable,
       bake those adjustments into hmtx except selected horizontal ss09
       yakumono, apply tracking,
       apply per-glyph sidebearing tweaks from ``family["glyphSpacing"]``,
       strip extreme bbox glyphs, optionally apply x-scale.
    3. **Merge** the proportional Noto with the matching Inter master via
       font-baker. ``subFont.excludeCodepoints`` keeps CJK-conventional
       symbols (※, ◯, ①, Ⓐ, …) on the Noto outline, and font-baker's
       glyph-name collision detection rescues cases like Inter's U+0298
       sharing the ``uni25CE`` glyph name with Noto's ◎. Output identity
       is rewritten to "Gen Interface JP", Inter's vertical metrics drive
       the merged hhea (``metricsSource: "sub"``), and our manufacturer /
       URL plus the project version get stamped into the final name/head
       metadata.
    """
    os.makedirs(INTERMEDIATE, exist_ok=True)

    inter_path = _inter_source_path(family, weight_num, weight_name)
    if not os.path.isfile(inter_path):
        raise FileNotFoundError(f"Inter font not found: {inter_path}")

    inst_path, prop_path = _intermediate_paths(family, weight_name)

    # ── Step 1: Bake Noto variable → static (font-baker, base-only) ──
    # `metadataMode: inheritBase` keeps Noto's name/OS2 records intact so
    # designer/OFL/version metadata survives into the inst TTF (no manual
    # save/restore needed). Only `weight` is overridden to stamp the static
    # instance — family/italic/width inherit from the Noto base. `output.upm`
    # moves every Noto glyph, including unmapped vertical alternates, onto
    # Inter's 2048 UPM grid before the in-house spacing passes run.
    print(f"    [1/3] Baking Noto Sans JP (wght={noto_wght})...")
    bake_config = {
        "baseFont": {
            "path": NOTO_VARIABLE,
            "scale": 1.0,
            "baselineOffset": 0,
            "axes": [{"tag": "wght", "currentValue": noto_wght}],
        },
        "output": {
            "weight": weight_num,
            "metadataMode": "inheritBase",
            "upm": TARGET_UPM,
        },
        "export": {
            "path": {
                "font": inst_path,
            },
        },
    }
    merge_fonts(bake_config)

    # ── Step 2: Convert to proportional + apply tracking ──
    font = TTFont(inst_path)
    _assert_target_upm(font, inst_path)
    font_upm = font["head"].unitsPerEm

    tracking_design = family["tracking"]
    tracking_kana_design = family["trackingKana"]
    tracking = _scale_design_unit(tracking_design, font_upm)
    tracking_kana = (
        None
        if tracking_kana_design is None
        else _scale_design_unit(tracking_kana_design, font_upm)
    )
    vertical_tracking_design = family.get("verticalTracking", 0)
    vertical_tracking = _scale_design_unit(vertical_tracking_design, font_upm)
    tracking_ignore = family.get("trackingIgnore")
    ss09_punctuation_chars = family.get("runtimePalt")

    desc = f"tracking +{tracking}"
    if font_upm != SOURCE_UPM:
        desc += f" ({tracking_design} at {SOURCE_UPM} UPM)"
    if tracking_kana is not None:
        kana_desc = f"kana/punct +{tracking_kana}"
        if font_upm != SOURCE_UPM:
            kana_desc += f" ({tracking_kana_design} at {SOURCE_UPM} UPM)"
        desc += f" ({kana_desc})"
    if vertical_tracking:
        vertical_desc = f"vertical JP +{vertical_tracking}"
        if font_upm != SOURCE_UPM:
            vertical_desc += f" ({vertical_tracking_design} at {SOURCE_UPM} UPM)"
        desc += f" ({vertical_desc})"
    print(f"    [2/3] Proportional (palt) + {desc}...")

    # Split U+30FB from U+2027 before metrics work. Noto maps both to
    # ``uni2027``, but U+30FB is one of the yakumono glyphs that should
    # keep an ss09 alternate while U+2027 stays on its own palt data.
    split_source = _split_cmap_codepoint_glyph(font, 0x30FB, "uni30FB")
    ss09_punctuation_glyphs = _glyphs_for_codepoints(font, ss09_punctuation_chars)

    # Use Noto's baseline palt for every weight. Thin through SemiBold carry
    # identical vendor palt values, while Bold / ExtraBold switch to a heavier
    # FeatureVariation set that looks too tight for this UI family.
    palt_data = _scale_design_adjustments(dict(_get_baseline_palt()), font_upm)
    if split_source in palt_data:
        palt_data["uni30FB"] = palt_data[split_source]
    ss09_punctuation_by_codepoint = _runtime_palt_residuals_by_codepoint(
        _feature_adjustments_for_codepoints(
            font,
            ss09_punctuation_chars,
            palt_data,
        )
    )

    # Bake Noto palt entries at full strength except ss09 yakumono, which
    # receive a reduced baked base. Their residual is captured above and later
    # exposed as final ss09 alternates. Glyphs without palt keep original hmtx.
    make_proportional(
        font,
        palt_override=palt_data,
        runtime_palt=ss09_punctuation_glyphs,
        runtime_palt_base_scale=RUNTIME_PALT_BASE_SCALE,
        install_runtime_palt=False,
    )
    _apply_tracking(font, tracking, tracking_kana, tracking_ignore)
    spacing_adjusted = _apply_glyph_spacing(
        font,
        _scale_glyph_spacing(family.get("glyphSpacing"), font_upm),
    )
    if spacing_adjusted:
        print(f"          Per-glyph spacing: {spacing_adjusted} glyph(s) adjusted")
    _strip_extreme_glyphs(font)
    vertical_tracking_adjusted = _apply_vertical_tracking(font, vertical_tracking)
    if vertical_tracking_adjusted:
        print(f"          Vertical tracking: {vertical_tracking_adjusted} glyph(s) adjusted")
    x_scale = family.get("xScale", 1.0)
    if x_scale != 1.0:
        _apply_x_scale(font, x_scale)
    font.save(prop_path)

    # ── Step 3: Merge Inter + proportional Noto ──
    family_name = family["familyName"]
    final_path = _final_ttf_path(family, weight_name)
    print(f"    [3/3] Merging {family['interPrefix']} + proportional Noto...")
    baseline_offset = _scale_design_unit(BASELINE_OFFSET, TARGET_UPM)
    merge_config = {
        "subFont": {
            "path": inter_path,
            "scale": 1.0,
            "baselineOffset": 0,
            "axes": [],
            "excludeCodepoints": SUB_EXCLUDE_CODEPOINTS,
        },
        "baseFont": {
            "path": prop_path,
            "scale": SCALE,
            "baselineOffset": baseline_offset,
            "axes": [],
        },
        "output": _final_output_metadata(
            family_name,
            weight_num,
            _project_version(),
        ),
        "export": {
            "path": {
                "font": final_path,
            },
        },
    }
    with _suppress_fonttools_coverage_warnings():
        merge_fonts(merge_config)
    final_font = TTFont(final_path)
    _assert_target_upm(final_font, final_path)
    final_font.close()
    _normalize_layout_coverage_order(final_path)
    vertical_body_adjusted = _scale_vertical_body_metrics_after_merge(
        final_path,
        prop_path,
        SCALE,
        baseline_offset,
    )
    if vertical_body_adjusted:
        print(f"          Vertical body metrics: {vertical_body_adjusted} glyph(s) adjusted")
    _refresh_ss09_feature_after_merge(
        final_path,
        _scale_feature_adjustments(ss09_punctuation_by_codepoint, SCALE),
        {},
        {},
    )
    return {
        "fontPath": final_path,
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m font.build",
        description="Build Gen Interface JP TTFs.",
    )
    parser.add_argument(
        "selection",
        nargs="*",
        help=(
            "Optional family key followed by weight filters. Examples: "
            "normal Regular Bold, all 400 700"
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=int(os.environ.get("FONT_BUILD_JOBS", "1")),
        help="Number of parallel family/weight jobs. Use 16 for the full TTF matrix.",
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be 1 or greater")
    return args


def _select_build_matrix(selection: list[str]) -> tuple[list[str], list[tuple[int, str, int]]]:
    families_to_build = list(FAMILIES.keys())
    weights_to_build = WEIGHTS

    args = list(selection)
    if args:
        first = args[0].lower()
        if first in FAMILIES or first == "all":
            if first != "all":
                families_to_build = [first]
            args = args[1:]

        if args:
            requested = {s.strip() for s in args}
            weights_to_build = [
                (n, name, nw) for n, name, nw in WEIGHTS
                if name in requested or str(n) in requested
            ]
            if not weights_to_build:
                raise ValueError(
                    f"No matching weights. Available: {[n for _, n, _ in WEIGHTS]}"
                )

    return families_to_build, weights_to_build


def _build_task(task: tuple[str, int, str, int, int, int]) -> dict:
    family_key, weight_num, weight_name, noto_wght, index, total = task
    family = FAMILIES[family_key]
    family_name = family["familyName"]
    print(f"\n[{index}/{total}] {family_name} {weight_name} ({weight_num})...")
    manifest = build_one(family, weight_num, weight_name, noto_wght)
    return {
        **manifest,
        "familyKey": family_key,
        "familyName": family_name,
        "weightNum": weight_num,
        "weightName": weight_name,
        "index": index,
        "total": total,
    }


def _task_manifest(task: tuple[str, int, str, int, int, int]) -> dict:
    family_key, weight_num, weight_name, _noto_wght, index, total = task
    family = FAMILIES[family_key]
    return {
        "fontPath": _final_ttf_path(family, weight_name),
        "familyKey": family_key,
        "familyName": family["familyName"],
        "weightNum": weight_num,
        "weightName": weight_name,
        "index": index,
        "total": total,
    }


def _child_pythonpath() -> str:
    src_path = os.path.join(ROOT, "src")
    existing = os.environ.get("PYTHONPATH")
    if existing:
        paths = existing.split(os.pathsep)
        if src_path in paths:
            return existing
        return os.pathsep.join([src_path, existing])
    return src_path


def _parallel_task_command(task: tuple[str, int, str, int, int, int]) -> list[str]:
    family_key, _weight_num, weight_name, _noto_wght, _index, _total = task
    return [
        sys.executable,
        "-m",
        "font.build",
        "--jobs",
        "1",
        family_key,
        weight_name,
    ]


def _run_parallel_build_tasks(
    tasks: list[tuple[str, int, str, int, int, int]],
    jobs: int,
) -> list[dict]:
    worker_count = min(jobs, len(tasks))
    print(
        f"\nRunning {len(tasks)} TTF build job(s) with {worker_count} worker(s).",
        flush=True,
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = _child_pythonpath()
    pending = list(tasks)
    active: list[tuple[tuple[str, int, str, int, int, int], subprocess.Popen]] = []
    results: list[dict] = []

    try:
        while pending or active:
            while pending and len(active) < worker_count:
                task = pending.pop(0)
                manifest = _task_manifest(task)
                print(
                    f"  -> starting [{manifest['index']}/{manifest['total']}] "
                    f"{manifest['familyName']} {manifest['weightName']}",
                    flush=True,
                )
                process = subprocess.Popen(
                    _parallel_task_command(task),
                    cwd=ROOT,
                    env=env,
                )
                active.append((task, process))

            for task, process in list(active):
                returncode = process.poll()
                if returncode is None:
                    continue

                active.remove((task, process))
                command = _parallel_task_command(task)
                if returncode != 0:
                    for _active_task, active_process in active:
                        active_process.terminate()
                    for _active_task, active_process in active:
                        active_process.wait()
                    raise subprocess.CalledProcessError(returncode, command)

                manifest = _task_manifest(task)
                results.append(manifest)
                print(
                    f"  -> done [{manifest['index']}/{manifest['total']}] "
                    f"{manifest['fontPath']}",
                    flush=True,
                )

            if pending or active:
                time.sleep(0.5)
    except KeyboardInterrupt:
        for _task, process in active:
            process.terminate()
        raise

    return sorted(results, key=lambda manifest: manifest["index"])


def _run_build_tasks(
    tasks: list[tuple[str, int, str, int, int, int]],
    jobs: int,
) -> list[dict]:
    if jobs == 1 or len(tasks) <= 1:
        return [_build_task(task) for task in tasks]

    return _run_parallel_build_tasks(tasks, jobs)


def main():
    """Drive the family/weight matrix from argv.

    Usage::

        python -m font.build                       # everything
        python -m font.build --jobs 16             # everything in parallel
        python -m font.build normal                # all weights of one family
        python -m font.build normal Regular Bold   # a slice
        python -m font.build all 400 700           # by weight, both families

    Argument parsing is positional and lenient: the first arg can be a
    family key (``normal`` / ``display`` / ``all``) or a weight; remaining
    args are always treated as weight filters. Weight filters match
    either by name (``Regular``) or by usWeightClass (``400``).
    """
    os.makedirs(DIST_TTF, exist_ok=True)
    cli_args = _parse_args(sys.argv[1:])
    try:
        families_to_build, weights_to_build = _select_build_matrix(cli_args.selection)
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    tasks: list[tuple[str, int, str, int, int, int]] = []
    total_tasks = len(families_to_build) * len(weights_to_build)
    index = 1
    for family_key in families_to_build:
        for weight_num, weight_name, noto_wght in weights_to_build:
            tasks.append((family_key, weight_num, weight_name, noto_wght, index, total_tasks))
            index += 1

    for family_key in families_to_build:
        family = FAMILIES[family_key]
        family_name = family["familyName"]
        print(f"\n{'='*60}")
        print(f"  {family_name}  (tracking +{family['tracking']})")
        print(f"{'='*60}")

    manifests = _run_build_tasks(tasks, cli_args.jobs)
    if cli_args.jobs == 1:
        for manifest in manifests:
            print(f"  -> {manifest['fontPath']}")

    for family_key in families_to_build:
        total = sum(1 for manifest in manifests if manifest["familyKey"] == family_key)
        family_name = FAMILIES[family_key]["familyName"]
        print(f"\n  Done. {total} weight(s) of {family_name}")

    print(f"\nAll done. Output in {DIST_TTF}")


if __name__ == "__main__":
    main()
