import { Download } from "./Hero";
import { DOWNLOAD_URL } from "../config";

export function Footer() {
  return (
    <footer className="footer">
      <div className="footer__rules" aria-hidden="true">
        <hr className="footer__rule--1" />
        <hr className="footer__rule--2" />
        <hr className="footer__rule--3" />
        <hr className="footer__rule--4" />
      </div>

      <div className="footer__inner">
        <div className="footer__top">
          <a
            className="hero__download"
            href={DOWNLOAD_URL}
            style={{ position: "static" }}
          >
            <Download />
          </a>

          <div className="footer__credits">
            <span className="footer__credit-label footer__credit-row-1-label">
              Noto Sans:
            </span>
            <span className="footer__credit-value footer__credit-row-1-value">
              {
                "Ryoko Nishizuka, Paul D. Hunt, Sandoll Communications, Soo-young Jang, Joo-yeon Kang, Dr. Ken Lunde, Masataka Hattori"
              }
            </span>
            <span className="footer__credit-label footer__credit-row-2-label">
              Inter:
            </span>
            <span className="footer__credit-value footer__credit-row-2-value">
              Rasmus Andersson
            </span>
            <span className="footer__credit-label footer__credit-row-3-label">
              Gen Interface JP:
            </span>
            <span className="footer__credit-value footer__credit-row-3-value">
              Composed by{" "}
              <a
                className="footer__credit-link"
                href="https://yamatoiizuka.com"
                target="_blank"
                rel="noreferrer"
              >
                Yamato Iizuka
              </a>{" "}
              with{" "}
              <a
                className="footer__credit-link"
                href="https://github.com/yamatoiizuka/ofl-font-baker"
                target="_blank"
                rel="noreferrer"
              >
                OFL Font Baker
              </a>
            </span>
            <span className="footer__credit-label footer__credit-row-4-label">
              GitHub:
            </span>
            <span className="footer__credit-value footer__credit-row-4-value">
              <a
                className="footer__credit-link"
                href="https://github.com/yamatoiizuka/gen-interface-jp"
                target="_blank"
                rel="noreferrer"
              >
                yamatoiizuka/gen-interface-jp
              </a>
            </span>
            <span className="footer__credit-label footer__coffee-label">
              Buy Me a Coffee:
            </span>
            <span className="footer__credit-value footer__coffee-value">
              <a
                className="footer__coffee-link"
                href="https://buymeacoffee.com/yamatoiizuka"
                target="_blank"
                rel="noreferrer"
                aria-label="Buy Me a Coffee"
              >
                ☕️
              </a>
            </span>
          </div>
        </div>

        <p className="license">
          This Font Software is licensed under <br className="sp-none" />
          the SIL Open Font License, Version 1.1. <br className="sp-none" />
          This license is available with a FAQ at:{" "}
          <a
            className="license__link"
            href="https://openfontlicense.org"
            target="_blank"
            rel="noreferrer"
          >
            https://openfontlicense.org
          </a>
        </p>
      </div>
    </footer>
  );
}
