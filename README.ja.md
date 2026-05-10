# Gen Interface JP

<p><strong><a href="https://github.com/yamatoiizuka/gen-interface-jp/blob/main/README.md">English</a></strong> | 日本語</p>

Gen Interface JP は、デジタルインターフェースのために設計された、欧文と和文の調和を目指す書体です。  
明快な UI 用書体である Inter に Noto Sans JP の和文グリフを合わせ、多言語環境で一貫した読みやすさを実現します。

## Overview

### 2 Families

- **Gen Interface JP**: 汎用／本文用
- **Gen Interface JP Display**: 見出し用

### 8 Weights

- 100: Thin
- 200: ExtraLight
- 300: Light
- 400: Regular
- 500: Medium
- 600: SemiBold
- 700: Bold
- 800: ExtraBold

### Web Fonts

Web プロジェクトにおいて、head 内のスタイルシートの読み込みのみで Web フォントを使用できます。  
[Google Fonts と同様のサブセット化](https://developers.googleblog.com/google-fonts-launches-japanese-support/)により、単一フォントデータと比べ高速な表示を実現しています。

#### Gen Interface JP

```html
<!-- 
 index.html 
 100.css ... 800.css
 -->
<head>
  <link
    rel="stylesheet"
    href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/400.css"
  />
</head>
```

```css
/* style.css */
body {
  font-family: "Gen Interface JP", sans-serif;
  font-weight: 400; /* 100–800 */
}
```

#### Gen Interface JP Display

```html
<!-- 
 index.html 
 display-100.css ... display-800.css
 -->
<head>
  <link
    rel="stylesheet"
    href="https://cdn.jsdelivr.net/npm/gen-interface-jp@latest/cdn/display-800.css"
  />
</head>
```

```css
/* style.css */
h1,
h2 {
  font-family: "Gen Interface JP Display", sans-serif;
  font-weight: 800; /* 100–800 */
}
```

### CSS List

- `all.css`: 全 16 ウェイトの CSS
- `400.css`: Gen Interface JP Regular (400) の CSS
- `display-400.css`: Gen Interface JP Display Regular (400) の CSS

jsDelivr から直接読む場合は `cdn/*.css`、npm install 後やセルフホストでは
相対 `./w/...` の WOFF2 参照を保つ通常の `.css` を使用してください。

## Repository

```text
src/
  font/       # 中核のフォント生成
  webfont/    # Web フォント配信用 CSS + subset WOFF2
  release/    # GitHub Release / npm 配信用の梱包
site/         # ランディングページ兼フォント表示確認サイト
vendor/
  fonts/      # Inter と Noto Sans JP の入力フォント
  nam-files/  # Web フォント分割用の googlefonts/nam-files データ
docs/
  ARCHITECTURE.ja.md  # ビルドパイプラインの全体仕様
```

このリポジトリの主成果物は `src/font/` で生成するフォントファミリーです。`src/webfont/` と `src/release/` は、その生成物から派生する配信・公開用の工程です。生成物は `dist/` 配下に置かれ、リポジトリにはコミットしません。

ビルドパイプラインや内部仕様の詳細は [`docs/ARCHITECTURE.ja.md`](docs/ARCHITECTURE.ja.md) を参照してください。

## Quick Start

```bash
make font     # dist/ttf/ にフォントを生成
make site     # サイトをビルド (site/dist/)
make serve    # サイトのローカル開発サーバー
```

webfont サブセット化、リリース梱包、テスト、npm 公開などの全コマンドは [`docs/ARCHITECTURE.ja.md`](docs/ARCHITECTURE.ja.md) の「コマンド」を参照してください。

## License

このリポジトリのソースコードは [MIT License](LICENSE)、生成されたフォント本体は [SIL Open Font License 1.1](https://scripts.sil.org/OFL) です。　　

`vendor/` 配下は、それぞれに同梱のライセンスに従います。

## References

- [Noto Sans JP](https://github.com/notofonts/noto-cjk)
- [Inter](https://github.com/rsms/inter)
