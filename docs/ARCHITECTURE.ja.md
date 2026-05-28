# Gen Interface JP — アーキテクチャ

## 概要

Gen Interface JP はフォントビルドパイプライン (アプリ/UI なし)。`make`
ターゲット経由で vendor のソースから配布用 TTF / WOFF2 / npm 成果物を
生成する。各ウェイトは Python の 3 ステージを通り、最後に静的デモサイトが
公開済みの webfont パッケージを参照する。

```
┌────────────────────────────────────────────────────────────────────┐
│  Source                                                            │
│    vendor/fonts/Inter-4.1/extras/ttf/Inter-{Weight}.ttf            │
│    vendor/fonts/Inter-4.1/extras/ttf/InterDisplay-{Weight}.ttf     │
│    vendor/fonts/Inter-4.1/InterVariable.ttf                        │
│    vendor/fonts/Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  font/build.py  (ファミリー × ウェイトごと)       │
        │                                                 │
        │   [1/3] Bake — font-baker, base-only            │
        │         Noto wght → static TTF                  │
        │         metadataMode=inheritBase                │
        │         output.upm=2048                         │
        │             ↓                                   │
        │   [2/3] Proportionalise — proportional.py       │
        │      palt → hmtx + 約物 ss09                    │
        │         tracking (連続記号は除外)              │
        │         + 極端な bbox の除去                    │
        │             ↓                                   │
        │   [3/3] Merge — font-baker                      │
        │         Inter (sub) + proportional Noto (base)  │
        │         subFont.excludeCodepoints で日本語慣習    │
        │         記号は Noto を維持                        │
        │         output.upm=2048, metricsSource=sub      │
        │         project version + manufacturer 刻印      │
        │         GSUB/GPOS coverage order 正規化          │
        │             ↓                                   │
        │   dist/ttf/  (ファミリー × ウェイトごとに TTF)    │
        │                                                  │
        └─────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  webfont/build.py — unicode-range サブセット化    │
        │     google-japanese ストラテジー (デフォルト) →    │
        │     all.css + ウェイト別 CSS + WOFF2 チャンク      │
        │     dist/webfont/gen-interface-jp/              │
        └─────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  release/build.py — パッケージング               │
        │     dist/release/github/   → GitHub Releases    │
        │     dist/release/npm/      → npm publish        │
        │     dist/release/webfonts/ → GitHub Pages       │
        └─────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────▼──────────────────────────┐
        │  site/  — Vite 静的デモサイト                     │
        │     jsDelivr (npm CDN) 経由で webfont を読込      │
        │     GitHub Pages へデプロイ                      │
        └─────────────────────────────────────────────────┘
```

## データフロー

### 1. フォントウェイトのビルド (`font.build`)

```
FAMILIES × WEIGHTS の各組合せに対して:
  → Inter source を選択:
      - ExtraLight から Bold は vendor の static Inter/InterDisplay TTF
      - Thin と ExtraBold は InterVariable.ttf から static instance を生成:
          Gen Interface JP         : opsz=14, wght=125 / 775
          Gen Interface JP Display : opsz=32, wght=125 / 775
        生成後の static instance は usWeightClass 100 / 800 に戻し、
        公開ウェイト名の metadata が内部 wght 座標に引きずられない
        ようにする。
  → font-baker bake: variable Noto → static TTF
                    inheritBase で designer/OFL/version を継承
                    weight だけを上書き
  → inst を再読込し、font-baker が 2048-UPM で bake した出力から
    palt/vpal 取得 (record はすでに active build grid 上)
  → ss09 約物を除き Noto の palt エントリを全量で焼き込み
    ss09 約物は 34% を base に焼き、66% を ss09 残差として保持
    (palt なしグリフはメトリクス維持)
  → make_proportional で palt → hmtx に焼き込み
    palt/vpal/halt/vhal を削除; 横方向の palt 残差と縦方向の
    vpal record は final ss09 用に保持
  → _apply_tracking で advance を広げ LSB を半分シフト
    ただし family["trackingIgnore"] の連続・隙間なし記号は除外
  → _apply_glyph_spacing で family["glyphSpacing"] の個別調整を適用
  → _strip_extreme_glyphs で縦組み用繰り返し記号 〱-〵 を無効化
    (1000-UPM 設計値: yMax > 1200 / yMin < -400)
  → font-baker merge: Inter source + proportional Noto
                     subFont.excludeCodepoints = SUB_EXCLUDE_CODEPOINTS で
                     日本語慣習記号 (① Ⓐ ※ ◯ …) は Noto を維持
                     glyph-name collision (Inter U+0298 と Noto U+25CE が
                     共に `uni25CE`) も font-baker が自動 rename
                     family/weight を「Gen Interface JP …」に刻印
                     output.upm=2048 で Inter ネイティブグリッドを維持
                     metricsSource=sub で Inter 基準の hhea を採用
                     project version を name metadata に刻印
                     manufacturer / manufacturerURL を刻印
  → final TTF を一度 reload/save し、GSUB/GPOS coverage を
    merge 後の glyph ID 順に正規化
  → merge 時の glyph rename 後も horizontal / vertical glyph に optional
    約物挙動が残るよう、final cmap / glyph 名に対して yakumono-only ss09 を生成
```

### 2. Web 用サブセット化 (`webfont.build`)

```
dist/ttf/{family}/{family}-{weight}.ttf を読み込み
  → プランナーがストラテジー選択:
      google-japanese (デフォルト) — Google Fonts の日本語
                                   unicode-range スライスを再現
                                   (~120 チャンク)
      gen              — 手動設計のスライス計画
  → 各 (family × weight × slice) に対して:
      fontTools.subset → WOFF2 チャンク
      .nam ファイル → 人間可読のコードポイント一覧
  → スライスごとに `unicode-range:` 付き @font-face を生成
  → all.css (フルファミリー) + ウェイト別 CSS を出力
    npm / self-host 用に WOFF2 は相対 ./w/... URL で参照
  → manifest.json にサイズ / brotli サイズを記録
```

### 3. パッケージング & 公開 (`release.build`)

```
dist/ttf/ + dist/webfont/gen-interface-jp/ を要求
  → GenInterfaceJP-<version>.zip (TTF、全ウェイト × 両ファミリー)
  → webfont package を npm/      にコピー (package.json + README.md 同梱)
  → webfont package を webfonts/ にコピー (Pages 配信用ミラー)
  → バージョン固定の jsDelivr WOFF2 URL を持つ cdn/*.css を追加
  → manifest.json に version, tag, アセット URL を記録
```

## ビルドパイプライン (`font/build.py`)

### Stage 1 — variable から static へベイク

font-baker を Noto Variable に対して base-only で実行。wght 軸はファミリー
ごとのウェイト値に固定 (`Regular = 465`、`SemiBold = 690` のように非整数:
Noto の軸は非線形で、整数位置の太さでは Inter より細く見えるため目視で
合わせ込んだ値)。`output.metadataMode = "inheritBase"` で Noto の identity
レコード (designer / OFL / manufacturer / version) をそのまま通すので、
inst TTF は手動 save/restore なしにクリーンな出元メタデータを持って
Stage 2 に渡る。

この最初の段階で `output.upm = 2048` を指定する。Noto のソースは 1000 UPM
だが、Inter / Inter Display は 2048 UPM がネイティブなので、プロジェクト側の
spacing 作業に入る前に Noto intermediate を Inter のグリッドへ移す。これにより
final merge で Inter を 1000 UPM に丸め落とさずに済む。ここでは font-baker が
glyph order 全体をスケールする前提で、縦組み代替のような unmapped glyph も
palt record を持ちうるため対象に含める。

### Stage 2 — プロポーショナル化 + メトリクス調整

inst に対して 4 つのサブパスを in-place で実行:

1. **palt のベイク / 約物 ss09** (`proportional.make_proportional`) — palt 値は
   font-baker が作った freshly baked inst TTF から読む。Stage 1 は
   `output.upm = 2048` で実行済みなので、GPOS ValueRecord はすでに active
   build grid 上にあり、このプロジェクト側では再スケールしない。
   XPlacement / XAdvance を LSB / advance に加算しアウトラインをシフト。
   Noto の palt エントリは
   原則として全量で焼き込むが、`PALT_FEATURE_CHARS` の約物は分割する。
   palt 調整量の 34% を `hmtx` に焼き込んでデフォルトでもある程度
   詰まる base metrics にし、残り 66% は final の yakumono-only `ss09`
   stylistic set「約物半角」用に保持する。Noto の典型的な 1000-UPM source
   scale の `XAdvance=-500` 約物は 2048-UPM では `-1024` 相当になり、
   palt-off で `-348` が base advance に焼かれ、`ss09` 有効時に残り
   `-676` が適用される。runtime `vpal` は feature としては
   再生成しないが、選択した約物 record は縦組み用 `.ss09` alternate の
   source data として保持し、`vmtx` に縦方向の placement / advance delta を
   焼き込む。final font では `palt`, `vpal`, `halt`, `vhal` を削除し、
   optional 約物 spacing は `ss09` だけで公開する。palt なしグリフは自動的に
   sidebearing を詰めない; 後続の明示的な spacing ルールが触らない限り
   元の `hmtx` を維持する。
   `U+30FB` (・) は Noto では `U+2027` (‧) と同じ `uni2027` を共有して
   いるため、palt ベイク前に `uni30FB` として分離する。これにより `‧` と
   `・` は必要に応じて別々の optional spacing record を持てる。
2. **トラッキング** (`_apply_tracking`) — advance を `tracking` 分広げ、
   `tracking // 2` を LSB に加算してアウトラインを広がった枠の中央に
   配置。kana / 句読点はファミリー設定の `trackingKana` で別値。
   これらの値は 1000-UPM 設計値として持ち、normal family では `+30` / `+40`
   を active UPM に換算して適用する。`trackingIgnore` はコードポイント / 範囲を受け取り、cmap で解決した
   グリフを完全にスキップする。デフォルトでは Noto の tracking stage で
   Box Drawing (`U+2500-U+257F`)、Block Elements (`U+2580-U+259F`)、
   2 点 / 中点 leader (`U+2025`, `U+22EF`)、半角中黒 (`U+FF65`)、`U+3030`、
   縦組み・互換 leader 形式 (`U+FE19`, `U+FE30-U+FE34`,
   `U+FE49-U+FE4F`)、two-/three-em dash (`U+2E3A`, `U+2E3B`)、全角
   low line (`U+FF3F`)、全角 macron (`U+FFE3`) を除外し、隙間なしで
   反復される記号のリズムを保つ。
3. **個別グリフのスペーシング** (`_apply_glyph_spacing`) — palt + 一律
   トラッキングだけでは追い込めない稀なグリフのための手動レイヤー。
   ファミリー設定の `glyphSpacing` がコードポイント (または 1 文字) を
   1000-UPM 設計値の `(lsb_delta, rsb_delta)` ペアにマップし、適用前に
   active UPM へ換算する: `lsb_delta` は hmtx LSB と
   advance を同量増やし、`rsb_delta` は advance を右側だけ広げる。
   アウトライン座標は触らない。各エントリは特定グリフを特定の隣接リズムに
   対して個別チューニングする想定なので、慎重に追加すること。現在の調整値は
   `font/build.py` の `FAMILIES` を参照。小書きひらがな / カタカナは、
   palt 後に詰まり気味に見えるため、このレイヤーで左右に明示的な余白を
   足す。大半の小書き仮名は左右 15 units を基準にしつつ、カタカナの
   小書き「ィ」「ャ」や、ひらがなの小書き「ょ」などは見え方に合わせて
   個別値を持つ。`U+30FB` (・) を含む約物はここでは扱わない: tracking は
   通常どおり通し、横方向の詰めは明示的な `ss09` feature に任せる。
4. **bbox 除去** (`_strip_extreme_glyphs`) — 下記 [垂直メトリクス] 参照。

オプションの **横スケール** (`xScale` 設定、現在未使用) は上記の後に
動き、CJK を縦方向は触らず横だけ縮める。

### Stage 3 — Inter とマージ

font-baker のマージモード: Inter が sub、プロポーショナル Noto が base。
`subFont.excludeCodepoints = SUB_EXCLUDE_CODEPOINTS` で日本語慣習として
Noto 由来で残したい記号 (`①` `Ⓐ` `※` `◯` …) を列挙すると、font-baker は
マージ前に Inter の cmap から該当エントリを剥がし、base のグリフを生かす。
font-baker はさらに **クロスコードポイントなグリフ名衝突** も自動検出する:
Inter の U+0298 (`ʘ`) と Noto の U+25CE (`◎`) は両方 `uni25CE` という
glyph 名で出荷されているため、放置すると Inter 側が `◎` を上書きしてしまう。
font-baker は sub のほうを `uni25CE.sub` にリネームし、base のグリフを温存
する。この 2 段で、直接重複と命名衝突の両方を、こちら側で cmap を手術せず
にカバーできる。

`output.upm = 2048` で final TTF でも Inter のネイティブ座標グリッドを維持し、
`output.metricsSource = "sub"` で merged の hhea / OS/2 包絡線を Inter 側に
揃え、欧文のメトリクスが行高を駆動する。`BASELINE_OFFSET = 25` は 1000-UPM
設計値で、2048-UPM build では `51` として Noto を上に持ち上げる。これにより
CJK 漢字が Latin の caps と光学的に同じベースラインに乗る。`SCALE = 0.925` は
UPM 変換ではなく光学的な CJK デザインスケールとして残し、CJK 1 文字の幅が
Inter の cap-height と揃うように Noto を縮める — 欧文/CJK 混植で CJK を少し
小さくして釣り合いを取る、という慣例的な配分。

`output.version` は共有 helper `project_metadata.project_version()` 経由で
`pyproject.toml` から読み、final TTF の nameID 5 と nameID 3 に
release zip / npm package / site metadata と同じ project/release version を
刻印する。OpenType の version 比較では先頭の `major.minor` numeric prefix
だけが使われるため、`1.2.3` のような project version は name string には
残り、`head.fontRevision` は numeric prefix の `1.2` 相当になる。
`output.manufacturer = "Yamato Iizuka"`、`output.manufacturerURL =
"https://yamatoiizuka.com"` でリリース TTF の nameID 8 / 11 を刻印。
merge 後は final TTF を一度 fontTools で reload/save し、GSUB/GPOS coverage を
最終 glyph order に合わせて正規化する。その後、merge 前に codepoint keyed で
保持した横方向 record と codepoint / glyph keyed の縦方向 record から、
最小 runtime `ss09` 挙動を final cmap / glyph order に対して生成する。
これは `U+FF40` (｀) のように、Noto では `U+2035` と同じ `uni2035`
を共有するが、Inter 側の `U+2035` と衝突して merge 後に
`uni2035.orig` へ rename される glyph で必要になる。この final install は
merge 後に行うため、保持していた残差 record も `SCALE` で換算し、live `ss09`
alternate が光学スケール済みの Noto base と揃うようにする。

## プロポーショナルメトリクス (`font/proportional.py`)

CJK フォントは全角がデフォルト: 全グリフがアウトライン幅に関係なく同じ
em-square を占有し、`palt` GPOS が runtime に kana / Latin を光学的に
詰める。`palt` を有効にしないアプリ (Adobe の和文コンポーザー、ブラウザ
フォールバック、CJK を等幅扱いするレイアウトエンジン) では live の調整が
効かない。Gen Interface JP では多くの palt を `hmtx` に焼き込み、optional
約物にも reduced palt 分を base に焼くことで、palt 無効時も完全な等幅
フォールバックにならないようにする。

`make_proportional` は多くの `palt` 値を static の `hmtx` に焼き込む。
`runtime_palt` と `runtime_palt_base_scale` が指定された場合は、その割合を
base metrics に焼き込む。production build では `install_runtime_palt` を無効化し、
残差を live `palt` としては公開せず、codepoint 単位で保持して Inter merge 後の
yakumono-only `ss09` として再生成する。本プロジェクトでは
`RUNTIME_PALT_BASE_SCALE = 0.34` を使うため、palt-off の約物はすでに部分的に
詰まり (2048-UPM build grid 上の典型的な `-1024` palt advance なら `-348`)、
`ss09` を有効にすると従来の Noto full palt 目標まで到達する。
production build では横方向の `ss09` target に `PALT_FEATURE_CHARS`
(48 文字)、縦方向の `ss09` target に `SS09_VERTICAL_FEATURE_CHARS` と
`SS09_VERTICAL_FEATURE_GLYPHS` を使う。後者は Noto の `vpal` 値を
source data として使うが、runtime `vpal` feature は公開せず alternate glyph
metrics に焼き込む。これにより、焼き込み済みの kana / Latin に palt が
二重適用されることを避けながら、optional 約物 spacing は `ss09` に集約する。
TrueType アウトラインのみ対応 (palt のベイクは `glyf` に書き戻すので
CFF は対象外)。
Inter との merge では base 側 glyph が rename されることがあるため、
ビルドは merge 前に横方向の残差を codepoint 単位で、縦方向の調整を
codepoint または glyph 名で保持し、merge 後の final cmap / glyph order に
対して retarget する。final record には Noto optical scale (`SCALE = 0.925`)
をこの時点でだけ掛ける。pre-merge の `palt_data` / `vpal_data` は active
UPM grid のままにし、焼き込み base metrics は merge 時に一度だけ scale
される。

`_remove_prop_features` は GPOS を 2 段で歩く: FeatureRecord の削除と、
それに対応する LangSys インデックスの再マップ。レコード削除は後ろの全
レコードのインデックスを動かすので、各 LangSys の `FeatureIndex` 配列を
生き残ったレコードに対して再キーする必要がある。feature 削除後は、どの
生存 feature からも参照されない lookup だけを pruning する; 共有 lookup は
残す。`kern` は GPOS に残る。横方向の optional 約物は
GSUB 側で扱う: `_install_ss09_punctuation_feature` が従来の palt 残差から
`.ss09` metric alternate を作り、UI 名「約物半角」の `ss09` stylistic set
を生成する。縦組み用 glyph については、従来の vpal YPlacement / YAdvance を
`vmtx` に焼き込んだ `.ss09` alternate を作る。既存の PairPos kerning は
`.ss09` alternate にも拡張するため、substitution 後も `kern` は効き続ける。
横方向のみの alternate は元 glyph の `vmtx` record もコピーするため、保存後の
font でも vertical metrics table の長さが拡張後の glyph order と揃う。
`ss09` を追加した後は GSUB `FeatureList` を
`FeatureTag` 順に戻し、LangSys の feature index を再マップする。lookup order
は変更しない。

## 垂直メトリクスと Illustrator のテキストボックス問題

### 背景

Illustrator では、CJK グリフを含むフォントは強制的に **Japanese コンポーザー**
で扱われ、行送りが「ポイントサイズ × 固定倍率(170%前後)」になる。
Inter の Latin 専用挙動 (各行のグリフに応じた行高動的調整) はフォント側
から制御不能 (Illustrator の仕様)。

ただし、**テキストフレームの自動サイズ**は `head.yMax` / `head.yMin` を
参照しているため、ここを縮められれば少なくともテキストフレームの上下
余白は小さくなる。

### 削除するグリフ

`_strip_extreme_glyphs` は縦組み用イテレーションマーク `U+3031-U+3035`
を明示的に無効化し、さらに `yMax > 1200` または `yMin < -400`
という 1000-UPM 設計グリッド上の閾値に該当するグリフも無効化する。実際の
比較前に active UPM へ換算するため、2048-UPM build ではおよそ
`yMax > 2458` / `yMin < -819` になる。Noto Sans JP で bbox の外れ値に
なるのは全形の縦組み用イテレーションマークと `vert` / `vrt2` 代替。
上半分/下半分の名残 (`〳〴〵`) は bbox としては外れ値ではないが、横組み
テキスト内で Adobe の Japanese コンポーザーを混乱させるため、コードポイント
指定で削除する。

| Glyph | Codepoint | 削除理由 |
|---|---|---|
| `uni3031` 〱 | U+3031 | 縦組み用繰り返し記号 |
| `uni3032` 〲 | U+3032 | 縦組み用濁点付き繰り返し記号 |
| `uni3033` 〳 | U+3033 | 縦組み用繰り返し記号の上半分 |
| `uni3034` 〴 | U+3034 | 縦組み用濁点付き繰り返し記号の上半分 |
| `uni3035` 〵 | U+3035 | 縦組み用繰り返し記号の下半分 |
| (vert alternate) | (unmapped) | `uni3031` の vert/vrt2 代替 |
| (vert alternate) | (unmapped) | `uni3032` の vert/vrt2 代替 |

スロット自体は残してアウトラインを空にするので GSUB / GPOS のインデックス
は崩れない。cmap エントリは落とすので、コードポイントを直接打つと
.notdef にフォールスルーする。

| | yMin / yMax | span |
|---|---|---|
| Before (Noto そのまま) | -1047 / +1807 | 2.85×em |
| After (final 2048 UPM) | 約 -660 / +2269 | ~1.43×em (Inter 相当) |

### 設計方針 — UI フォントとして横組み専用

本フォントは **UI・本文用途の横書き専用** として設計する。

- **縦組み・伝統的な日本語組版は非対応。**
- em-square 厳密準拠 (Hiragino 式 hhea = 880 / -120) は追求しない —
  `metricsSource: "sub"` で Inter の比率 (~1.21×em) を継承しているため、
  ベトナム語・ダイアクリティカル付き Latin (~1.11×em) が切り詰めで欠ける。
- トレードオフは受け入れる: head bbox を削って Illustrator のフレーム
  自動サイズを改善する。縦組みイテレーションマーク 〱〲 はこのフォントで
  描画されないが、UI 用途では使わない、という判断。

## Webfont サブセット化 (`webfont/build.py`)

`font.build` の TTF をそのまま Web に乗せるには大きすぎる (1 ウェイト
あたり約 5 MB)。`webfont.build` は各ウェイトを Unicode の範囲で
スライスし、各スライスに対して `unicode-range:` 付き `@font-face` を 1 つ
出す。ブラウザはページのテキストが参照したチャンクだけをダウンロードする。

### ストラテジー

- **`google-japanese`** *(デフォルト)* — Google Fonts の日本語スライス
  方式 (`vendor/nam-files/slices/japanese_default.txt`) を再現。Google
  ホスティングの Noto と同じチャンク境界を使うので、カバレッジとキャッシュ
  挙動が既存の日本語サイトと整合する。
- **`gen`** — 手動設計のプラン: Latin / kana / 句読点 / JIS 16-92 区 /
  残余漢字を `extra_han_slices` で均等分割。

### 出力

```
dist/webfont/gen-interface-jp/
  all.css                # 全ウェイト × 両ファミリー
  400.css                # normal Regular (ウェイトごとに 1 ファイル)
  display-400.css        # display Regular (ウェイトごとに 1 ファイル)
  ...
  w/{family}/{weight}/{slice}.woff2
  nam/{slice}.nam        # 人間可読のコードポイント一覧
  manifest.json          # スライスごとのサイズ / brotli サイズ
```

この段階の CSS は意図的に相対 `./w/...` の WOFF2 URL を使う。
`release.build` は npm install 後やセルフホスト向けに通常の `.css` を残し、
同時に WOFF2 URL をバージョン固定の jsDelivr 絶対 URL へ書き換えた
`cdn/*.css` を出す:

```
dist/release/npm/
  all.css
  cdn/all.css
  400.css
  cdn/400.css
  display-400.css
  cdn/display-400.css
  README.md              # CDN と self-host の使い分けを説明
```

`benchmark.mjs` (Node) はローカルサブセットに対する throttled fetch を
再生し、スライス分割が単一フル WOFF2 比でペイするかを検証する。
比較対象のフル WOFF2 は Regular TTF からオンデマンドで生成する
(`webfont.build` の `--all` なしモード経由)。リリース成果物には含めない。

## リリースパッケージング (`release/build.py`)

下流のコンシューマーが 3 種類、出力も 3 種類:

- **GitHub Releases** (`dist/release/github/`) —
  `GenInterfaceJP-<version>.zip` 1 本に TTF 全 16 本 (両ファミリー × 8
  ウェイト) を同梱。アセット名にバージョンが埋め込まれているので、より
  新しいリリースが「latest」になった後でも各リリースを一意にリンクできる。
  フル WOFF2 単一ファイルは意図的に再配布しない — Web 配信は下記 npm
  サブセット経由が本道、自前ホスティングする場合も TTF→WOFF2 変換は
  fontTools / pyftsubset で容易。
- **npm パッケージ** (`dist/release/npm/`) — webfont サブセット +
  自動生成された `package.json` (name, version, files, OFL-1.1 license) と
  `README.md`。通常の `all.css` / ウェイト別 CSS は npm install 後や
  セルフホスト向けに相対 WOFF2 URL を使う。`cdn/*.css` は jsDelivr
  直読み用に、バージョン固定の絶対 WOFF2 URL を使う。
- **GitHub Pages ミラー** (`dist/release/webfonts/gen-interface-jp/`) —
  webfont CSS / WOFF2 レイアウトの静的ミラーを、デモサイトの隣で配信。

バージョンは `pyproject.toml` (CI では `GITHUB_REF_NAME`) から読む。
font build も同じ共有 metadata helper で `pyproject.toml` を読み、final TTF の
version record を刻印するため、フォント本体とリリース成果物の version source
は分岐しない。github / npm / webfonts ディレクトリの隣にある `manifest.json` には
リリース URL が記録され、下流ツールが参照できる。

## サイト (`site/`)

`site/` 配下の Vite 静的サイト。実行時に jsDelivr の npm CDN 経由で公開
済み webfont パッケージをロードする — つまりライブサイトはサードパーティ
コンシューマーが使うのと同じ npm 成果物を使い、エンドツーエンドでパッケージ
の動作確認になる。サイトは jsDelivr へ直接リンクするため `cdn/*.css` を使う。
GitHub Pages のデプロイは `.github/workflows/pages.yml`
で実行。

## テスト

```bash
PYTHONPATH=src python3 -m pytest        # 全テスト (~35 秒)
```

テストは表面ごとに `tests/` 直下に分割:

- **`tests/conftest.py`** — 共有フィクスチャ: 実 palt / vpal / vert / cmap データ
  が必要なテスト用に Noto Variable のサブセットをセッション単位でキャッシュ;
  全グリフ走査が必要な mutation テスト用には `FontBuilder` で組み立てた
  最小 TrueType (Noto 17000 グリフを毎回触るのは無駄)。
- **`tests/test_font_build.py`** — UPM 設計値換算、project-version metadata
  forwarding
  (`SOURCE_UPM = 1000`, `TARGET_UPM = 2048`)、`_glyph_codepoint`, `_is_kana_or_punct`,
  `_is_cjk_codepoint`, `_is_kana_letter`, `_get_cjk_glyphs`,
  `_get_vert_alternates`, `_apply_x_scale`, `_strip_extreme_glyphs`,
  `_apply_tracking`, `_apply_glyph_spacing`, `_glyphs_for_codepoints`,
  `_split_cmap_codepoint_glyph`、明示的な palt/ss09 spacing 方針、
  vendor palt/vpal policy check、Thin / ExtraBold 用の
  InterVariable edge instance source 選択。
- **`src/font/verify_edge_instances.py`** — Thin / ExtraBold のビルド後検証。
  生成された InterVariable static instance が vendor static と同じ cmap /
  GSUB / GPOS 表面を保ち、variation table を残さず、指定 `wght` / `opsz`
  座標になっていること、さらに final TTF の metadata が public weight
  100 / 800 のままで InterVariable axis 名を漏らさないことを確認する。
- **`tests/test_proportional.py`** — `_read_palt`, `_read_vpal`,
  `_shift_glyph_x`, `_remove_prop_features`, `make_proportional` (palt
  ベイク、optional runtime-palt 再生成、runtime-palt の base/residual 分割、
  reduced palt scale、palt なしグリフのメトリクス維持、
  オプションの squeeze SB sidebearing 計算、
  palt_override の優先、CFF 拒否、feature 削除後の LangSys index 整合性)、
  さらに `ss09` 生成と HarfBuzz shaping。
- **`tests/test_release.py`** — 公開配布の契約: GitHub アセット URL の形
  (サイトのダウンロードボタンが参照)、npm パッケージのレイアウト
  (`files` glob、生成 README、`cdn/*.css` エントリポイント、`license`
  メタデータ、CSS エントリポイントの root 配置)。
- **`tests/test_webfont_build.py`** — コードポイント範囲のマージ、
  unicode-range のフォーマット (5 桁含む)、JIS 区 → コードポイント、
  サブセット計画の配置 / 非重複 / 完全カバレッジ、Google-Japanese
  ストラテジーパーサーのエッジケース (コメント内 `}` を含む)。

| ファイル | テスト数 | 検証内容 |
|---|---|---|
| `test_font_build.py` | 102 | UPM 換算ポリシー、project-version metadata forwarding、グリフ名パース、kana / CJK 分類、GSUB/GPOS 走査、x-scale、bbox 除去、tracking、ss09 feature の retarget、final runtime feature scaling、InterVariable edge instance 互換性 |
| `test_proportional.py` | 36 | palt/vpal 抽出、グリフ平行移動、GPOS feature 削除、runtime-palt/vpal helper coverage + base/residual 分割 + optional squeeze helper、ss09 生成 + shaping |
| `test_release.py` | 2 | GitHub アセット URL 契約、npm パッケージレイアウト (files glob、license、README、self-host/CDN CSS root 配置) |
| `test_webfont_build.py` | 42 | 範囲マージ / 重複除去、5 桁 hex 含む unicode-range、JIS 区マッピング、サブセット計画の配置 / 非重複 / 完全カバレッジ、ストラテジーパーサーのエッジケース |

## コマンド

| コマンド | 用途 |
|---|---|
| `make font` | 全ファミリー × 全ウェイトの TTF を生成 |
| `make verify-edge-instances` | Thin / ExtraBold をビルドし、InterVariable edge instance と final TTF metadata を検証 |
| `make webfont` | unicode-range サブセットを生成 (`font` 依存) |
| `make release` | GitHub zip + npm + Pages パッケージ生成 (`webfont` 依存) |
| `make webfont-benchmark` | スライス方式の throttled fetch ベンチ |
| `make npm-pack` | npm パッケージのドライラン検査 |
| `make npm-publish` | npm に publish |
| `make site` | デモサイトのビルド (`site/dist/` がそのまま GitHub Pages artifact) |
| `make serve` | サイトのローカル Vite 開発サーバー |
| `make clean` | `dist/` と `site/dist/` を削除 |
| `python3 -m font.build [family] [weight ...]` | 部分ビルド (例: `normal Regular`) |
| `python3 -m font.verify_edge_instances` | ビルド済み Thin / ExtraBold edge output を検証 |
| `python3 -m pytest` | テスト実行 |

CI: `.github/workflows/pages.yml` が `main` への push ごとにデモサイトを
GitHub Pages にデプロイ。リリースパッケージングはローカル実行に統一 —
詳細は `src/release/README.md` の `make release` + `gh release upload`
の手順を参照。

## 依存

### Python

- `ofl-font-baker` (>= 0.4.6) — コンポジットフォントマージエンジン。
  `metadataMode` で base / sub の identity を継承する。Stage 1 (bake) と
  Stage 3 (merge) を駆動。0.4.0 で `subFont.excludeCodepoints` と
  glyph-name collision rename が追加され、merge 段で日本語慣習記号を
  Noto に残すために利用している。0.4.1 で rename / duplicate された
  グリフに対して縦書き metrics (`vmtx` / `VORG`) と `vert` / `vrt2` GSUB
  マッピングを base から継承するように修正され、上書き対象の縦書き
  位置が崩れない。0.4.5 で `output.upm` が追加され、Noto bake と final merge
  の両段で 2048 UPM の Inter グリッドを維持するために使っている。0.4.6 で
  `output.upm` 適用時の layout table もスケールされるため、この pipeline が
  読むプロポーショナル / 縦書き位置データも active build grid 上に揃う。
- `fonttools` (>= 4.47.0) — フォントパース、instancer、subsetter、
  GPOS / GSUB の編集。
- `freetype-py` — メトリクス検証ツーリングで使用。
- `brotli` — WOFF2 圧縮 (fonttools 経由の transitive)。
- `pytest` — テストランナー。

### Node.js (サイトのみ)

- `vite` — ビルドツール / 開発サーバー。
- サイトは webfont のソースには依存しない — jsDelivr 経由で公開済み npm
  パッケージを読み込む。

## このドキュメントの保守

以下のいずれかに変更が入った場合、本ファイル (および `ARCHITECTURE.md`) を
更新する:

- ビルドパイプラインの形 (ステージ境界、出力ファイル、中間生成物)
- プロポーショナル化 / トラッキング / bbox 除去の方針
- Webfont サブセット化のストラテジーや出力レイアウト
- リリースパッケージング表面 (zip 名、npm パッケージ形、manifest フィールド)
- テスト基盤 (フィクスチャ、ファイル分割)
- vendor 依存や CI ワークフロー

ドキュメントとコードを同期させるのは変更のフォローアップではなく、
変更そのものの一部とする。
