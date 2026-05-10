import { useEffect, useState, type ReactNode } from "react";
import { APP_VERSION } from "../config";
import { SectionHead } from "./SectionHead";
import { Slider } from "./Slider";

type WeightCell = { sample: string; label: string; weight: number };

const WEIGHTS: WeightCell[] = [
  { sample: "あA1", label: "Thin", weight: 100 },
  { sample: "いB2", label: "ExtraLight", weight: 200 },
  { sample: "うC3", label: "Light", weight: 300 },
  { sample: "えD4", label: "Regular", weight: 400 },
  { sample: "おE5", label: "Medium", weight: 500 },
  { sample: "かF6", label: "SemiBold", weight: 600 },
  { sample: "きG7", label: "Bold", weight: 700 },
  { sample: "くH8", label: "ExtraBold", weight: 800 },
];

const WEIGHT_NAMES: Record<number, string> = Object.fromEntries(
  WEIGHTS.map(({ weight, label }) => [weight, label]),
);

type CommandToken = string | { text: string; href: string };

type ToolBlock = {
  name: string;
  href: string;
  meta: string;
  commands: CommandToken[][];
  bitmapDir: string;
};

const TOOLS: ToolBlock[] = [
  {
    name: "LVGL / lv_font_conv",
    href: "https://github.com/lvgl/lv_font_conv",
    meta: "24px ･ Regular ･ 16階調",
    commands: [
      [
        { text: "lv_font_conv", href: "https://github.com/lvgl/lv_font_conv" },
        " --bpp 4 --size 24 --font GenInterfaceJP-Regular.ttf -o GenInterfaceJP-24.bin",
      ],
    ],
    bitmapDir: "lvgl",
  },
  {
    name: "U8g2 / bdfconv",
    href: "https://github.com/olikraus/u8g2",
    meta: "24px ･ Regular ･ 2階調",
    commands: [
      [
        { text: "otf2bdf", href: "https://github.com/jirutka/otf2bdf" },
        " -p 24 GenInterfaceJP-Regular.ttf -o GenInterfaceJP-Regular-24.bdf",
      ],
      [
        { text: "bdfconv", href: "https://github.com/olikraus/u8g2" },
        " -f 1 GenInterfaceJP-Regular-24.bdf -o gen_interface_r24.c -n u8g2_font_gen_r24",
      ],
    ],
    bitmapDir: "u8g2",
  },
];

type FontMode = "normal" | "display" | "all";

const MODES: { key: FontMode; label: string }[] = [
  { key: "normal", label: "Normal" },
  { key: "display", label: "Display" },
  { key: "all", label: "All" },
];

function CodeLine({
  className,
  children,
}: {
  className?: string;
  children?: ReactNode;
}) {
  return (
    <span className={"webfont__code-line" + (className ? ` ${className}` : "")}>
      {children}
    </span>
  );
}

/* Syntax-highlight token. Colors are mapped 1:1 to vscode-theme-w3schools-light:
   - comment / tag-punct / tag-name / attr / string / selector / prop / value /
     punct — each rendered via .webfont__code-<type> in styles.css. */
function Tok({ type, children }: { type: string; children: ReactNode }) {
  return <span className={`webfont__code-${type}`}>{children}</span>;
}

function LinkLine({ fileName }: { fileName: string }) {
  const url = `https://cdn.jsdelivr.net/npm/gen-interface-jp@${APP_VERSION}/${fileName}`;
  return (
    <CodeLine className="webfont__code-line--indent-2">
      <Tok type="tag-punct">{"<"}</Tok>
      <Tok type="tag-name">{"link"}</Tok> <Tok type="attr">{"rel"}</Tok>
      <Tok type="tag-punct">{"="}</Tok>
      <Tok type="string">{`"stylesheet"`}</Tok> <Tok type="attr">{"href"}</Tok>
      <Tok type="tag-punct">{"="}</Tok>
      <Tok type="string">{`"${url}"`}</Tok>
      <Tok type="tag-punct">{">"}</Tok>
    </CodeLine>
  );
}

function CssRule({
  selector,
  family,
  weight,
  weightRange,
}: {
  selector: ReactNode;
  family: string;
  weight: number;
  weightRange?: string;
}) {
  return (
    <>
      <CodeLine>
        {selector} <Tok type="punct">{"{"}</Tok>
      </CodeLine>
      <CodeLine className="webfont__code-line--indent-2">
        <Tok type="prop">{"font-family"}</Tok>
        <Tok type="punct">{":"}</Tok> <Tok type="string">{`"${family}"`}</Tok>
        <Tok type="punct">{","}</Tok> <Tok type="value">{"sans-serif"}</Tok>
        <Tok type="punct">{";"}</Tok>
      </CodeLine>
      <CodeLine className="webfont__code-line--indent-2">
        <Tok type="prop">{"font-weight"}</Tok>
        <Tok type="punct">{":"}</Tok> <Tok type="value">{`${weight}`}</Tok>
        <Tok type="punct">{";"}</Tok>
        {weightRange ? (
          <>
            {" "}
            <Tok type="comment">{`/* ${weightRange} */`}</Tok>
          </>
        ) : null}
      </CodeLine>
      <CodeLine>
        <Tok type="punct">{"}"}</Tok>
      </CodeLine>
    </>
  );
}

export function Variations() {
  const [mode, setMode] = useState<FontMode>("normal");
  const [weight, setWeight] = useState(400);
  const showNormal = mode !== "display";
  const showDisplay = mode !== "normal";
  const showSlider = mode !== "all";

  const linkFile =
    mode === "all"
      ? "cdn/all.css"
      : mode === "normal"
        ? `cdn/${weight}.css`
        : `cdn/display-${weight}.css`;

  // Selection-aware underline: hide the underline on `.bitmap-tool__cmd-link`
  // while it (or any part of it) is part of a non-collapsed selection so the
  // user can read/copy the URL without the underline visually merging into
  // descenders. ::selection { text-decoration: none } isn't honored in
  // Chrome/Safari (text-decoration is painted on the box, not the selection
  // fragment), so we toggle a class instead.
  useEffect(() => {
    const handler = () => {
      const sel = document.getSelection();
      const links = document.querySelectorAll<HTMLElement>(
        ".bitmap-tool__cmd-link",
      );
      links.forEach((el) => {
        let inside = false;
        if (sel) {
          for (let i = 0; i < sel.rangeCount; i++) {
            const r = sel.getRangeAt(i);
            if (!r.collapsed && r.intersectsNode(el)) {
              inside = true;
              break;
            }
          }
        }
        el.classList.toggle("is-selected", inside);
      });
    };
    document.addEventListener("selectionchange", handler);
    return () => document.removeEventListener("selectionchange", handler);
  }, []);

  return (
    <section className="variations">
      <SectionHead name="Variations" />

      <div className="variations__body">
        <hr className="variations__rule" />

        <div className="variations__row">
          <span className="variations__caption">2 Families</span>
          <div className="styles-row">
            <div className="styles-cell">
              <span className="styles-cell__head">
                {"Type Design\n書体デザイン"}
              </span>
              <span className="styles-cell__name styles-cell__name--narrow">
                {"Gen Interface JP\n汎用／本文用"}
              </span>
            </div>
            <div className="styles-cell styles-cell--display">
              <span className="styles-cell__head">
                {"Type Design\n書体デザイン"}
              </span>
              <span className="styles-cell__name styles-cell__name--wide">
                {"Gen Interface JP Display\n見出し用"}
              </span>
            </div>
          </div>
        </div>

        <hr className="variations__rule" />

        <div className="variations__row">
          <span className="variations__caption">8 Weights</span>
          <div className="weights-grid">
            {WEIGHTS.map(({ sample, label, weight }) => (
              <div key={label} className="weights-grid__cell">
                <span
                  className="weights-grid__sample"
                  style={{ fontWeight: weight }}
                >
                  {sample}
                </span>
                <span className="weights-grid__label">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <hr className="variations__rule" />

        <div className="variations__row">
          <span className="variations__caption">Web Fonts</span>
          <div className="webfont__copy">
            <p>
              Web プロジェクトにおいて、head 内のスタイルシートの読み込みのみで
              Web フォントを使用できます。
              <a
                className="webfont__copy-link"
                href="https://developers.googleblog.com/google-fonts-launches-japanese-support/"
                target="_blank"
                rel="noreferrer"
              >
                Google Fonts と同様のサブセット化
              </a>
              により、単一フォントデータと比べ高速な表示を実現しています。
            </p>
            <p>
              You can use web fonts simply by loading a stylesheet within the
              head. With{" "}
              <a
                className="webfont__copy-link"
                href="https://developers.googleblog.com/google-fonts-launches-japanese-support/"
                target="_blank"
                rel="noreferrer"
              >
                subsetting similar to Google Fonts
              </a>
              , we achieve faster display than a single bundled font file.
            </p>
          </div>
        </div>

        <div className="variations__row">
          <div className="webfont__controls">
            <div className="radio-group">
              {MODES.map(({ key, label }) => (
                <button
                  key={key}
                  type="button"
                  className={`radio${mode === key ? " radio--active" : ""}`}
                  onClick={() => setMode(key)}
                >
                  <span className="radio__circle" />
                  {label}
                </button>
              ))}
            </div>
            {showSlider && (
              <Slider
                kind="weight"
                value={weight}
                min={100}
                max={800}
                step={100}
                onChange={setWeight}
                label={WEIGHT_NAMES[weight]}
                pillLabels={Object.values(WEIGHT_NAMES)}
              />
            )}
          </div>
          <pre className="webfont__code">
            <CodeLine>
              <Tok type="comment">{"<!-- index.html -->"}</Tok>
            </CodeLine>
            <CodeLine>
              <Tok type="tag-punct">{"<"}</Tok>
              <Tok type="tag-name">{"head"}</Tok>
              <Tok type="tag-punct">{">"}</Tok>
            </CodeLine>
            <LinkLine fileName={linkFile} />
            <CodeLine>
              <Tok type="tag-punct">{"</"}</Tok>
              <Tok type="tag-name">{"head"}</Tok>
              <Tok type="tag-punct">{">"}</Tok>
            </CodeLine>
            <CodeLine className="webfont__code-line--blank" />
            <CodeLine>
              <Tok type="comment">{"/* style.css */"}</Tok>
            </CodeLine>
            {showNormal && (
              <CssRule
                selector={<Tok type="selector">{"body"}</Tok>}
                family="Gen Interface JP"
                weight={mode === "all" ? 400 : weight}
                weightRange={mode === "all" ? "100–800" : undefined}
              />
            )}
            {showNormal && showDisplay && (
              <CodeLine className="webfont__code-line--blank" />
            )}
            {showDisplay && (
              <CssRule
                selector={
                  <>
                    <Tok type="selector">{"h1"}</Tok>
                    <Tok type="punct">{","}</Tok>{" "}
                    <Tok type="selector">{"h2"}</Tok>
                  </>
                }
                family="Gen Interface JP Display"
                weight={mode === "all" ? 700 : weight}
                weightRange={mode === "all" ? "100–800" : undefined}
              />
            )}
          </pre>
        </div>

        <hr className="variations__rule" />

        <div className="variations__row">
          <span className="variations__caption">Bitmap Compatibility</span>
          <div className="bitmap-sample">
            <div className="bitmap-sample__copy">
              <p>
                外部ツールを使用することで、組み込み機器向けのビットマップフォントへの変換が可能です。変換後も
                OFL ライセンスが保持されることに留意してください。
              </p>
              <p>
                External tools can convert this font into bitmap fonts for
                embedded devices. Note that the OFL license is preserved across
                such conversions.
              </p>
            </div>
            <div className="bitmap-sample__main">
              {TOOLS.map((tool) => (
                <div key={tool.name} className="bitmap-tool">
                  <a
                    href={tool.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="bitmap-tool__heading"
                  >
                    <img
                      src={`${import.meta.env.BASE_URL}bitmap/${tool.bitmapDir}/combined.png`}
                      alt={tool.name}
                      className="bitmap-tool__heading-img"
                    />
                  </a>
                  <p className="bitmap-tool__meta">{tool.meta}</p>
                  <code className="bitmap-tool__command">
                    {tool.commands.map((line, lineIdx) => (
                      <span key={lineIdx} className="bitmap-tool__cmd-line">
                        <span className="bitmap-tool__prompt">$</span>
                        <span className="bitmap-tool__cmd-text">
                          {line.map((part, i) =>
                            typeof part === "string" ? (
                              part
                            ) : (
                              <a
                                key={i}
                                href={part.href}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="bitmap-tool__cmd-link"
                              >
                                {part.text}
                              </a>
                            ),
                          )}
                        </span>
                      </span>
                    ))}
                  </code>
                </div>
              ))}
            </div>
          </div>
        </div>

        <hr className="variations__rule" />
      </div>
    </section>
  );
}
