"""Verify Thin/ExtraBold InterVariable edge instances after a font build.

Run after building the edge weights:

    PYTHONPATH=src python3 -m font.build all Thin ExtraBold
    PYTHONPATH=src python3 -m font.verify_edge_instances
"""

from __future__ import annotations

from pathlib import Path
import sys

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from .build import (
    DIST_TTF,
    FAMILIES,
    INTER_VARIABLE,
    INTER_VARIABLE_EDGE_WEIGHTS,
    STATIC_INSTANCE_VARIATION_TABLES,
    _default_inter_static_path,
    _inter_variable_instance_path,
)


SAMPLE_GLYPHS = ("H", "A", "a", "period")


def _layout_feature_tags(font: TTFont, table_tag: str) -> set[str]:
    if table_tag not in font:
        return set()
    feature_list = font[table_tag].table.FeatureList
    if feature_list is None:
        return set()
    return {record.FeatureTag for record in feature_list.FeatureRecord}


def _glyph_signature(font: TTFont, glyph_name: str) -> tuple[tuple[int, int], int, int, int, int]:
    glyph = font["glyf"][glyph_name]
    return (
        font["hmtx"][glyph_name],
        glyph.xMin,
        glyph.xMax,
        glyph.yMin,
        glyph.yMax,
    )


def _final_ttf_path(family: dict, weight_name: str) -> Path:
    return Path(DIST_TTF) / family["familyName"] / f"{family['folderPrefix']}-{weight_name}.ttf"


def _check(condition: bool, failures: list[str], message: str) -> None:
    if not condition:
        failures.append(message)


def _verify_source_instance(
    family_key: str,
    weight_num: int,
    weight_name: str,
) -> list[str]:
    failures: list[str] = []
    family = FAMILIES[family_key]
    config = INTER_VARIABLE_EDGE_WEIGHTS[weight_name]
    axes = {"wght": config["wght"], "opsz": family["interOpsz"]}
    instance_path = Path(_inter_variable_instance_path(family, weight_name, axes))
    _check(instance_path.is_file(), failures, f"missing generated Inter instance: {instance_path}")
    if failures:
        return failures

    generated = TTFont(str(instance_path))
    variable = TTFont(INTER_VARIABLE)
    vendor_static = TTFont(_default_inter_static_path(family, weight_name))
    variable_for_expected = TTFont(INTER_VARIABLE)
    try:
        expected = instancer.instantiateVariableFont(variable_for_expected, axes, inplace=False)
    finally:
        variable_for_expected.close()

    try:
        prefix = f"{family_key} {weight_name}"
        _check(
            generated["OS/2"].usWeightClass == weight_num,
            failures,
            f"{prefix}: generated usWeightClass is {generated['OS/2'].usWeightClass}, expected {weight_num}",
        )
        for table_tag in STATIC_INSTANCE_VARIATION_TABLES:
            _check(
                table_tag not in generated,
                failures,
                f"{prefix}: generated instance still contains {table_tag}",
            )

        generated_cmap = set(generated.getBestCmap() or {})
        variable_cmap = set(variable.getBestCmap() or {})
        static_cmap = set(vendor_static.getBestCmap() or {})
        _check(generated_cmap == variable_cmap, failures, f"{prefix}: cmap differs from InterVariable")
        _check(generated_cmap == static_cmap, failures, f"{prefix}: cmap differs from vendor static Inter")
        _check(
            _layout_feature_tags(generated, "GSUB") == _layout_feature_tags(variable, "GSUB"),
            failures,
            f"{prefix}: GSUB feature set differs from InterVariable",
        )
        _check(
            _layout_feature_tags(generated, "GPOS") == _layout_feature_tags(variable, "GPOS"),
            failures,
            f"{prefix}: GPOS feature set differs from InterVariable",
        )
        _check(
            _layout_feature_tags(generated, "GSUB") == _layout_feature_tags(vendor_static, "GSUB"),
            failures,
            f"{prefix}: GSUB feature set differs from vendor static Inter",
        )
        _check(
            _layout_feature_tags(generated, "GPOS") == _layout_feature_tags(vendor_static, "GPOS"),
            failures,
            f"{prefix}: GPOS feature set differs from vendor static Inter",
        )
        for glyph_name in SAMPLE_GLYPHS:
            _check(
                _glyph_signature(generated, glyph_name) == _glyph_signature(expected, glyph_name),
                failures,
                f"{prefix}: glyph {glyph_name} does not match requested axes {axes}",
            )
    finally:
        generated.close()
        variable.close()
        vendor_static.close()
        expected.close()

    return failures


def _verify_final_ttf(
    family_key: str,
    weight_num: int,
    weight_name: str,
) -> list[str]:
    failures: list[str] = []
    family = FAMILIES[family_key]
    config = INTER_VARIABLE_EDGE_WEIGHTS[weight_name]
    axes = {"wght": config["wght"], "opsz": family["interOpsz"]}
    instance_path = Path(_inter_variable_instance_path(family, weight_name, axes))
    final_path = _final_ttf_path(family, weight_name)
    _check(final_path.is_file(), failures, f"missing final TTF: {final_path}")
    _check(instance_path.is_file(), failures, f"missing generated Inter instance: {instance_path}")
    if failures:
        return failures

    generated = TTFont(str(instance_path))
    final_font = TTFont(str(final_path))
    try:
        prefix = f"{family_key} {weight_name}"
        _check(
            final_font["OS/2"].usWeightClass == weight_num,
            failures,
            f"{prefix}: final usWeightClass is {final_font['OS/2'].usWeightClass}, expected {weight_num}",
        )
        name_values = [record.toUnicode() for record in final_font["name"].names]
        leak_markers = ("wght125", "wght775", "InterVariable")
        for marker in leak_markers:
            _check(
                not any(marker in value for value in name_values),
                failures,
                f"{prefix}: final name table leaks {marker}",
            )

        names_by_id: dict[int, set[str]] = {}
        for record in final_font["name"].names:
            names_by_id.setdefault(record.nameID, set()).add(record.toUnicode())
        _check(
            family["familyName"] in names_by_id.get(1, set()),
            failures,
            f"{prefix}: final legacy family name is not {family['familyName']}",
        )
        _check(
            family["familyName"] in names_by_id.get(16, set()),
            failures,
            f"{prefix}: final typographic family name is not {family['familyName']}",
        )
        _check(
            weight_name in names_by_id.get(2, set()),
            failures,
            f"{prefix}: final subfamily name is not {weight_name}",
        )
        _check(
            weight_name in names_by_id.get(17, set()),
            failures,
            f"{prefix}: final typographic subfamily name is not {weight_name}",
        )

        generated_cmap = set(generated.getBestCmap() or {})
        final_cmap = set(final_font.getBestCmap() or {})
        _check(generated_cmap.issubset(final_cmap), failures, f"{prefix}: final cmap lost Inter codepoints")
        _check(
            _layout_feature_tags(generated, "GSUB").issubset(_layout_feature_tags(final_font, "GSUB")),
            failures,
            f"{prefix}: final GSUB lost Inter features",
        )
        _check(
            _layout_feature_tags(generated, "GPOS").issubset(_layout_feature_tags(final_font, "GPOS")),
            failures,
            f"{prefix}: final GPOS lost Inter features",
        )
    finally:
        generated.close()
        final_font.close()

    return failures


def verify() -> list[str]:
    failures: list[str] = []
    for family_key in ("normal", "display"):
        for weight_num, weight_name in ((100, "Thin"), (800, "ExtraBold")):
            failures.extend(_verify_source_instance(family_key, weight_num, weight_name))
            failures.extend(_verify_final_ttf(family_key, weight_num, weight_name))
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("Edge instance verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "Run `PYTHONPATH=src python3 -m font.build all Thin ExtraBold` first.",
            file=sys.stderr,
        )
        return 1

    print("Edge instance verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
