"""Compare every SFNT table, ignoring only head timestamps and checksum adjustment.

Usage: python scripts/compare_fonts.py BEFORE AFTER [--output report.json]
Accepts TTF, OTF and WOFF2 trees. Exits nonzero on missing or changed fonts.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.misc.xmlWriter import XMLWriter


def tables(path):
    with TTFont(path, recalcTimestamp=False) as font:
        result = {}
        for tag in font.reader.keys():
            data = font.reader[tag]
            if tag == "head":
                # checkSumAdjustment, created, modified; retain all other fields.
                data = data[:8] + bytes(4) + data[12:20] + bytes(16) + data[36:]
            result[tag] = hashlib.sha256(data).hexdigest()
        return result


def semantic_table(font, tag):
    if tag in {"GPOS", "GSUB"}:
        extension_type = 9 if tag == "GPOS" else 7
        for lookup in font[tag].table.LookupList.Lookup:
            if lookup.LookupType == extension_type:
                types = {sub.ExtensionLookupType for sub in lookup.SubTable}
                if len(types) == 1:
                    lookup.LookupType = types.pop()
                    lookup.SubTable = [sub.ExtSubTable for sub in lookup.SubTable]
    output = io.BytesIO()
    font[tag].toXML(XMLWriter(output), font)
    return output.getvalue()


def inventory(root):
    return {p.relative_to(root): p for p in root.rglob("*")
            if p.suffix.lower() in {".ttf", ".otf", ".woff2"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    before, after = inventory(args.before), inventory(args.after)
    report = {"before": str(args.before), "after": str(args.after),
              "ignored": ["head.checkSumAdjustment", "head.created", "head.modified"],
              "fonts": len(before), "identical_bytes": 0, "identical_tables": 0,
              "missing": sorted(str(p) for p in before.keys() - after.keys()),
              "added": sorted(str(p) for p in after.keys() - before.keys()),
              "encoding_only": {}, "changed": {}}
    for name in sorted(before.keys() & after.keys()):
        a, b = before[name], after[name]
        report["identical_bytes"] += a.read_bytes() == b.read_bytes()
        ta, tb = tables(a), tables(b)
        changed = sorted(t for t in ta.keys() | tb.keys() if ta.get(t) != tb.get(t))
        if changed:
            with TTFont(a, recalcTimestamp=False) as fa, TTFont(b, recalcTimestamp=False) as fb:
                encoding = [t for t in changed if t in fa and t in fb and t != "head"
                            and semantic_table(fa, t) == semantic_table(fb, t)]
            if encoding:
                report["encoding_only"][str(name)] = encoding
            semantic = [t for t in changed if t not in encoding]
            if semantic:
                report["changed"][str(name)] = semantic
        else:
            report["identical_tables"] += 1
    report["semantically_identical"] = len(before.keys() & after.keys()) - len(report["changed"])
    body = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(body)
    print(body, end="")
    return int(not before or bool(report["missing"] or report["added"] or report["changed"]))


if __name__ == "__main__":
    raise SystemExit(main())
