# Vendored Source Fonts

This directory contains the third-party source fonts used to build Gen
Interface JP.

Expected layout:

```text
vendor/fonts/
  Inter-4.1/
  Noto_Sans_JP/
```

`src/font/build.py` reads most Inter static TTFs from `Inter-4.1/extras/ttf/`.
Thin and ExtraBold are generated from `Inter-4.1/InterVariable.ttf` so their
Latin weights can use tuned `wght` coordinates while keeping public metadata at
100 / 800. Noto Sans JP is read from
`Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf`.
