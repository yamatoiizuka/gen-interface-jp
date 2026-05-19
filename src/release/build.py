#!/usr/bin/env python3
"""Prepare release artifacts for Gen Interface JP.

Outputs:

- dist/release/github/GenInterfaceJP-<version>.zip
                                              (TTF, both families × all weights)
- dist/release/npm/                        (subset webfont package for npm)
- dist/release/webfonts/gen-interface-jp/  (Pages-hosted mirror of the package)

The GitHub zip is the downloadable asset for users who want the TTFs to
install or to feed into other tools (Illustrator / Figma desktop /
bitmap converters). The npm directory is laid out so jsDelivr can serve
/all.css and weight-specific CSS entrypoints directly from the package
root, plus cdn/*.css variants whose WOFF2 URLs are absolute jsDelivr
URLs for direct CDN linking. Full single-file WOFF2 is intentionally
not redistributed — web delivery flows through the subset package, and
self-hosters can convert TTF→WOFF2 trivially with fontTools or
pyftsubset.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from font.build import DIST_TTF, FAMILIES, ROOT, WEIGHTS
from project_metadata import project_version as read_project_version

ROOT_PATH = Path(ROOT)
DIST_TTF_PATH = Path(DIST_TTF)

DEFAULT_WEBFONT_SOURCE = ROOT_PATH / "dist" / "webfont" / "gen-interface-jp"
DEFAULT_RELEASE_DIR = ROOT_PATH / "dist" / "release"
DEFAULT_REPOSITORY = "yamatoiizuka/gen-interface-jp"
NPM_PACKAGE_NAME = "gen-interface-jp"
INTER_OFL = ROOT_PATH / "vendor" / "fonts" / "Inter-4.1" / "LICENSE.txt"


@dataclass(frozen=True)
class ReleaseFile:
    path: Path
    archive_name: str


def project_version() -> str:
    return read_project_version(ROOT_PATH)


def normalized_version(version: str | None) -> str:
    raw = version or os.environ.get("GITHUB_REF_NAME") or project_version()
    return raw[1:] if raw.startswith("v") else raw


def release_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def family_files(version: str) -> list[ReleaseFile]:
    """Collect every TTF expected at dist/ttf/ for the GitHub Release zip.

    Archive paths are nested under a version-stamped root folder
    (``GenInterfaceJP-<version>/``) so that unzipping cleanly drops the
    files into a single labelled directory rather than spilling
    family folders into whatever the user's working directory is.
    """
    root = f"GenInterfaceJP-{version}"
    files: list[ReleaseFile] = []
    for family in FAMILIES.values():
        family_name = family["familyName"]
        folder_prefix = family["folderPrefix"]
        for _, weight_name, _ in WEIGHTS:
            filename = f"{folder_prefix}-{weight_name}.ttf"
            path = DIST_TTF_PATH / family_name / filename
            files.append(ReleaseFile(path=path, archive_name=f"{root}/{family_name}/{filename}"))
    return files


def require_files(files: list[ReleaseFile]) -> None:
    missing = [str(file.path) for file in files if not file.path.is_file()]
    if missing:
        lines = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Missing release input files:\n{lines}\nRun: make font")


def write_zip(
    path: Path,
    files: list[ReleaseFile],
    inline: dict[str, str] | None = None,
) -> None:
    """Write a zip from on-disk files, optionally adding inline text entries.

    ``inline`` maps archive path → string content. Used to place the OFL
    license text directly into the archive without staging it on disk
    first (it is a generated string composed at release time).
    """
    require_files(files)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for file in files:
            archive.write(file.path, file.archive_name)
        for arc_name, content in (inline or {}).items():
            archive.writestr(arc_name, content)
    print(f"Wrote {path}")


def require_webfont_package(source: Path) -> None:
    required = [
        source / "all.css",
        source / "manifest.json",
        source / "nam",
        source / "w",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        lines = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Missing webfont build outputs:\n{lines}\nRun: make webfont")


def copy_webfont_package(source: Path, out_dir: Path, *, include_nam: bool = True) -> None:
    require_webfont_package(source)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(source, out_dir)
    if not include_nam:
        shutil.rmtree(out_dir / "nam", ignore_errors=True)
    print(f"Wrote {out_dir}")


def ofl_text() -> str:
    inter_license = INTER_OFL.read_text(encoding="utf-8")
    _, ofl_body = inter_license.split("\n\n", 1)
    copyright_lines = [
        "Copyright 2026 The Gen Interface JP Project Authors (https://github.com/yamatoiizuka/gen-interface-jp)",
        "Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)",
        "Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'",
    ]
    return "\n".join(copyright_lines) + "\n\n" + ofl_body


def write_npm_license_files(out_dir: Path) -> None:
    (out_dir / "OFL.txt").write_text(ofl_text(), encoding="utf-8")


def cdn_base_url(version: str) -> str:
    return f"https://cdn.jsdelivr.net/npm/{NPM_PACKAGE_NAME}@{version}/"


def css_with_cdn_urls(css: str, version: str) -> str:
    return css.replace('url("./w/', f'url("{cdn_base_url(version)}w/')


def write_cdn_css_files(out_dir: Path, version: str) -> list[Path]:
    """Write CDN-specific CSS entrypoints next to the self-host CSS files.

    The source CSS keeps relative `./w/...` URLs so npm installers and
    self-hosters can copy the package anywhere. The CDN variants point
    at the exact version on jsDelivr, matching the Google Fonts hosted
    CSS shape and avoiding bad relative-URL resolution by crawlers.
    """
    written: list[Path] = []
    cdn_dir = out_dir / "cdn"
    if cdn_dir.exists():
        shutil.rmtree(cdn_dir)
    cdn_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(out_dir.glob("*.css")):
        target = cdn_dir / source.name
        target.write_text(
            css_with_cdn_urls(source.read_text(encoding="utf-8"), version),
            encoding="utf-8",
        )
        written.append(target)
    return written


def write_npm_readme(out_dir: Path, version: str) -> None:
    base_url = cdn_base_url(version)
    content = f"""# Gen Interface JP

Gen Interface JP is a Japanese/Latin UI typeface built from Inter and
Noto Sans JP. This package contains unicode-range WOFF2 subsets for web
use.

## Which CSS Should I Use?

Use the `cdn/*.css` files when linking directly from jsDelivr:

```html
<link rel="stylesheet" href="{base_url}cdn/400.css">
<link rel="stylesheet" href="{base_url}cdn/display-800.css">
```

The CDN CSS files contain absolute WOFF2 URLs such as:

```css
src: url("{base_url}w/normal/400/000.woff2") format("woff2");
```

Use the plain `.css` files when installing from npm or self-hosting:

```js
import "gen-interface-jp/400.css";
import "gen-interface-jp/display-800.css";
```

Plain CSS files keep relative WOFF2 URLs:

```css
src: url("./w/normal/400/000.woff2") format("woff2");
```

That relative form is correct for browsers and lets bundlers, local
servers, and self-hosted deployments keep the CSS and `w/` directory
together without depending on jsDelivr.

## Entry Points

- `all.css`: self-host all weights and both families.
- `100.css` ... `800.css`: self-host Gen Interface JP.
- `display-100.css` ... `display-800.css`: self-host Gen Interface JP Display.

- `cdn/all.css`: jsDelivr all weights and both families.
- `cdn/100.css` ... `cdn/800.css`: jsDelivr Gen Interface JP.
- `cdn/display-100.css` ... `cdn/display-800.css`: jsDelivr Gen Interface JP Display.

## Font Families

```css
body {{
  font-family: "Gen Interface JP", sans-serif;
}}

h1 {{
  font-family: "Gen Interface JP Display", sans-serif;
}}
```

## License

Generated fonts are licensed under the SIL Open Font License 1.1. See
`OFL.txt`.
"""
    (out_dir / "README.md").write_text(content, encoding="utf-8")


def write_npm_package(out_dir: Path, version: str, repository: str) -> None:
    package = {
        "name": NPM_PACKAGE_NAME,
        "version": version,
        "description": "Gen Interface JP web font subsets",
        "style": "all.css",
        "files": [
            "*.css",
            "cdn",
            "manifest.json",
            "README.md",
            "OFL.txt",
            "w",
        ],
        "repository": {
            "type": "git",
            "url": f"git+https://github.com/{repository}.git",
        },
        "homepage": f"https://github.com/{repository}",
        "license": "OFL-1.1",
    }
    (out_dir / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def asset_filename(version: str) -> str:
    """Asset filename for the GitHub Release zip.

    Embeds the version so a downloaded archive carries its identity in
    the filename itself (and so a v0.1.1 site build can link at the
    exact zip that matches the running font).
    """
    return f"GenInterfaceJP-{version}.zip"


def github_asset_urls(repository: str, tag: str, version: str) -> dict[str, str]:
    """Stable URLs for the GitHub Release asset.

    Only the tag-specific URL is exposed because the asset filename
    embeds the version: ``releases/latest/download/<filename>`` only
    resolves while the current "latest" release happens to ship that
    exact filename, so a versioned filename and a tag-pinned URL go
    together — older site builds keep pointing at the asset they were
    built against, even after a newer release becomes "latest".
    """
    base = f"https://github.com/{repository}/releases/download/{tag}"
    return {
        "bundle": f"{base}/{asset_filename(version)}",
    }


def build_release(args: argparse.Namespace) -> dict:
    version = normalized_version(args.version)
    tag = release_tag(version)
    release_dir = args.output.resolve()
    github_dir = release_dir / "github"
    npm_dir = release_dir / "npm"
    webfont_out = release_dir / "webfonts" / "gen-interface-jp"

    # GitHub Release ships TTFs only. Web delivery (subset WOFF2 chunks
    # behind unicode-range) flows through the npm package below; full
    # WOFF2 single-file is intentionally not redistributed (anyone
    # self-hosting can convert TTF→WOFF2 trivially, and the subset
    # delivery is the recommended path on the web).
    #
    # OFL.txt is added inline alongside the TTFs at the version-rooted
    # archive directory so anyone unzipping the bundle has the license
    # immediately at hand (matches OFL §2's "include this license"
    # requirement for redistribution).
    archive_root = f"GenInterfaceJP-{version}"
    write_zip(
        github_dir / asset_filename(version),
        family_files(version),
        inline={f"{archive_root}/OFL.txt": ofl_text()},
    )
    source = args.webfont_source.resolve()
    copy_webfont_package(source, npm_dir, include_nam=False)
    write_cdn_css_files(npm_dir, version)
    write_npm_license_files(npm_dir)
    write_npm_readme(npm_dir, version)
    write_npm_package(npm_dir, version, args.repository)
    copy_webfont_package(source, webfont_out)
    write_cdn_css_files(webfont_out, version)

    manifest = {
        "version": version,
        "tag": tag,
        "githubRepository": args.repository,
        "githubReleaseAssets": github_asset_urls(args.repository, tag, version),
        "webfonts": {
            "npmPackage": "npm",
            "npmAllCss": "npm/all.css",
            "npmAllCdnCss": "npm/cdn/all.css",
            "staticAllCss": "webfonts/gen-interface-jp/all.css",
            "staticAllCdnCss": "webfonts/gen-interface-jp/cdn/all.css",
        },
    }
    manifest_path = release_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {manifest_path}")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Release version. Defaults to GITHUB_REF_NAME or pyproject.toml.")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY), help="GitHub owner/repo for release URLs.")
    parser.add_argument("--output", type=Path, default=DEFAULT_RELEASE_DIR, help="Release output directory.")
    parser.add_argument("--webfont-source", type=Path, default=DEFAULT_WEBFONT_SOURCE, help="Built Gen Interface JP Regular webfont directory.")
    return parser.parse_args()


def main() -> None:
    build_release(parse_args())


if __name__ == "__main__":
    main()
