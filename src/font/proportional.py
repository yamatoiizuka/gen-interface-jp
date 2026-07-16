#!/usr/bin/env python3
"""
Convert a CJK font to proportional metrics using its palt GPOS feature.

CJK fonts ship with full-width metrics by default — every glyph occupies
the same em-square box regardless of its actual outline width — and rely
on the GPOS ``palt`` feature to optically narrow kana, punctuation, and
Latin-in-CJK glyphs at runtime. Apps that don't enable ``palt`` (Adobe's
Japanese composer, browser fallbacks, anything that treats CJK as
monospaced for layout) miss those adjustments and lay the text out at
full-width spacing.

This module bakes most ``palt`` values into the static hmtx so the font
reads as proportional everywhere. A caller may keep a small glyph set as a
live runtime ``palt`` / ``vpal`` feature; palt glyphs can either stay
unbaked or split into a baked base fraction plus a live residual feature.
Fresh lookups are installed after the redundant proportional features are
removed. Glyphs not covered by ``palt`` keep their original metrics —
nothing is forced.

Usage:
    python3 -m font.proportional INPUT.ttf OUTPUT.ttf
"""

from __future__ import annotations

import copy
import sys
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables


# GPOS features that provide proportional metric adjustments or vertical
# pair tightening. These become redundant or undesirable once the font's
# project-owned spacing policy has been baked, so we strip them to keep apps
# from double-applying the shrink and to keep vertical writing on a basic
# full-width grid.
#   palt  - proportional alternate widths (horizontal)
#   vpal  - proportional alternate widths (vertical)
#   halt  - alternate metrics (horizontal, ½-width / pseudo-half)
#   vhal  - alternate metrics (vertical)
#   vkrn  - vertical kerning; disabled for Gen Interface JP's fallback
#           vertical writing behavior
PROP_FEATURES = {"palt", "vpal", "halt", "vhal", "vkrn"}

SS09_FEATURE_TAG = "ss09"
SS09_ALTERNATE_SUFFIX = ".ss09"
SS09_UI_NAMES = {
    "en": "Half-width punctuation",
    "ja": "約物半角",
}


def _scale_position_adjustment(
    adjustment: tuple[int, int],
    scale: float,
) -> tuple[int, int]:
    """Scale an OpenType placement/advance pair with design-unit rounding."""
    placement, advance = adjustment
    return (round(placement * scale), round(advance * scale))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def make_proportional(
    font: TTFont,
    reduced_palt: set[str] | None = None,
    reduced_palt_scale: float = 1 / 3,
    squeeze_sb: set[str] | None = None,
    squeeze_sb_scale: float | None = None,
    palt_override: dict[str, tuple[int, int]] | None = None,
    runtime_palt: set[str] | None = None,
    runtime_palt_base_scale: float = 0.0,
    install_runtime_palt: bool = True,
    vpal_override: dict[str, tuple[int, int]] | None = None,
    runtime_vpal: set[str] | None = None,
) -> None:
    """Bake palt adjustments into hmtx in place, then strip prop features.

    Three groups of glyphs:

    - **Full palt**: glyphs in ``palt_adjustments`` that aren't called out in
      ``reduced_palt``. The XPlacement / XAdvance from palt is applied at
      full strength — outline shifts left by XPlacement, advance grows
      by XAdvance (negative = narrower).
    - **Reduced palt** (``reduced_palt``): same glyphs as palt but the
      adjustment is scaled by ``reduced_palt_scale`` (default 1/3).
      Used in this project for punctuation, where full palt feels too
      tight when set against kana that already shrank.
    - **Squeeze SB** (``squeeze_sb``): glyphs *without* palt entries that
      should still narrow proportionally. Their LSB and RSB each shrink
      by ``(1 - squeeze_sb_scale)`` so the rhythm of the squeeze stays
      consistent with reduced-palt punctuation.

    ``palt_override`` lets the caller supply palt values from elsewhere
    (e.g. the variable-font source) when the font's own GPOS palt has
    been corrupted by variable instantiation. Variable→static baking can
    leave palt ValueRecords with zeroed XPlacement/XAdvance pairs.

    ``runtime_palt`` leaves selected palt-covered glyphs available as a live
    ``palt`` feature. By default those glyphs are not baked. When
    ``runtime_palt_base_scale`` is non-zero, that fraction is baked into the
    base hmtx and only the remaining delta is reinstalled as live ``palt``.
    This lets selected yakumono stay somewhat tight when ``palt`` is disabled
    while preserving the full palt result when it is enabled.
    Callers that migrate the residual elsewhere can set
    ``install_runtime_palt=False`` while keeping the base bake behavior.

    ``vpal_override`` / ``runtime_vpal`` mirror that runtime feature path
    for vertical proportional metrics. ``vpal`` is not baked into hmtx; when
    requested, selected records are preserved as a live GPOS feature after
    the original proportional features are stripped.

    Only TrueType-outlined fonts are supported — palt baking writes back
    to ``glyf``, not to CFF.
    """
    if "glyf" not in font:
        raise ValueError("Only TrueType-outline fonts are supported")

    if squeeze_sb_scale is None:
        squeeze_sb_scale = reduced_palt_scale
    if not 0 <= runtime_palt_base_scale <= 1:
        raise ValueError("runtime_palt_base_scale must be between 0 and 1")

    runtime_palt = runtime_palt or set()
    runtime_vpal = runtime_vpal or set()

    # Extract palt adjustments before removing features
    palt_adjustments = palt_override if palt_override is not None else _read_palt(font)
    runtime_palt_adjustments = {}
    vpal_adjustments = vpal_override if vpal_override is not None else _read_vpal(font)
    runtime_vpal_adjustments = {
        glyph_name: value
        for glyph_name, value in vpal_adjustments.items()
        if glyph_name in runtime_vpal
    }

    glyf = font["glyf"]
    hmtx = font["hmtx"]

    # ── Apply palt adjustments ──
    for glyph_name, (x_placement, x_advance) in palt_adjustments.items():
        if glyph_name in runtime_palt:
            if runtime_palt_base_scale == 0:
                runtime_palt_adjustments[glyph_name] = (x_placement, x_advance)
                continue
            if glyph_name not in hmtx.metrics:
                runtime_palt_adjustments[glyph_name] = (x_placement, x_advance)
                continue
            base_x_placement, base_x_advance = _scale_position_adjustment(
                (x_placement, x_advance),
                runtime_palt_base_scale,
            )
            runtime_palt_adjustments[glyph_name] = (
                x_placement - base_x_placement,
                x_advance - base_x_advance,
            )
            x_placement = base_x_placement
            x_advance = base_x_advance
        elif glyph_name not in hmtx.metrics:
            continue

        # Reduced palt: apply a fraction of the adjustment
        if glyph_name not in runtime_palt and reduced_palt and glyph_name in reduced_palt:
            x_placement, x_advance = _scale_position_adjustment(
                (x_placement, x_advance),
                reduced_palt_scale,
            )

        aw, lsb = hmtx[glyph_name]

        # x_placement: shift the glyph origin (negative = shift left)
        # x_advance: adjust the advance width (negative = narrower)
        new_lsb = lsb + x_placement
        new_aw = aw + x_advance

        # Shift outlines by x_placement
        if x_placement != 0 and glyph_name in glyf:
            glyph = glyf[glyph_name]
            if glyph.numberOfContours != 0 and hasattr(glyph, "xMin") and glyph.xMin is not None:
                _shift_glyph_x(glyph, x_placement)

        hmtx[glyph_name] = (new_aw, new_lsb)

    # ── Squeeze sidebearings for non-palt glyphs ──
    if squeeze_sb:
        for glyph_name in squeeze_sb:
            if glyph_name in palt_adjustments:
                continue  # already handled above
            if glyph_name not in hmtx.metrics:
                continue
            if glyph_name not in glyf:
                continue

            glyph = glyf[glyph_name]
            if glyph.numberOfContours == 0:
                continue
            if not hasattr(glyph, "xMin") or glyph.xMin is None:
                continue

            aw, lsb = hmtx[glyph_name]
            bbox_w = glyph.xMax - glyph.xMin
            rsb = aw - lsb - bbox_w

            # How much to remove: (1 - scale) of each sidebearing
            cut = 1 - squeeze_sb_scale
            lsb_remove = round(lsb * cut)
            rsb_remove = round(rsb * cut)

            if lsb_remove == 0 and rsb_remove == 0:
                continue

            # Shift outlines left by lsb_remove
            if lsb_remove != 0:
                _shift_glyph_x(glyph, -lsb_remove)

            new_lsb = lsb - lsb_remove
            new_aw = aw - lsb_remove - rsb_remove
            hmtx[glyph_name] = (new_aw, new_lsb)

    # Remove proportional-metric GPOS features plus vertical kerning, then
    # install minimal live palt/vpal lookups for glyphs intentionally kept at
    # runtime.
    _remove_prop_features(font)
    if install_runtime_palt and runtime_palt_adjustments:
        _install_palt_feature(font, runtime_palt_adjustments)
    if runtime_vpal_adjustments:
        _install_vpal_feature(font, runtime_vpal_adjustments)


# ---------------------------------------------------------------------------
# GPOS palt/vpal extraction
# ---------------------------------------------------------------------------

def _read_palt(font: TTFont) -> dict[str, tuple[int, int]]:
    """Walk GPOS palt lookups and return ``{glyph_name: (XPlacement, XAdvance)}``.

    Handles SinglePos formats 1 (one ValueRecord shared by all glyphs in the
    coverage) and 2 (one ValueRecord per glyph), and unwraps Extension
    lookups (type 9). Other lookup types (PairPos, contextual) don't appear
    in real-world palt features so they're skipped silently.

    Missing GPOS, missing FeatureList, or no palt records all return an
    empty dict — callers treat the absence of palt as "leave hmtx alone".
    """
    return _read_single_pos_feature(font, "palt", "XPlacement", "XAdvance")


def _read_vpal(font: TTFont) -> dict[str, tuple[int, int]]:
    """Walk GPOS vpal lookups and return ``{glyph_name: (YPlacement, YAdvance)}``.

    Mirrors :func:`_read_palt`, but reads vertical placement / advance
    records. Missing GPOS, missing FeatureList, or no vpal records all
    return an empty dict.
    """
    return _read_single_pos_feature(font, "vpal", "YPlacement", "YAdvance")


def _read_single_pos_feature(
    font: TTFont,
    feature_tag: str,
    placement_attr: str,
    advance_attr: str,
) -> dict[str, tuple[int, int]]:
    """Read a SinglePos feature as ``{glyph_name: (placement, advance)}``.

    Multiple lookups in the same OpenType feature are applied sequentially by
    shaping engines, so records covering the same glyph must be summed rather
    than replaced by the later lookup.
    """
    gpos = font.get("GPOS")
    if gpos is None or gpos.table is None:
        return {}
    if gpos.table.FeatureList is None:
        return {}

    lookup_indices = []
    for fr in gpos.table.FeatureList.FeatureRecord:
        if fr.FeatureTag == feature_tag:
            lookup_indices.extend(fr.Feature.LookupListIndex)

    if not lookup_indices:
        return {}

    adjustments: dict[str, tuple[int, int]] = {}

    for li in lookup_indices:
        lookup = gpos.table.LookupList.Lookup[li]
        lookup_type = lookup.LookupType

        subtables = lookup.SubTable
        # Unwrap Extension lookups (type 9)
        if lookup_type == 9:
            subtables = [st.ExtSubTable for st in subtables]
        elif lookup_type != 1:
            continue

        for subtable in subtables:
            if not hasattr(subtable, "Coverage") or subtable.Coverage is None:
                continue
            glyphs = subtable.Coverage.glyphs
            for j, glyph_name in enumerate(glyphs):
                if subtable.Format == 1:
                    # Format 1: single ValueRecord for all glyphs
                    v = subtable.Value
                elif subtable.Format == 2:
                    # Format 2: array of ValueRecords
                    v = subtable.Value[j]
                else:
                    continue

                placement = getattr(v, placement_attr, 0) or 0
                advance = getattr(v, advance_attr, 0) or 0
                prev_placement, prev_advance = adjustments.get(glyph_name, (0, 0))
                adjustments[glyph_name] = (
                    prev_placement + placement,
                    prev_advance + advance,
                )

    return adjustments


# ---------------------------------------------------------------------------
# Glyph mutation helpers
# ---------------------------------------------------------------------------

def _shift_glyph_x(glyph, dx: int) -> None:
    """Translate a TrueType glyph horizontally by ``dx`` in place.

    Composite glyphs are shifted by adjusting each component's anchor
    offset rather than recursing into the referenced glyph — that keeps
    the underlying base glyph shareable with other composites and avoids
    double-shifting when both a base and a composite-of-base appear in
    the same call sequence.

    The bounding box (xMin / xMax) is updated to match. yMin/yMax are
    unaffected — this is x-only.
    """
    if glyph.isComposite():
        for component in glyph.components:
            component.x += dx
    else:
        coords = glyph.coordinates
        for i in range(len(coords)):
            x, y = coords[i]
            coords[i] = (x + dx, y)

    # Update bounding box
    glyph.xMin += dx
    glyph.xMax += dx


# ---------------------------------------------------------------------------
# GPOS feature removal
# ---------------------------------------------------------------------------

def _remove_prop_features(font: TTFont) -> None:
    """Strip palt/vpal/halt/vhal/vkrn from GPOS, keeping other features intact.

    GPOS feature indices live in two places that must stay in sync:
    the FeatureRecord list itself (the data) and the FeatureIndex arrays
    inside every LangSys (the references). Removing a record changes the
    indices of every later record, so the LangSys references need to be
    remapped — the helpers ``_filter_feature_indices`` and
    ``_remap_feature_indices`` handle the two halves of that update.

    Lookup tables are pruned after feature removal, but only for lookups no
    longer referenced by surviving features. If a palt lookup is shared by a
    kept feature, it stays referenced and is not removed.
    """
    gpos = font.get("GPOS")
    if gpos is None or gpos.table is None:
        return
    if gpos.table.FeatureList is None:
        return

    feature_list = gpos.table.FeatureList
    records = feature_list.FeatureRecord

    # Find indices of features to remove
    indices_to_remove = set()
    for i, fr in enumerate(records):
        if fr.FeatureTag in PROP_FEATURES:
            indices_to_remove.add(i)

    if not indices_to_remove:
        return

    # Remove from ScriptList references
    if gpos.table.ScriptList:
        for script_record in gpos.table.ScriptList.ScriptRecord:
            script = script_record.Script
            if script.DefaultLangSys:
                _filter_feature_indices(script.DefaultLangSys, indices_to_remove)
            if script.LangSysRecord:
                for lsr in script.LangSysRecord:
                    _filter_feature_indices(lsr.LangSys, indices_to_remove)

    # Build index remapping (old → new) for kept features
    kept = sorted(set(range(len(records))) - indices_to_remove)
    remap = {old: new for new, old in enumerate(kept)}

    # Rebuild FeatureRecord list
    feature_list.FeatureRecord = [records[i] for i in kept]
    feature_list.FeatureCount = len(feature_list.FeatureRecord)

    # Remap all feature indices in ScriptList
    if gpos.table.ScriptList:
        for script_record in gpos.table.ScriptList.ScriptRecord:
            script = script_record.Script
            if script.DefaultLangSys:
                _remap_feature_indices(script.DefaultLangSys, remap)
            if script.LangSysRecord:
                for lsr in script.LangSysRecord:
                    _remap_feature_indices(lsr.LangSys, remap)

    _prune_unreferenced_lookups(gpos.table)


def _filter_feature_indices(langsys, indices_to_remove: set) -> None:
    """Remove feature indices from a LangSys."""
    if langsys.FeatureIndex:
        langsys.FeatureIndex = [
            i for i in langsys.FeatureIndex if i not in indices_to_remove
        ]
        langsys.FeatureCount = len(langsys.FeatureIndex)


def _remap_feature_indices(langsys, remap: dict) -> None:
    """Remap feature indices in a LangSys after removal."""
    if langsys.FeatureIndex:
        langsys.FeatureIndex = [
            remap[i] for i in langsys.FeatureIndex if i in remap
        ]
        langsys.FeatureCount = len(langsys.FeatureIndex)


def _prune_unreferenced_lookups(table) -> None:
    """Remove GPOS lookups not referenced by surviving features."""
    if table.LookupList is None or table.FeatureList is None:
        return

    lookups = table.LookupList.Lookup
    used = set()
    for feature_record in table.FeatureList.FeatureRecord:
        used.update(feature_record.Feature.LookupListIndex or [])

    if used == set(range(len(lookups))):
        return

    kept = sorted(used)
    remap = {old: new for new, old in enumerate(kept)}
    table.LookupList.Lookup = [lookups[i] for i in kept]
    table.LookupList.LookupCount = len(table.LookupList.Lookup)

    for feature_record in table.FeatureList.FeatureRecord:
        feature = feature_record.Feature
        feature.LookupListIndex = [
            remap[i] for i in feature.LookupListIndex if i in remap
        ]
        feature.LookupCount = len(feature.LookupListIndex)


def _install_palt_feature(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
) -> None:
    """Install a fresh SinglePos palt feature for ``adjustments``.

    The build pipeline reads palt values from the original variable font
    because instantiated statics can carry stale ValueRecords. Rebuilding the
    palt feature from that canonical data is simpler and safer than trying to
    preserve and prune the original lookup tree.
    """
    _install_single_pos_feature(
        font,
        "palt",
        adjustments,
        "XPlacement",
        "XAdvance",
        0x0001 | 0x0004,
    )


def _install_halt_feature(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
) -> None:
    """Install a fresh SinglePos halt feature for ``adjustments``.

    Chromium enables CSS ``text-spacing-trim`` only when the font carries
    ``halt`` (Blink HanKerning early-returns otherwise). The shipped fonts
    bake reduced palt into hmtx and strip Noto's original ``halt``, so a new
    one is synthesized from the ss09 residual adjustments: applying ``halt``
    yields the same metrics as substituting the ss09 half-width alternates.
    """
    _install_single_pos_feature(
        font,
        "halt",
        adjustments,
        "XPlacement",
        "XAdvance",
        0x0001 | 0x0004,
    )


def _install_vhal_feature(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
) -> None:
    """Install a fresh SinglePos vhal feature for ``adjustments``.

    The feature is synthesized from the ss09 vertical residuals so Chromium's
    ``text-spacing-trim`` works in vertical writing (Blink checks ``vhal`` in
    vertical flows). Coverage uses vertical-form glyphs because ``vert``
    substitution runs before positioning.
    """
    _install_single_pos_feature(
        font,
        "vhal",
        adjustments,
        "YPlacement",
        "YAdvance",
        0x0002 | 0x0008,
    )


def _install_vpal_feature(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
) -> None:
    """Install a fresh SinglePos vpal feature for ``adjustments``."""
    _install_single_pos_feature(
        font,
        "vpal",
        adjustments,
        "YPlacement",
        "YAdvance",
        0x0002 | 0x0008,
    )


def _install_ss09_punctuation_feature(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
    vertical_adjustments: dict[str, tuple[int, int]] | None = None,
) -> None:
    """Install an ``ss09`` stylistic set for optional half-width yakumono.

    The default glyphs keep the reduced baked metrics. ``ss09`` substitutes
    to private alternate glyphs that carry the remaining palt delta in their
    outline position and hmtx advance. When vertical adjustments are supplied,
    the same helper can create vertical-form alternates whose vmtx carries a
    vertical metric delta. Production builds derive these vertical adjustments
    from Noto's baseline vhal feature.

    PairPos kerning is extended to alternates so enabling ``ss09`` does not
    drop existing horizontal ``kern`` pairs.
    """
    alternates = _create_metric_alternates(font, adjustments)
    alternates.update(
        _create_vertical_metric_alternates(font, vertical_adjustments or {})
    )
    if not alternates:
        return
    _extend_pairpos_for_alternates(font, alternates)
    _install_single_subst_feature(font, SS09_FEATURE_TAG, alternates)


def _create_metric_alternates(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
) -> dict[str, str]:
    """Create suffixed glyph alternates carrying residual metric deltas."""
    if not adjustments or "glyf" not in font or "hmtx" not in font:
        return {}

    glyph_order = list(font.getGlyphOrder())
    glyph_order_set = set(glyph_order)
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    vmtx = font["vmtx"] if "vmtx" in font else None
    alternates: dict[str, str] = {}

    for glyph_name, (x_placement, x_advance) in adjustments.items():
        if glyph_name not in glyph_order_set:
            continue
        if glyph_name not in glyf or glyph_name not in hmtx.metrics:
            continue
        alternate_name = f"{glyph_name}{SS09_ALTERNATE_SUFFIX}"
        if alternate_name not in glyph_order_set:
            glyph_order.append(alternate_name)
            glyph_order_set.add(alternate_name)
        alternate_glyph = copy.deepcopy(glyf[glyph_name])
        if (
            x_placement
            and alternate_glyph.numberOfContours != 0
            and hasattr(alternate_glyph, "xMin")
            and alternate_glyph.xMin is not None
        ):
            _shift_glyph_x(alternate_glyph, x_placement)
        glyf[alternate_name] = alternate_glyph

        advance_width, lsb = hmtx[glyph_name]
        hmtx[alternate_name] = (
            advance_width + x_advance,
            lsb + x_placement,
        )
        if vmtx is not None and glyph_name in vmtx.metrics:
            vmtx.metrics[alternate_name] = vmtx.metrics[glyph_name]
        alternates[glyph_name] = alternate_name

    if alternates:
        font.setGlyphOrder(glyph_order)
        if "maxp" in font:
            font["maxp"].numGlyphs = len(glyph_order)
    return alternates


def _create_vertical_metric_alternates(
    font: TTFont,
    adjustments: dict[str, tuple[int, int]],
) -> dict[str, str]:
    """Create suffixed glyph alternates carrying vertical metric deltas."""
    if not adjustments or "glyf" not in font or "hmtx" not in font or "vmtx" not in font:
        return {}

    glyph_order = list(font.getGlyphOrder())
    glyph_order_set = set(glyph_order)
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    vmtx = font["vmtx"]
    alternates: dict[str, str] = {}

    for glyph_name, (y_placement, y_advance) in adjustments.items():
        if glyph_name not in glyph_order_set:
            continue
        if glyph_name not in glyf or glyph_name not in hmtx.metrics:
            continue
        if glyph_name not in vmtx.metrics:
            continue
        alternate_name = f"{glyph_name}{SS09_ALTERNATE_SUFFIX}"
        if alternate_name not in glyph_order_set:
            glyph_order.append(alternate_name)
            glyph_order_set.add(alternate_name)
            glyf[alternate_name] = copy.deepcopy(glyf[glyph_name])
            hmtx[alternate_name] = hmtx[glyph_name]

        advance_height, top_side_bearing = vmtx[glyph_name]
        vmtx[alternate_name] = (
            advance_height + y_advance,
            top_side_bearing - y_placement,
        )
        alternates[glyph_name] = alternate_name

    if alternates:
        font.setGlyphOrder(glyph_order)
        if "maxp" in font:
            font["maxp"].numGlyphs = len(glyph_order)
    return alternates


def _install_single_subst_feature(
    font: TTFont,
    feature_tag: str,
    substitutions: dict[str, str],
) -> None:
    """Install a GSUB SingleSubst feature for ``substitutions``."""
    if not substitutions:
        return

    gsub = font.get("GSUB")
    if gsub is None or gsub.table is None:
        gsub = newTable("GSUB")
        font["GSUB"] = gsub
        gsub.table = otTables.GSUB()
        gsub.table.Version = 0x00010000

    table = gsub.table
    if getattr(table, "LookupList", None) is None:
        table.LookupList = otTables.LookupList()
        table.LookupList.Lookup = []
        table.LookupList.LookupCount = 0
    if getattr(table, "FeatureList", None) is None:
        table.FeatureList = otTables.FeatureList()
        table.FeatureList.FeatureRecord = []
        table.FeatureList.FeatureCount = 0
    _ensure_script_list(table)

    glyph_order = {glyph_name: i for i, glyph_name in enumerate(font.getGlyphOrder())}
    glyphs = [
        glyph_name for glyph_name in substitutions
        if glyph_name in glyph_order and substitutions[glyph_name] in glyph_order
    ]
    glyphs.sort(key=glyph_order.__getitem__)
    if not glyphs:
        return

    subtable = otTables.SingleSubst()
    subtable.mapping = {
        glyph_name: substitutions[glyph_name]
        for glyph_name in glyphs
    }

    lookup = otTables.Lookup()
    lookup.LookupType = 1  # SingleSubst
    lookup.LookupFlag = 0
    lookup.SubTable = [subtable]
    lookup.SubTableCount = 1

    lookup_list = table.LookupList
    lookup_index = lookup_list.LookupCount
    lookup_list.Lookup.append(lookup)
    lookup_list.LookupCount += 1

    feature = otTables.Feature()
    feature.FeatureParams = _stylistic_set_feature_params(font)
    feature.LookupListIndex = [lookup_index]
    feature.LookupCount = 1

    feature_record = otTables.FeatureRecord()
    feature_record.FeatureTag = feature_tag
    feature_record.Feature = feature

    feature_list = table.FeatureList
    feature_index = feature_list.FeatureCount
    feature_list.FeatureRecord.append(feature_record)
    feature_list.FeatureCount += 1

    for script_record in table.ScriptList.ScriptRecord:
        script = script_record.Script
        if script.DefaultLangSys:
            _append_feature_index(script.DefaultLangSys, feature_index)
        if script.LangSysRecord:
            for lsr in script.LangSysRecord:
                _append_feature_index(lsr.LangSys, feature_index)
    _sort_feature_list_and_remap_langsys(table)


def _stylistic_set_feature_params(font: TTFont):
    """Return FeatureParams for the Japanese UI label of ss09."""
    # mac=False: fontTools would otherwise add Macintosh (platformID 1)
    # records alongside the Windows ones. ofl-font-baker 0.4.8+ strips all
    # Mac name records from merged output, and this UI label is installed
    # after the merge — keep the font consistently Windows-Unicode-only.
    # Illustrator/InDesign read stylistic-set labels from the Windows
    # records (en 0x409 / ja 0x411), so the visible ss09 name is unchanged.
    name_id = font["name"].addMultilingualName(
        SS09_UI_NAMES,
        ttFont=font,
        minNameID=256,
        mac=False,
    )
    params = otTables.FeatureParamsStylisticSet()
    params.Version = 0
    params.UINameID = name_id
    return params


def _ensure_script_list(table) -> None:
    """Create a minimal DFLT ScriptList when a layout table has none."""
    if (
        getattr(table, "ScriptList", None) is not None
        and table.ScriptList.ScriptCount
    ):
        return

    langsys = otTables.LangSys()
    langsys.LookupOrder = None
    langsys.ReqFeatureIndex = 0xFFFF
    langsys.FeatureIndex = []
    langsys.FeatureCount = 0

    script = otTables.Script()
    script.DefaultLangSys = langsys
    script.LangSysRecord = []
    script.LangSysCount = 0

    script_record = otTables.ScriptRecord()
    script_record.ScriptTag = "DFLT"
    script_record.Script = script

    table.ScriptList = otTables.ScriptList()
    table.ScriptList.ScriptRecord = [script_record]
    table.ScriptList.ScriptCount = 1


def _sort_feature_list_and_remap_langsys(table) -> None:
    """Sort FeatureRecords by tag and remap LangSys feature indices."""
    feature_list = getattr(table, "FeatureList", None)
    script_list = getattr(table, "ScriptList", None)
    if feature_list is None or script_list is None:
        return
    records = list(getattr(feature_list, "FeatureRecord", None) or [])
    if not records:
        return

    sorted_pairs = sorted(
        enumerate(records),
        key=lambda item: item[1].FeatureTag,
    )
    old_to_new = {
        old_index: new_index
        for new_index, (old_index, _record) in enumerate(sorted_pairs)
    }
    feature_list.FeatureRecord = [
        record for _old_index, record in sorted_pairs
    ]
    feature_list.FeatureCount = len(feature_list.FeatureRecord)

    for script_record in getattr(script_list, "ScriptRecord", None) or []:
        script = script_record.Script
        langsystems = []
        if script.DefaultLangSys:
            langsystems.append(script.DefaultLangSys)
        for langsys_record in script.LangSysRecord or []:
            langsystems.append(langsys_record.LangSys)

        for langsys in langsystems:
            langsys.FeatureIndex = [
                old_to_new[index]
                for index in (langsys.FeatureIndex or [])
                if index in old_to_new
            ]
            langsys.FeatureCount = len(langsys.FeatureIndex)
            if getattr(langsys, "ReqFeatureIndex", 0xFFFF) != 0xFFFF:
                langsys.ReqFeatureIndex = old_to_new.get(
                    langsys.ReqFeatureIndex,
                    0xFFFF,
                )


def _extend_pairpos_for_alternates(
    font: TTFont,
    alternates: dict[str, str],
) -> None:
    """Teach existing GPOS PairPos lookups about GSUB alternate glyphs."""
    gpos = font.get("GPOS")
    if gpos is None or gpos.table is None or gpos.table.LookupList is None:
        return
    glyph_order = {glyph: i for i, glyph in enumerate(font.getGlyphOrder())}
    for lookup in gpos.table.LookupList.Lookup:
        _extend_pairpos_lookup(lookup, alternates, glyph_order)


def _extend_pairpos_lookup(
    lookup,
    alternates: dict[str, str],
    glyph_order: dict[str, int],
) -> None:
    """Extend PairPos subtables, unwrapping extension lookups when needed."""
    if lookup.LookupType == 9:
        for subtable in lookup.SubTable:
            if getattr(subtable, "ExtensionLookupType", None) == 2:
                _extend_pairpos_subtable(
                    subtable.ExtSubTable,
                    alternates,
                    glyph_order,
                )
        return
    if lookup.LookupType != 2:
        return
    for subtable in lookup.SubTable:
        _extend_pairpos_subtable(subtable, alternates, glyph_order)


def _extend_pairpos_subtable(
    subtable,
    alternates: dict[str, str],
    glyph_order: dict[str, int],
) -> None:
    if subtable.Format == 1:
        _extend_pairpos_format1(subtable, alternates, glyph_order)
    elif subtable.Format == 2:
        _extend_pairpos_format2(subtable, alternates, glyph_order)


def _extend_pairpos_format1(
    subtable,
    alternates: dict[str, str],
    glyph_order: dict[str, int],
) -> None:
    """Duplicate explicit pair records for substituted first/second glyphs."""
    if subtable.Coverage is None:
        return

    pairsets = {
        first_glyph: pairset
        for first_glyph, pairset in zip(subtable.Coverage.glyphs, subtable.PairSet)
    }
    for pairset in list(pairsets.values()):
        _extend_pairset_seconds(pairset, alternates, glyph_order)

    for first_glyph, alternate_first in alternates.items():
        if first_glyph not in pairsets or alternate_first in pairsets:
            continue
        pairsets[alternate_first] = copy.deepcopy(pairsets[first_glyph])

    entries = sorted(
        pairsets.items(),
        key=lambda item: glyph_order.get(item[0], 1_000_000),
    )
    subtable.Coverage.glyphs = [first_glyph for first_glyph, _ in entries]
    subtable.PairSet = [pairset for _, pairset in entries]
    subtable.PairSetCount = len(subtable.PairSet)


def _extend_pairset_seconds(
    pairset,
    alternates: dict[str, str],
    glyph_order: dict[str, int],
) -> None:
    existing = {
        record.SecondGlyph
        for record in pairset.PairValueRecord
    }
    records = list(pairset.PairValueRecord)
    for record in list(pairset.PairValueRecord):
        alternate_second = alternates.get(record.SecondGlyph)
        if alternate_second is None or alternate_second in existing:
            continue
        cloned = copy.deepcopy(record)
        cloned.SecondGlyph = alternate_second
        records.append(cloned)
        existing.add(alternate_second)

    records.sort(key=lambda record: glyph_order.get(record.SecondGlyph, 1_000_000))
    pairset.PairValueRecord = records
    pairset.PairValueCount = len(records)


def _extend_pairpos_format2(
    subtable,
    alternates: dict[str, str],
    glyph_order: dict[str, int],
) -> None:
    """Assign substituted glyphs to the same PairPos classes as their bases."""
    if subtable.Coverage is not None:
        coverage_glyphs = set(subtable.Coverage.glyphs)
        for first_glyph, alternate_first in alternates.items():
            if first_glyph in coverage_glyphs:
                coverage_glyphs.add(alternate_first)
        subtable.Coverage.glyphs = sorted(
            coverage_glyphs,
            key=lambda glyph: glyph_order.get(glyph, 1_000_000),
        )

    for class_def in (subtable.ClassDef1, subtable.ClassDef2):
        if class_def is None:
            continue
        for glyph_name, alternate_name in alternates.items():
            class_value = class_def.classDefs.get(glyph_name)
            if class_value is not None and alternate_name not in class_def.classDefs:
                class_def.classDefs[alternate_name] = class_value


def _install_single_pos_feature(
    font: TTFont,
    feature_tag: str,
    adjustments: dict[str, tuple[int, int]],
    placement_attr: str,
    advance_attr: str,
    value_format: int,
) -> None:
    """Install a fresh SinglePos feature for ``adjustments``."""
    if not adjustments:
        return

    gpos = font.get("GPOS")
    if gpos is None or gpos.table is None:
        return

    table = gpos.table
    if table.LookupList is None:
        table.LookupList = otTables.LookupList()
        table.LookupList.Lookup = []
        table.LookupList.LookupCount = 0
    if table.FeatureList is None:
        table.FeatureList = otTables.FeatureList()
        table.FeatureList.FeatureRecord = []
        table.FeatureList.FeatureCount = 0

    glyph_order = {glyph_name: i for i, glyph_name in enumerate(font.getGlyphOrder())}
    glyphs = [
        glyph_name for glyph_name in adjustments
        if glyph_name in glyph_order
    ]
    glyphs.sort(key=glyph_order.__getitem__)
    if not glyphs:
        return

    coverage = otTables.Coverage()
    coverage.glyphs = glyphs

    subtable = otTables.SinglePos()
    subtable.Format = 2
    subtable.Coverage = coverage
    subtable.ValueFormat = value_format
    subtable.Value = []
    for glyph_name in glyphs:
        placement, advance = adjustments[glyph_name]
        value = otTables.ValueRecord()
        setattr(value, placement_attr, placement)
        setattr(value, advance_attr, advance)
        subtable.Value.append(value)
    subtable.ValueCount = len(subtable.Value)

    lookup = otTables.Lookup()
    lookup.LookupType = 1  # SinglePos
    lookup.LookupFlag = 0
    lookup.SubTable = [subtable]
    lookup.SubTableCount = 1

    lookup_list = table.LookupList
    lookup_index = lookup_list.LookupCount
    lookup_list.Lookup.append(lookup)
    lookup_list.LookupCount += 1

    feature = otTables.Feature()
    feature.LookupListIndex = [lookup_index]
    feature.LookupCount = 1

    feature_record = otTables.FeatureRecord()
    feature_record.FeatureTag = feature_tag
    feature_record.Feature = feature

    feature_list = table.FeatureList
    feature_index = feature_list.FeatureCount
    feature_list.FeatureRecord.append(feature_record)
    feature_list.FeatureCount += 1

    if table.ScriptList:
        for script_record in table.ScriptList.ScriptRecord:
            script = script_record.Script
            if script.DefaultLangSys:
                _append_feature_index(script.DefaultLangSys, feature_index)
            if script.LangSysRecord:
                for lsr in script.LangSysRecord:
                    _append_feature_index(lsr.LangSys, feature_index)


def _append_feature_index(langsys, feature_index: int) -> None:
    """Append ``feature_index`` to a LangSys if it is not already present."""
    feature_indices = list(langsys.FeatureIndex or [])
    if feature_index not in feature_indices:
        feature_indices.append(feature_index)
    langsys.FeatureIndex = feature_indices
    langsys.FeatureCount = len(feature_indices)


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} INPUT.ttf OUTPUT.ttf")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]

    font = TTFont(input_path)
    make_proportional(font)
    font.save(output_path)
    print(f"Proportional font saved to {output_path}")


if __name__ == "__main__":
    main()
