"""Unit tests for the in-house proportional-baking logic in font/proportional.py.

Covers palt extraction, glyph translation, GPOS feature removal, and the
``make_proportional`` integration that ties them together.
"""

from font.proportional import (
    PROP_FEATURES,
    _read_palt,
    _read_vpal,
    _remove_prop_features,
    _shift_glyph_x,
    make_proportional,
)


# ---------------------------------------------------------------------------
# _read_palt
# ---------------------------------------------------------------------------

class TestReadPalt:
    """GPOS palt extraction."""

    def test_returns_dict_for_noto(self, noto_subset):
        palt = _read_palt(noto_subset)
        assert isinstance(palt, dict)
        # Noto's palt covers a few hundred glyphs in full; even after the
        # subset trims to ~28 glyphs, several palt entries should survive.
        assert len(palt) > 0

    def test_values_are_xplacement_xadvance_pairs(self, noto_subset):
        palt = _read_palt(noto_subset)
        for gname, value in palt.items():
            assert isinstance(value, tuple)
            assert len(value) == 2
            xp, xa = value
            assert isinstance(xp, int)
            assert isinstance(xa, int)

    def test_kana_letters_have_negative_x_advance(self, noto_subset):
        # palt narrows kana — XAdvance should be negative for kana letters
        # that have palt entries.
        palt = _read_palt(noto_subset)
        cmap = noto_subset.getBestCmap()
        kana_glyph = cmap.get(0x3042)  # あ
        if kana_glyph in palt:
            xp, xa = palt[kana_glyph]
            assert xa < 0, f"Expected negative XAdvance for あ, got {xa}"

    def test_empty_when_no_gsub_palt(self, synthetic_ttf):
        # synthetic_ttf has no GPOS at all
        assert _read_palt(synthetic_ttf) == {}


# ---------------------------------------------------------------------------
# _read_vpal
# ---------------------------------------------------------------------------

class TestReadVpal:
    """GPOS vpal extraction."""

    def test_returns_dict_for_noto(self, noto_subset):
        vpal = _read_vpal(noto_subset)
        assert isinstance(vpal, dict)
        assert len(vpal) > 0

    def test_values_are_yplacement_yadvance_pairs(self, noto_subset):
        vpal = _read_vpal(noto_subset)
        for gname, value in vpal.items():
            assert isinstance(gname, str)
            assert isinstance(value, tuple)
            assert len(value) == 2
            yp, ya = value
            assert isinstance(yp, int)
            assert isinstance(ya, int)

    def test_middle_dot_has_negative_y_advance(self, noto_subset):
        vpal = _read_vpal(noto_subset)
        cmap = noto_subset.getBestCmap()
        middle_dot_glyph = cmap.get(0x30FB)  # ・
        if middle_dot_glyph in vpal:
            yp, ya = vpal[middle_dot_glyph]
            assert ya < 0, f"Expected negative YAdvance for ・, got {ya}"

    def test_empty_when_no_gpos_vpal(self, synthetic_ttf):
        # synthetic_ttf has no GPOS at all
        assert _read_vpal(synthetic_ttf) == {}


# ---------------------------------------------------------------------------
# _shift_glyph_x
# ---------------------------------------------------------------------------

class TestShiftGlyphX:
    """In-place horizontal translation of a TrueType glyph."""

    def test_shifts_simple_glyph_coordinates(self, synthetic_ttf):
        glyph = synthetic_ttf["glyf"]["A"]
        before_xmin = glyph.xMin
        before_xmax = glyph.xMax
        before_coords = [(x, y) for x, y in glyph.coordinates]

        _shift_glyph_x(glyph, 50)

        assert glyph.xMin == before_xmin + 50
        assert glyph.xMax == before_xmax + 50
        for (bx, by), (ax, ay) in zip(before_coords, glyph.coordinates):
            assert ax == bx + 50
            assert ay == by  # y untouched

    def test_negative_shift(self, synthetic_ttf):
        glyph = synthetic_ttf["glyf"]["A"]
        before_xmin = glyph.xMin

        _shift_glyph_x(glyph, -25)

        assert glyph.xMin == before_xmin - 25

    def test_composite_shifts_component_anchor(self, synthetic_ttf):
        glyph = synthetic_ttf["glyf"]["compositeA"]
        before_x = glyph.components[0].x
        # Composite has bbox derived from referenced glyph; capture for
        # the bbox-update assertion below.
        before_xmin = glyph.xMin

        _shift_glyph_x(glyph, 30)

        assert glyph.components[0].x == before_x + 30
        assert glyph.xMin == before_xmin + 30


# ---------------------------------------------------------------------------
# _remove_prop_features
# ---------------------------------------------------------------------------

class TestRemovePropFeatures:
    """Strip palt/vpal/halt/vhal from GPOS while keeping other features."""

    def test_palt_is_removed(self, noto_subset):
        gpos = noto_subset["GPOS"]
        before = {fr.FeatureTag for fr in gpos.table.FeatureList.FeatureRecord}
        assert "palt" in before

        _remove_prop_features(noto_subset)

        after = {fr.FeatureTag for fr in gpos.table.FeatureList.FeatureRecord}
        assert "palt" not in after

    def test_all_prop_features_removed(self, noto_subset):
        _remove_prop_features(noto_subset)
        gpos = noto_subset["GPOS"]
        after = {fr.FeatureTag for fr in gpos.table.FeatureList.FeatureRecord}
        assert not (PROP_FEATURES & after), \
            f"Prop features still present: {PROP_FEATURES & after}"

    def test_keeps_kerning(self, noto_subset):
        gpos = noto_subset["GPOS"]
        had_kern = any(
            fr.FeatureTag == "kern"
            for fr in gpos.table.FeatureList.FeatureRecord
        )

        _remove_prop_features(noto_subset)

        if had_kern:
            after = {fr.FeatureTag for fr in gpos.table.FeatureList.FeatureRecord}
            assert "kern" in after

    def test_langsys_indices_remain_valid(self, noto_subset):
        # After removal, every FeatureIndex referenced from a LangSys
        # must still point to a real FeatureRecord.
        _remove_prop_features(noto_subset)
        gpos = noto_subset["GPOS"]
        n_features = len(gpos.table.FeatureList.FeatureRecord)
        if not gpos.table.ScriptList:
            return
        for sr in gpos.table.ScriptList.ScriptRecord:
            script = sr.Script
            for langsys in [script.DefaultLangSys] + [
                lsr.LangSys for lsr in (script.LangSysRecord or [])
            ]:
                if langsys is None:
                    continue
                for idx in langsys.FeatureIndex:
                    assert 0 <= idx < n_features, \
                        f"Stale FeatureIndex {idx} (n_features={n_features})"

    def test_unreferenced_lookups_are_pruned(self, noto_subset):
        _remove_prop_features(noto_subset)
        gpos = noto_subset["GPOS"]
        if not gpos.table.LookupList or not gpos.table.FeatureList:
            return

        n_lookups = gpos.table.LookupList.LookupCount
        used = set()
        for feature_record in gpos.table.FeatureList.FeatureRecord:
            used.update(feature_record.Feature.LookupListIndex or [])

        assert used == set(range(n_lookups))

    def test_no_op_when_no_prop_features(self, synthetic_ttf):
        # Synthetic font has no GPOS at all — should not raise.
        _remove_prop_features(synthetic_ttf)


# ---------------------------------------------------------------------------
# make_proportional
# ---------------------------------------------------------------------------

class TestMakeProportional:
    """End-to-end: bake palt → hmtx, optionally keep runtime palt."""

    def test_advance_narrows_for_palt_glyph(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        kana_glyph = cmap.get(0x3042)  # あ
        palt = _read_palt(noto_subset)
        if kana_glyph not in palt:
            import pytest
            pytest.skip("subset palt does not cover U+3042")

        before_aw = noto_subset["hmtx"][kana_glyph][0]
        xp, xa = palt[kana_glyph]

        make_proportional(noto_subset)

        after_aw = noto_subset["hmtx"][kana_glyph][0]
        assert after_aw == before_aw + xa

    def test_strips_prop_features_after_baking(self, noto_subset):
        make_proportional(noto_subset)
        gpos = noto_subset["GPOS"]
        if gpos and gpos.table and gpos.table.FeatureList:
            tags = {fr.FeatureTag for fr in gpos.table.FeatureList.FeatureRecord}
            assert not (PROP_FEATURES & tags)

    def test_runtime_palt_skips_baking_and_reinstalls_palt(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        punct_glyph = cmap.get(0x3001)  # 、
        palt = _read_palt(noto_subset)
        if punct_glyph not in palt:
            import pytest
            pytest.skip("subset palt does not cover U+3001")

        before_hmtx = noto_subset["hmtx"][punct_glyph]
        expected_palt = palt[punct_glyph]

        make_proportional(noto_subset, runtime_palt={punct_glyph})

        after_hmtx = noto_subset["hmtx"][punct_glyph]
        assert after_hmtx == before_hmtx
        assert _read_palt(noto_subset) == {punct_glyph: expected_palt}

        tags = {
            fr.FeatureTag
            for fr in noto_subset["GPOS"].table.FeatureList.FeatureRecord
        }
        assert "palt" in tags
        assert not ({"vpal", "halt", "vhal"} & tags)

    def test_runtime_palt_and_vpal_can_be_reinstalled_together(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        punct_glyph = cmap.get(0x30FB)  # ・
        palt = _read_palt(noto_subset)
        vpal = _read_vpal(noto_subset)
        if punct_glyph not in palt or punct_glyph not in vpal:
            import pytest
            pytest.skip("subset does not cover U+30FB palt/vpal")

        before_hmtx = noto_subset["hmtx"][punct_glyph]
        expected_palt = palt[punct_glyph]
        expected_vpal = vpal[punct_glyph]

        make_proportional(
            noto_subset,
            runtime_palt={punct_glyph},
            runtime_vpal={punct_glyph},
        )

        assert noto_subset["hmtx"][punct_glyph] == before_hmtx
        assert _read_palt(noto_subset) == {punct_glyph: expected_palt}
        assert _read_vpal(noto_subset) == {punct_glyph: expected_vpal}

        tags = {
            fr.FeatureTag
            for fr in noto_subset["GPOS"].table.FeatureList.FeatureRecord
        }
        assert {"palt", "vpal"} <= tags
        assert not ({"halt", "vhal"} & tags)

    def test_runtime_palt_does_not_keep_baked_glyphs_in_feature(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        runtime_glyph = cmap.get(0x3001)  # 、
        baked_glyph = cmap.get(0x3042)  # あ
        palt = _read_palt(noto_subset)
        if runtime_glyph not in palt or baked_glyph not in palt:
            import pytest
            pytest.skip("subset palt does not cover runtime and baked glyphs")

        before_baked_aw = noto_subset["hmtx"][baked_glyph][0]
        _, baked_xa = palt[baked_glyph]

        make_proportional(noto_subset, runtime_palt={runtime_glyph})

        assert noto_subset["hmtx"][baked_glyph][0] == before_baked_aw + baked_xa
        after_palt = _read_palt(noto_subset)
        assert runtime_glyph in after_palt
        assert baked_glyph not in after_palt

    def test_palt_override_takes_precedence(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        kana_glyph = cmap.get(0x3042)  # あ
        before_aw, before_lsb = noto_subset["hmtx"][kana_glyph]

        # Synthetic override: shrink advance by 100, no x_placement shift.
        make_proportional(
            noto_subset,
            palt_override={kana_glyph: (0, -100)},
        )

        after_aw = noto_subset["hmtx"][kana_glyph][0]
        assert after_aw == before_aw - 100

    def test_reduced_palt_applies_fraction(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        kana_glyph = cmap.get(0x3042)
        before_aw = noto_subset["hmtx"][kana_glyph][0]

        # Override with -90 advance, reduced scale 1/3 → expected -30
        make_proportional(
            noto_subset,
            palt_override={kana_glyph: (0, -90)},
            reduced_palt={kana_glyph},
            reduced_palt_scale=1 / 3,
        )

        after_aw = noto_subset["hmtx"][kana_glyph][0]
        assert after_aw == before_aw - 30

    def test_non_palt_glyph_keeps_metrics_without_squeeze(self, noto_subset):
        palt = _read_palt(noto_subset)
        cmap = noto_subset.getBestCmap()
        candidate = None
        for cp in (0x0041, 0x0061, 0x4E00):  # A, a, 一
            gname = cmap.get(cp)
            if gname and gname not in palt:
                candidate = gname
                break
        if candidate is None:
            import pytest
            pytest.skip("no non-palt glyph available")

        before = noto_subset["hmtx"][candidate]

        make_proportional(noto_subset)

        assert noto_subset["hmtx"][candidate] == before

    def test_squeeze_sb_narrows_non_palt_glyph(self, noto_subset):
        # Pick a glyph that has no palt entry; verify sidebearings shrink.
        palt = _read_palt(noto_subset)
        cmap = noto_subset.getBestCmap()
        candidate = None
        for cp in (0x0041, 0x0061):  # A, a
            gname = cmap.get(cp)
            if gname and gname not in palt:
                glyph = noto_subset["glyf"][gname]
                if glyph.numberOfContours > 0 and getattr(glyph, "xMin", None) is not None:
                    candidate = gname
                    break
        if candidate is None:
            import pytest
            pytest.skip("no non-palt glyph available")

        before_aw, before_lsb = noto_subset["hmtx"][candidate]
        glyph = noto_subset["glyf"][candidate]
        bbox_w = glyph.xMax - glyph.xMin
        before_rsb = before_aw - before_lsb - bbox_w

        # Half the sidebearings (squeeze_sb_scale = 0.5)
        make_proportional(
            noto_subset,
            squeeze_sb={candidate},
            squeeze_sb_scale=0.5,
        )

        after_aw, after_lsb = noto_subset["hmtx"][candidate]
        # LSB removal: round(lsb * 0.5); RSB removal: round(rsb * 0.5)
        expected_lsb_remove = round(before_lsb * 0.5)
        expected_rsb_remove = round(before_rsb * 0.5)
        assert after_lsb == before_lsb - expected_lsb_remove
        assert after_aw == before_aw - expected_lsb_remove - expected_rsb_remove

    def test_rejects_cff_font(self):
        # The pipeline only knows how to mutate glyf — CFF outlines should
        # fail loudly rather than silently produce an inconsistent font.
        from fontTools.ttLib import TTFont
        empty = TTFont()  # has no glyf
        import pytest
        with pytest.raises(ValueError, match="TrueType-outline"):
            make_proportional(empty)

    def test_no_op_for_glyph_not_in_hmtx(self, noto_subset):
        # palt entries pointing to glyphs that aren't in hmtx (e.g. dropped
        # by subsetting) should be silently ignored, not raise.
        before_count = len(noto_subset["hmtx"].metrics)
        make_proportional(
            noto_subset,
            palt_override={"nonexistent_glyph_name": (-50, -100)},
        )
        # No new hmtx entries should be invented.
        assert len(noto_subset["hmtx"].metrics) == before_count
