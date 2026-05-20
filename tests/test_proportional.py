"""Unit tests for the in-house proportional-baking logic in font/proportional.py.

Covers palt extraction, glyph translation, GPOS feature removal, and the
``make_proportional`` integration that ties them together.
"""

import io

import pytest

from fontTools.ttLib import newTable
from fontTools.ttLib.tables import otTables

from font.proportional import (
    PROP_FEATURES,
    _install_ss09_punctuation_feature,
    _read_palt,
    _read_vpal,
    _remove_prop_features,
    _shift_glyph_x,
    make_proportional,
)


def _feature_record(font, table_tag: str, feature_tag: str):
    table = font[table_tag].table
    for index, record in enumerate(table.FeatureList.FeatureRecord):
        if record.FeatureTag == feature_tag:
            return index, record
    raise AssertionError(f"{feature_tag} not found in {table_tag}")


def _install_synthetic_pairpos_kern(
    font,
    first_glyph: str,
    second_glyph: str,
    x_advance: int,
) -> None:
    gpos = newTable("GPOS")
    font["GPOS"] = gpos
    gpos.table = otTables.GPOS()
    gpos.table.Version = 0x00010000

    langsys = otTables.LangSys()
    langsys.LookupOrder = None
    langsys.ReqFeatureIndex = 0xFFFF
    langsys.FeatureIndex = [0]
    langsys.FeatureCount = 1

    script = otTables.Script()
    script.DefaultLangSys = langsys
    script.LangSysRecord = []
    script.LangSysCount = 0

    script_record = otTables.ScriptRecord()
    script_record.ScriptTag = "DFLT"
    script_record.Script = script

    gpos.table.ScriptList = otTables.ScriptList()
    gpos.table.ScriptList.ScriptRecord = [script_record]
    gpos.table.ScriptList.ScriptCount = 1

    value = otTables.ValueRecord()
    value.XAdvance = x_advance

    pair_record = otTables.PairValueRecord()
    pair_record.SecondGlyph = second_glyph
    pair_record.Value1 = value
    pair_record.Value2 = None

    pairset = otTables.PairSet()
    pairset.PairValueRecord = [pair_record]
    pairset.PairValueCount = 1

    coverage = otTables.Coverage()
    coverage.glyphs = [first_glyph]

    subtable = otTables.PairPos()
    subtable.Format = 1
    subtable.Coverage = coverage
    subtable.ValueFormat1 = 0x0004
    subtable.ValueFormat2 = 0
    subtable.PairSet = [pairset]
    subtable.PairSetCount = 1

    lookup = otTables.Lookup()
    lookup.LookupType = 2
    lookup.LookupFlag = 0
    lookup.SubTable = [subtable]
    lookup.SubTableCount = 1

    gpos.table.LookupList = otTables.LookupList()
    gpos.table.LookupList.Lookup = [lookup]
    gpos.table.LookupList.LookupCount = 1

    feature = otTables.Feature()
    feature.FeatureParams = None
    feature.LookupListIndex = [0]
    feature.LookupCount = 1

    feature_record = otTables.FeatureRecord()
    feature_record.FeatureTag = "kern"
    feature_record.Feature = feature

    gpos.table.FeatureList = otTables.FeatureList()
    gpos.table.FeatureList.FeatureRecord = [feature_record]
    gpos.table.FeatureList.FeatureCount = 1


def _install_synthetic_gsub_single_subst_feature(
    font,
    feature_tag: str,
    mapping: dict[str, str],
) -> None:
    gsub = newTable("GSUB")
    font["GSUB"] = gsub
    gsub.table = otTables.GSUB()
    gsub.table.Version = 0x00010000

    langsys = otTables.LangSys()
    langsys.LookupOrder = None
    langsys.ReqFeatureIndex = 0
    langsys.FeatureIndex = [0]
    langsys.FeatureCount = 1

    script = otTables.Script()
    script.DefaultLangSys = langsys
    script.LangSysRecord = []
    script.LangSysCount = 0

    script_record = otTables.ScriptRecord()
    script_record.ScriptTag = "DFLT"
    script_record.Script = script

    gsub.table.ScriptList = otTables.ScriptList()
    gsub.table.ScriptList.ScriptRecord = [script_record]
    gsub.table.ScriptList.ScriptCount = 1

    subtable = otTables.SingleSubst()
    subtable.mapping = mapping

    lookup = otTables.Lookup()
    lookup.LookupType = 1
    lookup.LookupFlag = 0
    lookup.SubTable = [subtable]
    lookup.SubTableCount = 1

    gsub.table.LookupList = otTables.LookupList()
    gsub.table.LookupList.Lookup = [lookup]
    gsub.table.LookupList.LookupCount = 1

    feature = otTables.Feature()
    feature.FeatureParams = None
    feature.LookupListIndex = [0]
    feature.LookupCount = 1

    feature_record = otTables.FeatureRecord()
    feature_record.FeatureTag = feature_tag
    feature_record.Feature = feature

    gsub.table.FeatureList = otTables.FeatureList()
    gsub.table.FeatureList.FeatureRecord = [feature_record]
    gsub.table.FeatureList.FeatureCount = 1


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
# ss09 punctuation feature
# ---------------------------------------------------------------------------

class TestInstallSS09Punctuation:
    """GSUB ss09 construction for optional yakumono spacing."""

    def test_installs_ss09_alternates_and_feature_name(self, synthetic_ttf):
        _install_synthetic_pairpos_kern(
            synthetic_ttf,
            "uni3042",
            "uni3001",
            -80,
        )
        before_order = list(synthetic_ttf.getGlyphOrder())

        _install_ss09_punctuation_feature(
            synthetic_ttf,
            {"uni3001": (-25, -100)},
        )

        assert synthetic_ttf.getGlyphOrder() == before_order + ["uni3001.ss09"]
        feature_index, record = _feature_record(synthetic_ttf, "GSUB", "ss09")
        assert feature_index >= 0
        ui_name_id = record.Feature.FeatureParams.UINameID
        names = {
            name.toUnicode()
            for name in synthetic_ttf["name"].names
            if name.nameID == ui_name_id
        }
        assert "約物半角" in names
        assert _feature_record(synthetic_ttf, "GPOS", "kern")

    def test_installs_ss09_alternates_with_vmtx_metrics(self, synthetic_ttf):
        vmtx = newTable("vmtx")
        vmtx.metrics = {
            glyph_name: (1000, 0)
            for glyph_name in synthetic_ttf.getGlyphOrder()
        }
        synthetic_ttf["vmtx"] = vmtx

        _install_ss09_punctuation_feature(
            synthetic_ttf,
            {"uni3001": (-25, -100)},
        )

        assert synthetic_ttf["vmtx"].metrics["uni3001.ss09"] == (
            1000,
            0,
        )
        assert len(synthetic_ttf["vmtx"].metrics) == len(
            synthetic_ttf.getGlyphOrder()
        )

    def test_harfbuzz_uses_ss09_only_when_feature_is_enabled(self, synthetic_ttf):
        hb = pytest.importorskip("uharfbuzz")
        _install_synthetic_pairpos_kern(
            synthetic_ttf,
            "uni3042",
            "uni3001",
            -80,
        )
        _install_ss09_punctuation_feature(
            synthetic_ttf,
            {"uni3001": (-25, -100)},
        )
        buffer = io.BytesIO()
        synthetic_ttf.save(buffer)
        font_data = buffer.getvalue()
        glyph_order = synthetic_ttf.getGlyphOrder()

        def shape(features):
            face = hb.Face(font_data)
            hb_font = hb.Font(face)
            hb_buffer = hb.Buffer()
            hb_buffer.add_str("、")
            hb_buffer.guess_segment_properties()
            hb.shape(hb_font, hb_buffer, features)
            return [
                (glyph_order[info.codepoint], pos.x_advance, pos.x_offset)
                for info, pos in zip(hb_buffer.glyph_infos, hb_buffer.glyph_positions)
            ]

        assert shape({}) == [("uni3001", 1000, 0)]
        assert shape({"kern": 1}) == [("uni3001", 1000, 0)]
        assert shape({"ss09": 1}) == [("uni3001.ss09", 900, 0)]
        assert shape({"kern": 1, "ss09": 1}) == [("uni3001.ss09", 900, 0)]

    def test_harfbuzz_applies_ss09_and_kern_together(self, synthetic_ttf):
        hb = pytest.importorskip("uharfbuzz")
        _install_synthetic_pairpos_kern(
            synthetic_ttf,
            "uni3042",
            "uni3001",
            -80,
        )
        _install_ss09_punctuation_feature(
            synthetic_ttf,
            {"uni3001": (0, -100)},
        )
        buffer = io.BytesIO()
        synthetic_ttf.save(buffer)
        font_data = buffer.getvalue()
        glyph_order = synthetic_ttf.getGlyphOrder()

        def shape(features):
            face = hb.Face(font_data)
            hb_font = hb.Font(face)
            hb_buffer = hb.Buffer()
            hb_buffer.add_str("あ、")
            hb_buffer.guess_segment_properties()
            hb.shape(hb_font, hb_buffer, features)
            return [
                (glyph_order[info.codepoint], pos.x_advance)
                for info, pos in zip(hb_buffer.glyph_infos, hb_buffer.glyph_positions)
            ]

        assert shape({"kern": 1}) == [("uni3042", 920), ("uni3001", 1000)]
        assert shape({"ss09": 1, "kern": 0}) == [
            ("uni3042", 1000),
            ("uni3001.ss09", 900),
        ]
        assert shape({"kern": 1, "ss09": 1}) == [
            ("uni3042", 920),
            ("uni3001.ss09", 900),
        ]

    def test_sorts_gsub_features_and_remaps_langsys(self, synthetic_ttf):
        hb = pytest.importorskip("uharfbuzz")
        _install_synthetic_gsub_single_subst_feature(
            synthetic_ttf,
            "zero",
            {"A": "A"},
        )

        _install_ss09_punctuation_feature(
            synthetic_ttf,
            {"uni3001": (0, -100)},
        )

        gsub = synthetic_ttf["GSUB"].table
        tags = [
            record.FeatureTag
            for record in gsub.FeatureList.FeatureRecord
        ]
        assert tags == sorted(tags)
        assert tags == ["ss09", "zero"]

        for script_record in gsub.ScriptList.ScriptRecord:
            script = script_record.Script
            langsystems = []
            if script.DefaultLangSys:
                langsystems.append(script.DefaultLangSys)
            langsystems.extend(
                langsys_record.LangSys
                for langsys_record in script.LangSysRecord or []
            )

            for langsys in langsystems:
                referenced_tags = {
                    gsub.FeatureList.FeatureRecord[index].FeatureTag
                    for index in langsys.FeatureIndex
                }
                assert referenced_tags == {"ss09", "zero"}
                assert gsub.FeatureList.FeatureRecord[
                    langsys.ReqFeatureIndex
                ].FeatureTag == "zero"

        buffer = io.BytesIO()
        synthetic_ttf.save(buffer)
        font_data = buffer.getvalue()
        glyph_order = synthetic_ttf.getGlyphOrder()
        face = hb.Face(font_data)
        hb_font = hb.Font(face)
        hb_buffer = hb.Buffer()
        hb_buffer.add_str("、")
        hb_buffer.guess_segment_properties()
        hb.shape(hb_font, hb_buffer, {"ss09": 1})

        assert [
            (glyph_order[info.codepoint], pos.x_advance)
            for info, pos in zip(hb_buffer.glyph_infos, hb_buffer.glyph_positions)
        ] == [("uni3001.ss09", 900)]


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

    def test_runtime_palt_can_bake_base_fraction_and_reinstall_residual(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        punct_glyph = cmap.get(0x3001)  # 、
        palt = _read_palt(noto_subset)
        if punct_glyph not in palt:
            import pytest
            pytest.skip("subset palt does not cover U+3001")

        before_aw, before_lsb = noto_subset["hmtx"][punct_glyph]
        x_placement, x_advance = palt[punct_glyph]
        base_x_placement = round(x_placement * 0.34)
        base_x_advance = round(x_advance * 0.34)

        make_proportional(
            noto_subset,
            runtime_palt={punct_glyph},
            runtime_palt_base_scale=0.34,
        )

        assert noto_subset["hmtx"][punct_glyph] == (
            before_aw + base_x_advance,
            before_lsb + base_x_placement,
        )
        assert _read_palt(noto_subset) == {
            punct_glyph: (
                x_placement - base_x_placement,
                x_advance - base_x_advance,
            )
        }

    def test_runtime_palt_base_fraction_can_skip_live_palt_reinstall(self, noto_subset):
        cmap = noto_subset.getBestCmap()
        punct_glyph = cmap.get(0x3001)  # 、
        palt = _read_palt(noto_subset)
        if punct_glyph not in palt:
            pytest.skip("subset palt does not cover U+3001")

        before_aw, before_lsb = noto_subset["hmtx"][punct_glyph]
        x_placement, x_advance = palt[punct_glyph]
        base_x_placement = round(x_placement * 0.34)
        base_x_advance = round(x_advance * 0.34)

        make_proportional(
            noto_subset,
            runtime_palt={punct_glyph},
            runtime_palt_base_scale=0.34,
            install_runtime_palt=False,
        )

        assert noto_subset["hmtx"][punct_glyph] == (
            before_aw + base_x_advance,
            before_lsb + base_x_placement,
        )
        assert _read_palt(noto_subset) == {}

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
