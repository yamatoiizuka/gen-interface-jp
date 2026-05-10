"""Unit tests for the in-house helpers in font/build.py.

The functions under test are everything that doesn't go through font-baker:
glyph-name codepoint parsing, kana / CJK classification, GSUB feature
inspection, horizontal scaling, bbox stripping, and tracking.
"""

import copy

import pytest

from font.build import (
    _apply_glyph_spacing,
    _apply_tracking,
    _apply_x_scale,
    _EXTREME_YMAX,
    _EXTREME_YMIN,
    _VERTICAL_REPEAT_MARK_CODEPOINTS,
    _get_cjk_glyphs,
    _get_variable_palt,
    _get_vert_alternates,
    _glyph_codepoint,
    _is_cjk_codepoint,
    _is_kana_letter,
    _is_kana_or_punct,
    _strip_extreme_glyphs,
    SUB_EXCLUDE_CODEPOINTS,
    TRACKING_IGNORE_CODEPOINTS,
)
from merge_fonts import parse_codepoint_list


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
        # If the constants drift, several call sites assume yMax=1200/yMin=-400.
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
        assert 0x30FB in codepoints  # ・
        assert 0x3030 in codepoints  # 〰
        assert 0xFE30 in codepoints  # ︰
        assert 0xFE4F in codepoints  # ﹏
        assert 0xFF65 in codepoints  # ･
        assert 0x2E3A in codepoints  # ⸺
        assert 0x2E3B in codepoints  # ⸻
        assert 0xFF3F in codepoints  # ＿
        assert 0xFFE3 in codepoints  # ￣

        # From the earlier "confirm visually" group, only U+3030 is opted in.
        assert 0x301C not in codepoints  # 〜
        assert 0x30FC not in codepoints  # ー
        assert 0xFF0D not in codepoints  # －
        assert 0xFF1D not in codepoints  # ＝


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
