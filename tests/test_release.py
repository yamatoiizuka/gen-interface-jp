"""Tests for release packaging — public distribution contracts.

The release pipeline emits artifacts that downstream consumers (jsDelivr,
the demo site, anyone hot-linking GitHub release assets) bind to by URL
or file path. The tests here lock those surfaces down so accidental
renames / restructures fail loudly during CI rather than silently after
publish.

Glue logic that's purely string formatting (e.g. version `v` prefix
stripping) is intentionally left untested — it's trivial enough that a
bug would show up the first time anyone tags a release, and the cost of
the test outweighs the protection.
"""

import json

from release.build import (
    copy_webfont_package,
    github_asset_urls,
    write_cdn_css_files,
    write_npm_package,
    write_npm_readme,
)


# ---------------------------------------------------------------------------
# GitHub Release asset URLs
# ---------------------------------------------------------------------------

def test_github_asset_urls_are_stable_for_site_downloads():
    """The site's download buttons and `.github/workflows/release.yml` both
    bind to these URL shapes by literal string. Any rename of the zip
    asset or change to GitHub's URL pattern would silently break those
    callers. Pinning the strings here surfaces the break in CI.

    Note: the asset filename embeds the version, and the URL is tag-
    pinned (no `releases/latest/download/...` form). Older site builds
    therefore keep linking at the exact archive they were built
    against, instead of resolving to whatever happens to be latest at
    download time.
    """
    urls = github_asset_urls("owner/repo", "v1.2.3", "1.2.3")
    assert (
        urls["bundle"]
        == "https://github.com/owner/repo/releases/download/v1.2.3/GenInterfaceJP-1.2.3.zip"
    )
    # No latestBundle: a versioned filename can't reliably resolve via
    # `latest/download/`, so the surface is intentionally tag-only.
    assert "latestBundle" not in urls


# ---------------------------------------------------------------------------
# npm package layout
# ---------------------------------------------------------------------------

def test_copy_webfont_package_keeps_css_entrypoints_at_package_root(tmp_path):
    """jsDelivr serves the npm package as static files. Consumers reference
    `.../gen-interface-jp/all.css` and per-weight CSS like `400.css` /
    `display-400.css` directly from the package root, with `cdn/*.css`
    variants for direct jsDelivr use. Moving them or renaming them is a
    breaking change in the public CDN URL space.

    This test stages a minimal source tree, runs the same copy + package
    write that `release.build` performs, and asserts the resulting npm
    directory still has CSS entrypoints at the root, the WOFF2 chunks
    under `w/{family}/{weight}/`, and a `package.json` whose `name` /
    `version` / `style` / `files` glob match what jsDelivr and `npm
    publish` rely on.
    """
    source = tmp_path / "source"
    (source / "w" / "normal" / "400").mkdir(parents=True)
    (source / "nam").mkdir()
    (source / "all.css").write_text(
        'src:url("./w/normal/400/000.woff2")',
        encoding="utf-8",
    )
    (source / "400.css").write_text(
        'src:url("./w/normal/400/000.woff2")',
        encoding="utf-8",
    )
    (source / "display-400.css").write_text(
        'src:url("./w/display/400/000.woff2")',
        encoding="utf-8",
    )
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "nam" / "000.nam").write_text("0x20\n", encoding="utf-8")
    (source / "w" / "normal" / "400" / "000.woff2").write_bytes(b"woff2")

    out_dir = tmp_path / "npm"
    copy_webfont_package(source, out_dir, include_nam=False)
    write_cdn_css_files(out_dir, "1.2.3")
    write_npm_readme(out_dir, "1.2.3")
    write_npm_package(out_dir, "1.2.3", "owner/repo")

    # Public CSS entrypoints stay at package root.
    assert (out_dir / "all.css").is_file()
    assert (out_dir / "400.css").is_file()
    assert (out_dir / "display-400.css").is_file()
    assert (out_dir / "cdn" / "all.css").is_file()
    assert (out_dir / "cdn" / "400.css").is_file()
    assert (out_dir / "cdn" / "display-400.css").is_file()

    # WOFF2 chunks live under w/{family}/{weight}/ — referenced by
    # `src: url("./w/normal/400/000.woff2")` from the CSS files.
    assert (out_dir / "w" / "normal" / "400" / "000.woff2").is_file()
    assert 'url("./w/normal/400/000.woff2")' in (out_dir / "400.css").read_text(
        encoding="utf-8"
    )
    assert (
        'url("https://cdn.jsdelivr.net/npm/gen-interface-jp@1.2.3/w/normal/400/000.woff2")'
        in (out_dir / "cdn" / "400.css").read_text(encoding="utf-8")
    )
    assert (
        'url("https://cdn.jsdelivr.net/npm/gen-interface-jp@1.2.3/w/display/400/000.woff2")'
        in (out_dir / "cdn" / "display-400.css").read_text(encoding="utf-8")
    )

    package = json.loads((out_dir / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "gen-interface-jp"
    assert package["version"] == "1.2.3"
    # `style` drives `<link>` resolution when consumers `import`
    # gen-interface-jp without specifying a path.
    assert package["style"] == "all.css"
    # `files` controls what `npm publish` ships. Missing any of these
    # globs would publish an empty / broken package even if the local
    # build directory looked right.
    assert "*.css" in package["files"]
    assert "cdn" in package["files"]
    assert "manifest.json" in package["files"]
    assert "README.md" in package["files"]
    assert "w" in package["files"]
    readme = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "cdn/400.css" in readme
    assert "400.css" in readme
    assert (
        "https://cdn.jsdelivr.net/npm/gen-interface-jp@1.2.3/w/normal/400/000.woff2"
        in readme
    )
    # OFL.txt rides alongside the package as part of the OFL §2
    # "include this license" requirement for redistribution.
    assert "OFL.txt" in package["files"]
    # `nam/` is build-tooling output (one human-readable codepoint
    # listing per slice) — kept locally for inspection but excluded
    # from the published npm tarball to keep downloads lean.
    assert "nam" not in package["files"]
    # OFL-1.1 license is part of the published metadata that npm and
    # license scanners surface to consumers.
    assert package["license"] == "OFL-1.1"
