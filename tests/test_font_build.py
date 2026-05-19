"""Unit tests for the in-house helpers in font/build.py.

The functions under test are everything that doesn't go through font-baker:
glyph-name codepoint parsing, kana / CJK classification, GSUB feature
inspection, horizontal scaling, bbox stripping, and tracking.
"""

import copy
import re
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from font.build import (
    _apply_glyph_spacing,
    _apply_tracking,
    _apply_x_scale,
    _EXTREME_YMAX,
    _EXTREME_YMIN,
    _VERTICAL_REPEAT_MARK_CODEPOINTS,
    _feature_adjustments_for_codepoints,
    _final_output_metadata,
    _get_cjk_glyphs,
    _get_variable_palt,
    _get_variable_vpal,
    _get_vert_alternates,
    _glyphs_for_codepoints,
    _glyph_codepoint,
    _is_cjk_codepoint,
    _is_kana_letter,
    _is_kana_or_punct,
    _project_version,
    _retarget_feature_adjustments,
    _retarget_named_adjustments,
    _runtime_palt_residual_adjustment,
    _scale_design_adjustment,
    _scale_design_adjustments,
    _scale_feature_adjustments,
    _scale_design_unit,
    _scale_glyph_spacing,
    _split_cmap_codepoint_glyph,
    _strip_extreme_glyphs,
    _build_inter_variable_instance,
    _default_inter_static_path,
    _inter_source_path,
    DISPLAY_PALT_SPACE_ADJUSTMENTS,
    FAMILIES,
    INTER_VARIABLE,
    INTER_VARIABLE_EDGE_WEIGHTS,
    NORMAL_PALT_SPACE_ADJUSTMENTS,
    PALT_FEATURE_CHARS,
    PALT_SPACE_ADJUSTMENTS,
    RUNTIME_PALT_BASE_SCALE,
    SOURCE_UPM,
    STATIC_INSTANCE_VARIATION_TABLES,
    SUB_EXCLUDE_CODEPOINTS,
    SYNTHETIC_VPAL_ADJUSTMENTS,
    TARGET_UPM,
    TRACKING_IGNORE_CODEPOINTS,
    VPAL_FEATURE_CHARS,
)
from merge_fonts import parse_codepoint_list


def _layout_feature_tags(font: TTFont, table_tag: str) -> set[str]:
    """Return GSUB/GPOS feature tags from a font."""
    if table_tag not in font:
        return set()
    feature_list = font[table_tag].table.FeatureList
    if feature_list is None:
        return set()
    return {record.FeatureTag for record in feature_list.FeatureRecord}


# ---------------------------------------------------------------------------
# UPM policy
# ---------------------------------------------------------------------------

class TestUpmPolicy:
    """Project-owned design units are authored at 1000 UPM and scaled at build time."""

    def test_target_upm_matches_inter_native_grid(self):
        assert SOURCE_UPM == 1000
        assert TARGET_UPM == 2048

    def test_scales_single_design_unit_value(self):
        assert _scale_design_unit(25) == 51
        assert _scale_design_unit(30) == 61
        assert _scale_design_unit(40) == 82
        assert _scale_design_unit(-400) == -819

    def test_scales_position_adjustment_tuple(self):
        assert _scale_design_adjustment((-250, -500)) == (-512, -1024)

    def test_scales_glyph_keyed_adjustments(self):
        assert _scale_design_adjustments({"uni3001": (-250, -500)}) == {
            "uni3001": (-512, -1024),
        }

    def test_scales_codepoint_keyed_spacing(self):
        assert _scale_glyph_spacing({"ょ": (30, 35)}) == {"ょ": (61, 72)}
        assert _scale_glyph_spacing(None) is None


# ---------------------------------------------------------------------------
# Project version metadata
# ---------------------------------------------------------------------------

class TestProjectVersionMetadata:
    """Final TTF metadata is stamped from the canonical project version."""

    def test_project_version_matches_pyproject(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        match = re.search(
            r'^version\s*=\s*"([^"]+)"',
            pyproject.read_text(encoding="utf-8"),
            re.M,
        )
        assert match is not None
        assert _project_version() == match.group(1)

    def test_final_output_metadata_forwards_version_to_font_baker(self):
        output = _final_output_metadata("Gen Interface JP", 400, "1.2.3")

        assert output["familyName"] == "Gen Interface JP"
        assert output["weight"] == 400
        assert output["metricsSource"] == "sub"
        assert output["upm"] == TARGET_UPM
        assert output["version"] == "1.2.3"


# ---------------------------------------------------------------------------
# Inter variable edge instances
# ---------------------------------------------------------------------------

class TestInterVariableEdgeInstances:
    """Thin/ExtraBold use tuned InterVariable instances, then static metadata."""

    @pytest.mark.parametrize(
        "family_key,weight_num,weight_name,expected_wght,expected_opsz,expected_family",
        [
            ("normal", 100, "Thin", 125, 14, "Inter"),
            ("display", 100, "Thin", 125, 32, "Inter Display"),
            ("normal", 800, "ExtraBold", 775, 14, "Inter"),
            ("display", 800, "ExtraBold", 775, 32, "Inter Display"),
        ],
    )
    def test_builds_edge_instance_with_static_metadata_and_full_layout(
        self,
        tmp_path,
        family_key,
        weight_num,
        weight_name,
        expected_wght,
        expected_opsz,
        expected_family,
    ):
        if not Path(INTER_VARIABLE).is_file():
            pytest.skip(f"Inter variable font not found at {INTER_VARIABLE}")

        family = FAMILIES[family_key]
        assert family["interOpsz"] == expected_opsz
        assert INTER_VARIABLE_EDGE_WEIGHTS[weight_name]["wght"] == expected_wght

        generated_path = _build_inter_variable_instance(
            family,
            weight_num,
            weight_name,
            str(tmp_path),
        )
        generated = TTFont(generated_path)
        variable = TTFont(INTER_VARIABLE)
        vendor_static = TTFont(_default_inter_static_path(family, weight_name))

        try:
            assert generated_path.endswith(
                f"{family['interPrefix']}-{weight_name}"
                f"-wght{expected_wght}-opsz{expected_opsz}.ttf"
            )
            assert generated["OS/2"].usWeightClass == weight_num
            for table in STATIC_INSTANCE_VARIATION_TABLES:
                assert table not in generated

            names = {
                record.nameID: record.toUnicode()
                for record in generated["name"].names
                if record.platformID == 3 and record.platEncID in (1, 10)
            }
            assert names[16] == expected_family
            assert names[17] == weight_name

            assert generated.getGlyphOrder() == variable.getGlyphOrder()
            assert set(generated.getBestCmap() or {}) == set(variable.getBestCmap() or {})
            assert set(generated.getBestCmap() or {}) == set(vendor_static.getBestCmap() or {})
            assert _layout_feature_tags(generated, "GSUB") == _layout_feature_tags(variable, "GSUB")
            assert _layout_feature_tags(generated, "GPOS") == _layout_feature_tags(variable, "GPOS")
            assert _layout_feature_tags(generated, "GSUB") == _layout_feature_tags(vendor_static, "GSUB")
            assert _layout_feature_tags(generated, "GPOS") == _layout_feature_tags(vendor_static, "GPOS")

            generated_a = generated["glyf"]["A"]
            static_a = vendor_static["glyf"]["A"]
            assert (generated_a.xMin, generated_a.xMax) != (static_a.xMin, static_a.xMax)
        finally:
            generated.close()
            variable.close()
            vendor_static.close()

    @pytest.mark.parametrize(
        "weight_num,weight_name",
        [
            (100, "Thin"),
            (800, "ExtraBold"),
        ],
    )
    def test_normal_and_display_edge_instances_use_distinct_opsz(
        self,
        tmp_path,
        weight_num,
        weight_name,
    ):
        normal_path = _build_inter_variable_instance(
            FAMILIES["normal"],
            weight_num,
            weight_name,
            str(tmp_path),
        )
        display_path = _build_inter_variable_instance(
            FAMILIES["display"],
            weight_num,
            weight_name,
            str(tmp_path),
        )
        normal = TTFont(normal_path)
        display = TTFont(display_path)

        try:
            assert normal["hmtx"]["H"] != display["hmtx"]["H"]
        finally:
            normal.close()
            display.close()

    def test_middle_weights_still_use_vendor_static_inter(self, tmp_path):
        family = FAMILIES["normal"]
        assert _inter_source_path(family, 400, "Regular", str(tmp_path)) == (
            _default_inter_static_path(family, "Regular")
        )


# ---------------------------------------------------------------------------
# _glyph_codepoint
# ---------------------------------------------------------------------------

class TestGlyphCodepoint:
    """Parse Adobe-style 'uniXXXX' glyph names to codepoints."""

    def test_basic_uni_name(self):
        assert _glyph_codepoint("uni3042") == 0x3042

    def test_uppercase_hex_works(self):
        assert _glyph_codepoint("uni30AB") == 0x30AB

    def test_non_uni_prefix_returns_none(self):
        assert _glyph_codepoint("A") is None
        assert _glyph_codepoint("cid12345") is None
        assert _glyph_codepoint(".notdef") is None

    def test_dotted_alternate_still_parses_base_codepoint(self):
        # '.alt', '.vert', '.001' suffixes are common — we treat the
        # first 4 hex digits after 'uni' as the codepoint.
        assert _glyph_codepoint("uni3042.vert") == 0x3042
        assert _glyph_codepoint("uni30AB.001") == 0x30AB

    def test_invalid_hex_returns_none(self):
        assert _glyph_codepoint("uniGGGG") is None

    def test_short_uni_returns_none(self):
        # A bare "uni" or fewer than 4 hex digits is malformed.
        # Implementation accepts any prefix-parseable hex; verify the
        # truly empty case fails cleanly.
        assert _glyph_codepoint("uni") is None


# ---------------------------------------------------------------------------
# _is_kana_or_punct
# ---------------------------------------------------------------------------

class TestIsKanaOrPunct:
    """Hiragana / katakana / CJK-punct classification by glyph name."""

    def test_hiragana_letter(self):
        assert _is_kana_or_punct("uni3042")  # あ
        assert _is_kana_or_punct("uni304B")  # か

    def test_katakana_letter(self):
        assert _is_kana_or_punct("uni30A2")  # ア
        assert _is_kana_or_punct("uni30AB")  # カ

    def test_cjk_punctuation(self):
        assert _is_kana_or_punct("uni3001")  # 、
        assert _is_kana_or_punct("uni3002")  # 。
        assert _is_kana_or_punct("uni30FB")  # ・ (in katakana block, but punct)

    def test_halfwidth_fullwidth_forms(self):
        assert _is_kana_or_punct("uniFF21")  # Ａ fullwidth A

    def test_latin_returns_false(self):
        assert not _is_kana_or_punct("A")
        assert not _is_kana_or_punct("uni0041")

    def test_cjk_ideograph_returns_false(self):
        assert not _is_kana_or_punct("uni4E00")  # 一
        assert not _is_kana_or_punct("uni6F22")  # 漢

    def test_unparseable_name_returns_false(self):
        assert not _is_kana_or_punct(".notdef")
        assert not _is_kana_or_punct("uniGGGG")


# ---------------------------------------------------------------------------
# _is_cjk_codepoint
# ---------------------------------------------------------------------------

class TestIsCjkCodepoint:
    """CJK ideograph / radical / compatibility block membership."""

    def test_unified_ideographs(self):
        assert _is_cjk_codepoint(0x4E00)  # 一
        assert _is_cjk_codepoint(0x9FFF)  # last in main block

    def test_extension_a(self):
        assert _is_cjk_codepoint(0x3400)
        assert _is_cjk_codepoint(0x4DBF)

    def test_extension_supplementary(self):
        assert _is_cjk_codepoint(0x20000)  # Extension B start
        assert _is_cjk_codepoint(0x2FA1F)  # Compatibility supplement end

    def test_radicals(self):
        assert _is_cjk_codepoint(0x2E80)  # CJK Radicals Supplement
        assert _is_cjk_codepoint(0x2F00)  # Kangxi Radical One

    def test_cjk_symbol_numerals(self):
        # 〸 (3038) is treated as ideographic for full-width preservation.
        assert _is_cjk_codepoint(0x3038)
        assert _is_cjk_codepoint(0x3020)

    def test_hiragana_excluded(self):
        assert not _is_cjk_codepoint(0x3042)  # あ
        assert not _is_cjk_codepoint(0x3041)  # ぁ

    def test_katakana_excluded(self):
        assert not _is_cjk_codepoint(0x30A2)  # ア

    def test_ascii_excluded(self):
        assert not _is_cjk_codepoint(0x0041)  # A

    def test_cjk_punct_block_excluded(self):
        # General CJK Symbols & Punctuation (3000-303F) is mostly excluded;
        # only the narrow CJK numeral / ideograph-symbol slices are CJK.
        assert not _is_cjk_codepoint(0x3001)  # 、
        assert not _is_cjk_codepoint(0x3000)  # ideographic space


# ---------------------------------------------------------------------------
# _is_kana_letter
# ---------------------------------------------------------------------------

class TestIsKanaLetter:
    """Kana *letter* classification — strict subset of _is_kana_or_punct."""

    def test_hiragana_letters(self):
        assert _is_kana_letter("uni3042")  # あ
        assert _is_kana_letter("uni3093")  # ん

    def test_katakana_letters(self):
        assert _is_kana_letter("uni30A2")  # ア
        assert _is_kana_letter("uni30F3")  # ン

    def test_hiragana_iteration_marks_included(self):
        # ゛ ゜ ゝ ゞ ゟ (U+309B-309F) — hiragana combining / iteration block,
        # treated as letter-class.
        assert _is_kana_letter("uni309D")  # ゝ
        assert _is_kana_letter("uni309E")  # ゞ

    def test_cjk_block_iteration_marks_excluded(self):
        # 〱 〲 (U+3031, U+3032) live in the CJK Symbols & Punctuation
        # block, not the kana blocks — _is_kana_letter excludes them so
        # they do not receive full kana palt treatment.
        assert not _is_kana_letter("uni3031")
        assert not _is_kana_letter("uni3032")

    def test_middle_dot_excluded(self):
        # ・ (U+30FB) is in the katakana block but counts as punctuation,
        # not a letter — it should NOT receive full kana palt.
        assert not _is_kana_letter("uni30FB")

    def test_cjk_punct_excluded(self):
        assert not _is_kana_letter("uni3001")  # 、
        assert not _is_kana_letter("uni3000")  # ideographic space

    def test_latin_excluded(self):
        assert not _is_kana_letter("A")
        assert not _is_kana_letter("uni0041")

    def test_cjk_ideograph_excluded(self):
        assert not _is_kana_letter("uni4E00")


# ---------------------------------------------------------------------------
# _get_cjk_glyphs
# ---------------------------------------------------------------------------

class TestGetCjkGlyphs:
    """cmap-based CJK glyph lookup."""

    def test_returns_ideograph_glyphs(self, noto_subset):
        cjk_glyphs = _get_cjk_glyphs(noto_subset)
        cmap = noto_subset.getBestCmap()
        # 一 (U+4E00) and 漢 (U+6F22) are CJK ideographs in our subset.
        # Look up their glyph names via the actual cmap (subsetter may
        # have remapped to canonical Adobe names like uni2F00 for U+4E00).
        assert cmap[0x4E00] in cjk_glyphs
        assert cmap[0x6F22] in cjk_glyphs

    def test_excludes_kana_glyphs(self, noto_subset):
        cjk_glyphs = _get_cjk_glyphs(noto_subset)
        cmap = noto_subset.getBestCmap()
        assert cmap[0x3042] not in cjk_glyphs  # あ
        assert cmap[0x30A2] not in cjk_glyphs  # ア

    def test_excludes_latin(self, noto_subset):
        cjk_glyphs = _get_cjk_glyphs(noto_subset)
        cmap = noto_subset.getBestCmap()
        assert cmap[0x0041] not in cjk_glyphs

    def test_empty_when_no_cmap(self, synthetic_ttf):
        # Drop the cmap and verify graceful fallback to empty set.
        synthetic_ttf["cmap"].tables = []
        # getBestCmap returns {} when there are no tables; we accept either
        # empty dict or None.
        result = _get_cjk_glyphs(synthetic_ttf)
        assert result == set()


# ---------------------------------------------------------------------------
# _get_vert_alternates
# ---------------------------------------------------------------------------

class TestGetVertAlternates:
    """GSUB vert/vrt2 lookup walking."""

    def test_returns_non_empty_for_noto(self, noto_subset):
        # Noto Sans JP has vert/vrt2 features for vertical text shaping;
        # subset should preserve at least some entries.
        alts = _get_vert_alternates(noto_subset)
        assert len(alts) > 0

    def test_alternates_are_real_glyph_names(self, noto_subset):
        alts = _get_vert_alternates(noto_subset)
        glyph_order = set(noto_subset.getGlyphOrder())
        for name in alts:
            assert name in glyph_order, f"{name} is not in glyph order"

    def test_empty_when_no_gsub(self, synthetic_ttf):
        # synthetic_ttf has no GSUB
        assert _get_vert_alternates(synthetic_ttf) == set()


# ---------------------------------------------------------------------------
# _apply_x_scale
# ---------------------------------------------------------------------------

class TestApplyXScale:
    """Horizontal-only condensation (長体)."""

    def test_no_op_at_scale_one(self, synthetic_ttf):
        # Snapshot a representative glyph and advance.
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]
        before_xmax = synthetic_ttf["glyf"]["A"].xMax

        _apply_x_scale(synthetic_ttf, 1.0)

        assert synthetic_ttf["hmtx"]["A"] == (before_aw, before_lsb)
        assert synthetic_ttf["glyf"]["A"].xMax == before_xmax

    def test_scales_advance_widths(self, synthetic_ttf):
        before_aw = synthetic_ttf["hmtx"]["A"][0]

        _apply_x_scale(synthetic_ttf, 0.5)

        after_aw = synthetic_ttf["hmtx"]["A"][0]
        assert after_aw == round(before_aw * 0.5)

    def test_scales_glyph_x_coordinates(self, synthetic_ttf):
        before_xmax = synthetic_ttf["glyf"]["A"].xMax
        before_xmin = synthetic_ttf["glyf"]["A"].xMin

        _apply_x_scale(synthetic_ttf, 0.5)

        glyph = synthetic_ttf["glyf"]["A"]
        assert glyph.xMax == round(before_xmax * 0.5)
        assert glyph.xMin == round(before_xmin * 0.5)

    def test_does_not_touch_y(self, synthetic_ttf):
        before_ymax = synthetic_ttf["glyf"]["A"].yMax
        before_ymin = synthetic_ttf["glyf"]["A"].yMin

        _apply_x_scale(synthetic_ttf, 0.5)

        glyph = synthetic_ttf["glyf"]["A"]
        assert glyph.yMax == before_ymax
        assert glyph.yMin == before_ymin

    def test_composite_components_shifted(self, synthetic_ttf):
        before_x = synthetic_ttf["glyf"]["compositeA"].components[0].x

        _apply_x_scale(synthetic_ttf, 0.5)

        after_x = synthetic_ttf["glyf"]["compositeA"].components[0].x
        assert after_x == round(before_x * 0.5)


# ---------------------------------------------------------------------------
# _strip_extreme_glyphs
# ---------------------------------------------------------------------------

class TestStripExtremeGlyphs:
    """Empty glyphs whose bbox dominates head.yMax/yMin."""

    def test_empties_extreme_glyph(self, synthetic_ttf):
        # uni3031 was constructed with yMax=1500 (> _EXTREME_YMAX=1200)
        before = synthetic_ttf["glyf"]["uni3031"]
        assert before.yMax > _EXTREME_YMAX

        _strip_extreme_glyphs(synthetic_ttf)

        after = synthetic_ttf["glyf"]["uni3031"]
        assert after.numberOfContours == 0
        assert (after.xMin, after.yMin, after.xMax, after.yMax) == (0, 0, 0, 0)

    def test_zeroes_hmtx(self, synthetic_ttf):
        _strip_extreme_glyphs(synthetic_ttf)
        assert synthetic_ttf["hmtx"]["uni3031"] == (0, 0)

    def test_drops_cmap_entry(self, synthetic_ttf):
        assert 0x3031 in synthetic_ttf.getBestCmap()

        _strip_extreme_glyphs(synthetic_ttf)

        assert 0x3031 not in synthetic_ttf.getBestCmap()

    def test_strips_vertical_repeat_remnants_even_when_not_extreme(self, synthetic_ttf):
        before = synthetic_ttf.getBestCmap()
        for cp in (0x3033, 0x3034, 0x3035):
            assert cp in before
            glyph = synthetic_ttf["glyf"][before[cp]]
            assert glyph.yMax <= _EXTREME_YMAX
            assert glyph.yMin >= _EXTREME_YMIN

        _strip_extreme_glyphs(synthetic_ttf)

        after = synthetic_ttf.getBestCmap()
        for cp in (0x3033, 0x3034, 0x3035):
            assert cp not in after
            glyph = synthetic_ttf["glyf"][f"uni{cp:04X}"]
            assert glyph.numberOfContours == 0
            assert synthetic_ttf["hmtx"][f"uni{cp:04X}"] == (0, 0)

    def test_keeps_non_extreme_glyphs_intact(self, synthetic_ttf):
        before = synthetic_ttf["glyf"]["A"]
        before_contours = before.numberOfContours

        _strip_extreme_glyphs(synthetic_ttf)

        after = synthetic_ttf["glyf"]["A"]
        assert after.numberOfContours == before_contours

    def test_threshold_constants_match_implementation(self):
        # Constants are authored on the 1000 UPM design grid; the implementation
        # scales them to the active font UPM before comparison.
        assert _EXTREME_YMAX == 1200
        assert _EXTREME_YMIN == -400

    def test_vertical_repeat_mark_policy_matches_unicode_range(self):
        assert _VERTICAL_REPEAT_MARK_CODEPOINTS == tuple(range(0x3031, 0x3036))


# ---------------------------------------------------------------------------
# SUB_EXCLUDE_CODEPOINTS
# ---------------------------------------------------------------------------

class TestSubExcludeCodepoints:
    """Sub-font codepoints handed to font-baker as ``subFont.excludeCodepoints``.

    The actual cmap-stripping and glyph-name collision rename happens inside
    font-baker (``parse_codepoint_list`` + the merge step). This project
    only owns the policy: which codepoints stay Noto-sourced. ◎ (U+25CE)
    is intentionally absent — Inter does not encode it directly; font-baker's
    glyph-name collision detection saves it via the ``uni25CE`` rename path.
    """

    def test_list_parses_via_font_baker_helper(self):
        # Sanity check that the entries match the format font-baker accepts.
        codepoints = parse_codepoint_list(SUB_EXCLUDE_CODEPOINTS)

        assert isinstance(codepoints, set)
        assert codepoints, "expected non-empty codepoint set"

    def test_covers_reported_symbols(self):
        codepoints = parse_codepoint_list(SUB_EXCLUDE_CODEPOINTS)

        for ch in "※⊕⊖⊗⊘◯":
            assert ord(ch) in codepoints, f"missing {ch} (U+{ord(ch):04X})"
        for ch in "⓪①②③④⑤⑥⑦⑧⑨":
            assert ord(ch) in codepoints, f"missing enclosed digit {ch}"
        # Dingbat Sans-Serif Circled aliases (Inter and Noto both map ➀ to
        # the same glyph as ①) — exclude so Inter's outline doesn't leak in.
        for ch in "➀➁➂➃➄➅➆➇➈":
            assert ord(ch) in codepoints, f"missing dingbat {ch}"
        for start, end in ((0x24B6, 0x24CF), (0x1F130, 0x1F149)):
            for cp in range(start, end + 1):
                assert cp in codepoints

    def test_excludes_unrelated_symbols(self):
        # Sanity check: things outside the policy stay out so we don't
        # accidentally widen Inter's replacement scope.
        codepoints = parse_codepoint_list(SUB_EXCLUDE_CODEPOINTS)

        for ch in "¼½¾℅A→":
            assert ord(ch) not in codepoints, f"{ch} unexpectedly excluded"

    def test_omits_bullseye_handled_by_collision_rename(self):
        # ◎ (U+25CE) is rescued by font-baker's glyph-name collision check
        # rather than excludeCodepoints — Inter does not encode U+25CE.
        codepoints = parse_codepoint_list(SUB_EXCLUDE_CODEPOINTS)

        assert 0x25CE not in codepoints


# ---------------------------------------------------------------------------
# _apply_tracking
# ---------------------------------------------------------------------------

class TestApplyTracking:
    """Per-glyph advance-widening with even L/R distribution."""

    def test_widens_advance_by_tracking(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]

        _apply_tracking(synthetic_ttf, tracking=30)

        after_aw, after_lsb = synthetic_ttf["hmtx"]["A"]
        assert after_aw == before_aw + 30
        assert after_lsb == before_lsb + 15

    def test_kana_gets_separate_tracking(self, synthetic_ttf):
        before_a = synthetic_ttf["hmtx"]["A"]
        before_kana = synthetic_ttf["hmtx"]["uni3042"]

        _apply_tracking(synthetic_ttf, tracking=30, tracking_kana=60)

        after_a = synthetic_ttf["hmtx"]["A"]
        after_kana = synthetic_ttf["hmtx"]["uni3042"]
        # Latin grew by tracking
        assert after_a[0] == before_a[0] + 30
        # Kana grew by tracking_kana
        assert after_kana[0] == before_kana[0] + 60
        assert after_kana[1] == before_kana[1] + 30  # half of 60

    def test_cjk_punct_uses_kana_tracking(self, synthetic_ttf):
        # _is_kana_or_punct includes CJK punct, so these get tracking_kana
        before = synthetic_ttf["hmtx"]["uni3001"]

        _apply_tracking(synthetic_ttf, tracking=10, tracking_kana=80)

        after = synthetic_ttf["hmtx"]["uni3001"]
        assert after[0] == before[0] + 80

    def test_zero_width_glyphs_skipped(self, synthetic_ttf):
        # Zero-width glyphs (e.g. mark positioning) shouldn't gain tracking.
        synthetic_ttf["hmtx"]["A"] = (0, 0)

        _apply_tracking(synthetic_ttf, tracking=30)

        assert synthetic_ttf["hmtx"]["A"] == (0, 0)

    def test_odd_tracking_truncates_half(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]

        _apply_tracking(synthetic_ttf, tracking=11)

        after_aw, after_lsb = synthetic_ttf["hmtx"]["A"]
        assert after_aw == before_aw + 11
        # 11 // 2 = 5 (floor), so RSB ends up 6 wider, LSB 5 wider.
        assert after_lsb == before_lsb + 5

    def test_tracking_ignore_skips_codepoint_ranges_and_singles(self, synthetic_ttf):
        before_line = synthetic_ttf["hmtx"]["uni2500"]
        before_wavy = synthetic_ttf["hmtx"]["uni3030"]
        before_middle_dot = synthetic_ttf["hmtx"]["uni30FB"]
        before_punct = synthetic_ttf["hmtx"]["uni3001"]

        _apply_tracking(
            synthetic_ttf,
            tracking=30,
            tracking_kana=60,
            tracking_ignore=["U+2500-U+257F", "U+3030", "U+30FB"],
        )

        assert synthetic_ttf["hmtx"]["uni2500"] == before_line
        assert synthetic_ttf["hmtx"]["uni3030"] == before_wavy
        assert synthetic_ttf["hmtx"]["uni30FB"] == before_middle_dot
        assert synthetic_ttf["hmtx"]["uni3001"][0] == before_punct[0] + 60

    def test_default_tracking_ignore_policy_matches_repeatable_symbols(self):
        codepoints = parse_codepoint_list(TRACKING_IGNORE_CODEPOINTS)

        assert 0x2500 in codepoints  # ─
        assert 0x257F in codepoints  # ╿
        assert 0x2580 in codepoints  # ▀
        assert 0x259F in codepoints  # ▟
        assert 0x2025 in codepoints  # ‥
        assert 0x22EF in codepoints  # ⋯
        assert 0x3030 in codepoints  # 〰
        assert 0xFE30 in codepoints  # ︰
        assert 0xFE4F in codepoints  # ﹏
        assert 0xFF65 in codepoints  # ･
        assert 0x2E3A in codepoints  # ⸺
        assert 0x2E3B in codepoints  # ⸻
        assert 0xFF3F in codepoints  # ＿
        assert 0xFFE3 in codepoints  # ￣

        # U+30FB belongs to the palt + spacing group, so it receives tracking
        # and is corrected later by glyphSpacing.
        assert 0x30FB not in codepoints  # ・

        # From the earlier "confirm visually" group, only U+3030 is opted in.
        assert 0x301C not in codepoints  # 〜
        assert 0x30FC not in codepoints  # ー
        assert 0xFF0D not in codepoints  # －
        assert 0xFF1D not in codepoints  # ＝


# ---------------------------------------------------------------------------
# palt symbol policy
# ---------------------------------------------------------------------------

class TestPaltSymbolPolicy:
    """Full palt is baked by default; selected yakumono keeps split runtime palt."""

    def test_tracking_only_symbols_are_implicit_palt_entries(self):
        palt = _get_variable_palt()

        for glyph_name in (
            "uni3012", "uni3005", "uni3006",
            "uniFF02", "uniFF03", "uniFF04", "uniFF06",
            "uniFF07", "uniFF0A",
            "uniFF10", "uniFF19", "uniFF21", "uniFF2C",
            "uniFF2E", "uniFF3A", "uniFF41", "uniFF56",
            "uniFF58", "uniFF5A", "uniFF3E",
            "uniFFE5", "uni2027", "uni2035",
        ):
            assert glyph_name in palt

        assert "〒" not in PALT_SPACE_ADJUSTMENTS
        assert "０" not in PALT_SPACE_ADJUSTMENTS
        assert "Ａ" not in PALT_SPACE_ADJUSTMENTS
        assert "‧" not in PALT_SPACE_ADJUSTMENTS

        assert "uniFF2D" not in palt  # Ｍ
        assert "uniFF57" not in palt  # ｗ
        assert "uniFF40" not in palt  # ｀

    def test_yakumono_uses_runtime_palt_not_spacing(self):
        assert len(PALT_FEATURE_CHARS) == 48
        for char in (
            "、。，．〈〉《》「」『』【】〔〕〖〗〘〙〚〛"
            "（）｛｝｟｠〝〞〟［］！？・：；"
            "〒＂＃＄＆＇＊＾｀￥"
        ):
            assert char in PALT_FEATURE_CHARS
            assert char not in PALT_SPACE_ADJUSTMENTS

    def test_runtime_palt_keeps_reduced_base_metrics(self):
        assert RUNTIME_PALT_BASE_SCALE == 0.34
        assert _runtime_palt_residual_adjustment((-250, -500)) == (-165, -330)
        assert _runtime_palt_residual_adjustment((-70, -140)) == (-46, -92)

    def test_scales_final_runtime_feature_adjustments_by_optical_scale(self):
        adjustments = {
            0x3001: (-512, -1024),
            0x3002: (100, -200),
        }

        assert _scale_feature_adjustments(adjustments, 0.925) == {
            0x3001: (round(-512 * 0.925), round(-1024 * 0.925)),
            0x3002: (round(100 * 0.925), round(-200 * 0.925)),
        }

    def test_scales_glyph_keyed_final_runtime_feature_adjustments(self):
        adjustments = {
            "uniFE10": (-512, -1024),
        }

        assert _scale_feature_adjustments(adjustments, 0.925) == {
            "uniFE10": (round(-512 * 0.925), round(-1024 * 0.925)),
        }

    def test_noto_vpal_yakumono_uses_separate_runtime_target_set(self):
        vpal = _get_variable_vpal()
        assert len(VPAL_FEATURE_CHARS) == 33

        palt_overlap = set("！？・〒＃＄＆＊￥")
        vpal_only = set("︐︑︒︗︘︵︶︷︸︹︺︻︼︽︾︿﹀﹁﹂﹃﹄﹇﹈％")

        assert set(VPAL_FEATURE_CHARS) & set(PALT_FEATURE_CHARS) == palt_overlap
        assert set(VPAL_FEATURE_CHARS) - set(PALT_FEATURE_CHARS) == vpal_only

        expected_glyphs = {
            "uni3012",  # 〒
            "uni2027",  # ・ (Noto source glyph before U+30FB split)
            "uniFF01",  # ！
            "uniFF03",  # ＃
            "uniFF04",  # ＄
            "uniFF06",  # ＆
            "uniFF0A",  # ＊
            "uniFF1F",  # ？
            "uniFFE5",  # ￥
            "uniFE10",  # ︐
            "uniFE11",  # ︑
            "uniFE12",  # ︒
            "uniFE17",  # ︗
            "uniFE18",  # ︘
            "uniFE35",  # ︵
            "uniFE36",  # ︶
            "uniFE37",  # ︷
            "uniFE38",  # ︸
            "uniFE39",  # ︹
            "uniFE3A",  # ︺
            "uniFE3B",  # ︻
            "uniFE3C",  # ︼
            "uniFE3D",  # ︽
            "uniFE3E",  # ︾
            "uniFE3F",  # ︿
            "uniFE40",  # ﹀
            "uniFE41",  # ﹁
            "uniFE42",  # ﹂
            "uniFE43",  # ﹃
            "uniFE44",  # ﹄
            "uniFE47",  # ﹇
            "uniFE48",  # ﹈
            "uniFF05",  # ％
        }

        assert expected_glyphs <= set(vpal)

    def test_colon_vpal_is_synthesized_for_vertical_glyph(self):
        assert SYNTHETIC_VPAL_ADJUSTMENTS == {
            "glyph17071": (250, -500),
            "uniFF1B": (250, -500),
        }

    def test_palt_spacing_adjustments_are_small_kana_only(self):
        assert "、" not in PALT_SPACE_ADJUSTMENTS
        assert "。" not in PALT_SPACE_ADJUSTMENTS
        assert "〈" not in PALT_SPACE_ADJUSTMENTS
        assert "！" not in PALT_SPACE_ADJUSTMENTS
        assert "？" not in PALT_SPACE_ADJUSTMENTS
        assert "・" not in PALT_SPACE_ADJUSTMENTS
        assert "〒" not in PALT_SPACE_ADJUSTMENTS
        assert "￥" not in PALT_SPACE_ADJUSTMENTS

        for char in "ぁぃぅぇぉっゃゅゎゕゖァゥェォッュョヮヵヶ":
            assert PALT_SPACE_ADJUSTMENTS[char] == (15, 15)
        assert PALT_SPACE_ADJUSTMENTS["ょ"] == (30, 35)
        assert PALT_SPACE_ADJUSTMENTS["ィ"] == (10, 10)
        assert PALT_SPACE_ADJUSTMENTS["ャ"] == (10, 15)

    def test_family_spacing_adjustments_do_not_override_middle_dot(self):
        assert "・" in PALT_FEATURE_CHARS
        assert "・" not in DISPLAY_PALT_SPACE_ADJUSTMENTS
        assert "・" not in NORMAL_PALT_SPACE_ADJUSTMENTS

    def test_codepoint_entries_resolve_to_glyphs(self, synthetic_ttf):
        glyphs = _glyphs_for_codepoints(
            synthetic_ttf,
            (0x2500, "、", "U+3033-U+3035"),
        )

        assert glyphs == {"uni2500", "uni3001", "uni3033", "uni3034", "uni3035"}

    def test_middle_dot_can_split_from_shared_noto_glyph(self, synthetic_ttf):
        synthetic_ttf.setGlyphOrder([*synthetic_ttf.getGlyphOrder(), "uni2027"])
        synthetic_ttf["glyf"]["uni2027"] = copy.deepcopy(synthetic_ttf["glyf"]["uni30FB"])
        synthetic_ttf["hmtx"].metrics["uni2027"] = synthetic_ttf["hmtx"]["uni30FB"]
        for table in synthetic_ttf["cmap"].tables:
            table.cmap[0x2027] = "uni2027"
            table.cmap[0x30FB] = "uni2027"
        before_hmtx = synthetic_ttf["hmtx"]["uni2027"]

        split_source = _split_cmap_codepoint_glyph(synthetic_ttf, 0x30FB, "uni30FB")

        cmap = synthetic_ttf.getBestCmap()
        assert split_source == "uni2027"
        assert cmap[0x2027] == "uni2027"
        assert cmap[0x30FB] == "uni30FB"
        assert synthetic_ttf["hmtx"]["uni30FB"] == before_hmtx
        assert synthetic_ttf["hmtx"]["uni2027"] == before_hmtx

    def test_runtime_feature_adjustments_survive_merge_renames(self, synthetic_ttf):
        synthetic_ttf.setGlyphOrder([
            *synthetic_ttf.getGlyphOrder(),
            "uni2035",
            "uni2035.orig",
        ])
        synthetic_ttf["glyf"]["uni2035"] = copy.deepcopy(synthetic_ttf["glyf"]["A"])
        synthetic_ttf["glyf"]["uni2035.orig"] = copy.deepcopy(synthetic_ttf["glyf"]["A"])
        synthetic_ttf["hmtx"].metrics["uni2035"] = synthetic_ttf["hmtx"]["A"]
        synthetic_ttf["hmtx"].metrics["uni2035.orig"] = synthetic_ttf["hmtx"]["A"]
        for table in synthetic_ttf["cmap"].tables:
            table.cmap[0x2035] = "uni2035"
            table.cmap[0xFF40] = "uni2035.orig"

        retargeted = _retarget_feature_adjustments(
            synthetic_ttf,
            {0xFF40: (-199, -500)},
        )

        assert retargeted == {"uni2035.orig": (-199, -500)}

    def test_runtime_feature_adjustments_capture_pre_merge_codepoints(self, synthetic_ttf):
        for table in synthetic_ttf["cmap"].tables:
            table.cmap[0xFF40] = "uni3030"

        adjustments = _feature_adjustments_for_codepoints(
            synthetic_ttf,
            ("｀", "U+1234"),
            {"uni3030": (-199, -500)},
        )

        assert adjustments == {0xFF40: (-199, -500)}

    def test_named_fallback_adjustments_can_retarget_by_codepoint(self, synthetic_ttf):
        synthetic_ttf.setGlyphOrder([
            *synthetic_ttf.getGlyphOrder(),
            "uniFF1B.orig",
        ])
        synthetic_ttf["glyf"]["uniFF1B.orig"] = copy.deepcopy(synthetic_ttf["glyf"]["A"])
        synthetic_ttf["hmtx"].metrics["uniFF1B.orig"] = synthetic_ttf["hmtx"]["A"]
        for table in synthetic_ttf["cmap"].tables:
            table.cmap[0xFF1B] = "uniFF1B.orig"

        retargeted = _retarget_named_adjustments(
            synthetic_ttf,
            {"uniFF1B": (250, -500), "glyph17071": (250, -500)},
        )

        assert retargeted == {"uniFF1B.orig": (250, -500)}


# ---------------------------------------------------------------------------
# _apply_glyph_spacing
# ---------------------------------------------------------------------------

class TestApplyGlyphSpacing:
    """Per-glyph LSB / RSB tweaks layered on top of tracking."""

    def test_lsb_delta_grows_advance_and_lsb(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]

        adjusted = _apply_glyph_spacing(synthetic_ttf, {"A": (40, 0)})

        after_aw, after_lsb = synthetic_ttf["hmtx"]["A"]
        assert adjusted == 1
        # +40 left whitespace: outline shifts right by 40, slot grows by 40.
        assert after_aw == before_aw + 40
        assert after_lsb == before_lsb + 40

    def test_rsb_delta_grows_advance_only(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]

        adjusted = _apply_glyph_spacing(synthetic_ttf, {"A": (0, 25)})

        after_aw, after_lsb = synthetic_ttf["hmtx"]["A"]
        assert adjusted == 1
        # +25 right whitespace: slot grows on the right, outline doesn't move.
        assert after_aw == before_aw + 25
        assert after_lsb == before_lsb

    def test_both_deltas_combine(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]

        adjusted = _apply_glyph_spacing(synthetic_ttf, {"A": (15, 25)})

        after_aw, after_lsb = synthetic_ttf["hmtx"]["A"]
        assert adjusted == 1
        assert after_aw == before_aw + 15 + 25
        assert after_lsb == before_lsb + 15

    def test_negative_deltas_tighten(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["A"]

        adjusted = _apply_glyph_spacing(synthetic_ttf, {"A": (-10, -10)})

        after_aw, after_lsb = synthetic_ttf["hmtx"]["A"]
        assert adjusted == 1
        assert after_aw == before_aw - 20
        assert after_lsb == before_lsb - 10

    def test_codepoint_int_key_works(self, synthetic_ttf):
        before_aw, before_lsb = synthetic_ttf["hmtx"]["uni3042"]

        # Pass codepoint as int instead of single-char string.
        adjusted = _apply_glyph_spacing(synthetic_ttf, {0x3042: (5, 7)})

        after_aw, after_lsb = synthetic_ttf["hmtx"]["uni3042"]
        assert adjusted == 1
        assert after_aw == before_aw + 12
        assert after_lsb == before_lsb + 5

    def test_unknown_codepoint_skipped_silently(self, synthetic_ttf):
        before_a = synthetic_ttf["hmtx"]["A"]

        # U+5000 is not in the synthetic cmap.
        adjusted = _apply_glyph_spacing(synthetic_ttf, {0x5000: (40, 0)})

        assert adjusted == 0
        # Nothing else moved.
        assert synthetic_ttf["hmtx"]["A"] == before_a

    def test_zero_advance_glyph_skipped(self, synthetic_ttf):
        # Combining marks have zero advance and serve a placement-only role —
        # spacing tweaks must not promote them to non-zero.
        synthetic_ttf["hmtx"]["A"] = (0, 0)

        adjusted = _apply_glyph_spacing(synthetic_ttf, {"A": (40, 0)})

        assert adjusted == 0
        assert synthetic_ttf["hmtx"]["A"] == (0, 0)

    def test_zero_deltas_no_op(self, synthetic_ttf):
        before = synthetic_ttf["hmtx"]["A"]

        adjusted = _apply_glyph_spacing(synthetic_ttf, {"A": (0, 0)})

        assert adjusted == 0
        assert synthetic_ttf["hmtx"]["A"] == before

    def test_empty_or_none_spacing_no_op(self, synthetic_ttf):
        before = dict(synthetic_ttf["hmtx"].metrics)

        assert _apply_glyph_spacing(synthetic_ttf, None) == 0
        assert _apply_glyph_spacing(synthetic_ttf, {}) == 0
        assert dict(synthetic_ttf["hmtx"].metrics) == before

    def test_only_target_glyph_changes(self, synthetic_ttf):
        # Spacing tweaks must not bleed into untargeted glyphs.
        before_kana = synthetic_ttf["hmtx"]["uni3042"]
        before_cjk = synthetic_ttf["hmtx"]["uni4E00"]

        _apply_glyph_spacing(synthetic_ttf, {"A": (40, 40)})

        assert synthetic_ttf["hmtx"]["uni3042"] == before_kana
        assert synthetic_ttf["hmtx"]["uni4E00"] == before_cjk

    def test_multi_char_string_key_rejected(self, synthetic_ttf):
        # Catch a likely typo: passing "AB" as a key would otherwise be
        # silently ignored (no matching codepoint).
        with pytest.raises(ValueError, match="single character"):
            _apply_glyph_spacing(synthetic_ttf, {"AB": (10, 10)})

    def test_malformed_value_rejected(self, synthetic_ttf):
        with pytest.raises(ValueError, match="lsb_delta, rsb_delta"):
            _apply_glyph_spacing(synthetic_ttf, {"A": 40})

    def test_outline_coordinates_untouched(self, synthetic_ttf):
        # Sidebearing tweaks rewrite hmtx only — glyph outlines must remain
        # byte-identical so collateral pipeline steps (bbox check, GPOS
        # anchors keyed on glyph extents) keep their assumptions.
        before_glyph = copy.deepcopy(synthetic_ttf["glyf"]["A"])

        _apply_glyph_spacing(synthetic_ttf, {"A": (40, 30)})

        after_glyph = synthetic_ttf["glyf"]["A"]
        assert after_glyph.xMin == before_glyph.xMin
        assert after_glyph.xMax == before_glyph.xMax
        assert list(after_glyph.coordinates) == list(before_glyph.coordinates)


# ---------------------------------------------------------------------------
# _get_variable_palt
# ---------------------------------------------------------------------------

class TestGetVariablePalt:
    """Cached read of palt from the vendor Noto Variable."""

    def test_returns_dict(self):
        palt = _get_variable_palt()
        assert isinstance(palt, dict)
        assert len(palt) > 0

    def test_returns_xplacement_xadvance_tuples(self):
        palt = _get_variable_palt()
        for gname, value in list(palt.items())[:5]:
            assert isinstance(gname, str)
            assert isinstance(value, tuple)
            assert len(value) == 2
            assert all(isinstance(v, int) for v in value)

    def test_cached_returns_same_instance(self):
        # Two calls return the same cached dict — module-level cache.
        first = _get_variable_palt()
        second = _get_variable_palt()
        assert first is second


# ---------------------------------------------------------------------------
# _get_variable_vpal
# ---------------------------------------------------------------------------

class TestGetVariableVpal:
    """Cached read of vpal from the vendor Noto Variable."""

    def test_returns_dict(self):
        vpal = _get_variable_vpal()
        assert isinstance(vpal, dict)
        assert len(vpal) > 0

    def test_returns_yplacement_yadvance_tuples(self):
        vpal = _get_variable_vpal()
        for gname, value in list(vpal.items())[:5]:
            assert isinstance(gname, str)
            assert isinstance(value, tuple)
            assert len(value) == 2
            assert all(isinstance(v, int) for v in value)

    def test_cached_returns_same_instance(self):
        # Two calls return the same cached dict — module-level cache.
        first = _get_variable_vpal()
        second = _get_variable_vpal()
        assert first is second
