"""
gerar_pdf.py — converte docs/relatorio_evolucao.md em docs/relatorio_evolucao.pdf.

O enunciado pede o relatorio de evolucao em PDF na pasta docs/. Este script gera
o PDF a partir do .md (que e a fonte editavel), usando fpdf2 + markdown, sem
depender de pandoc/LaTeX.

    python docs/gerar_pdf.py
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown
from fpdf import FPDF

_DOCS = Path(__file__).resolve().parent
MD = _DOCS / "relatorio_evolucao.md"
PDF = _DOCS / "relatorio_evolucao.pdf"

# a fonte embutida do fpdf2 (Helvetica) so cobre latin-1; o .md usa ->, <=, etc.
_TRANSLIT = {
    "→": "->", "←": "<-", "≤": "<=", "≥": ">=",
    "–": "-", "—": "--", "‑": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "×": "x", "•": "-",
    " ": " ", " ": " ", " ": " ", "≈": "~", "−": "-",
    "✅": "[ok]", "❌": "[x]", "⚠": "[!]", "️": "",
}


def _translit(texto: str) -> str:
    for a, b in _TRANSLIT.items():
        texto = texto.replace(a, b)
    return texto.encode("latin-1", "replace").decode("latin-1")


def _md_para_html(texto: str) -> str:
    # tira comentarios HTML do .md (marcadores "colar aqui")
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.DOTALL)
    texto = _translit(texto)
    html = markdown.markdown(texto, extensions=["tables", "fenced_code", "sane_lists"])
    # write_html do fpdf2 nao aceita <code>/<strong>/<em> aninhados em <td> — tira as
    # tags inline (mantem o texto). Tabela com borda ajuda a leitura.
    html = re.sub(r"</?(code|strong|em|b|i)(\s[^>]*)?>", "", html)
    html = html.replace("<table>", '<table border="1" cellpadding="3">')
    return html


def main() -> None:
    html = _md_para_html(MD.read_text(encoding="utf-8"))

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.write_html(html, table_line_separators=True)
    pdf.output(str(PDF))
    print(f"gerado: {PDF}  ({PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
