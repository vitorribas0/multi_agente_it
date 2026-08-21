"""
Tool de geração de documentação em PDF.

O agente escreve a documentação em **Markdown**; esta tool converte para
HTML, aplica um tema bonito (capa, tipografia, tabelas e blocos de código
estilizados) e renderiza um PDF via xhtml2pdf (Python puro, sem dependências
de sistema). Salva em <BASE_DIR>/exports/ e publica como attachment
`kind: "export"` (formato "pdf") — o frontend renderiza o mesmo card de
download usado para CSV/XLSX.
"""
import json
import os
import re
import time
from pathlib import Path
from uuid import uuid4

from .registry import tool, publish_attachment


# ── Helpers ───────────────────────────────────────────────────────────

def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _exports_dir() -> Path:
    """Resolve exports/ a partir do settings.BASE_DIR ou cwd como fallback."""
    try:
        from django.conf import settings
        base = Path(settings.BASE_DIR)
    except Exception:
        base = Path(os.getcwd())
    d = base / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(stem: str) -> str:
    """Sanitiza o nome do arquivo: só [a-z0-9_-]."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem.strip())[:60]
    return stem or "documentacao"


def _strip_fences(md: str) -> str:
    """Remove uma cerca markdown externa ```markdown ... ``` que o modelo
    às vezes embrulha em volta do conteúdo inteiro."""
    md = md.strip()
    if md.startswith("```"):
        first = md.split("\n", 1)[0].lower()
        if first in ("```", "```markdown", "```md"):
            md = re.sub(r"^```[a-zA-Z]*\s*\n", "", md)
            md = re.sub(r"\n?```\s*$", "", md)
    return md.strip()


def _markdown_to_html(md: str) -> str:
    """Converte Markdown → HTML com tabelas, código e listas."""
    import markdown as _md
    return _md.markdown(
        md,
        extensions=["extra", "sane_lists", "tables", "fenced_code", "nl2br"],
        output_format="html5",
    )


# CSS do documento — adaptado ao subconjunto suportado pelo xhtml2pdf
# (sem flexbox/gradiente). Identidade visual Itaú: laranja #EC7000 e
# azul #003399. Rodapé com número de página via @frame + <pdf:pagenumber>.
ITAU_ORANGE = "#EC7000"
ITAU_BLUE = "#003399"

_PDF_CSS = """
/* Página da capa: sem margens → o azul Itaú sangra até as bordas. */
@page cover {
  size: a4 portrait;
  margin: 0;
}

/* Páginas do corpo: margens normais + rodapé com numeração */
@page {
  size: a4 portrait;
  margin: 24mm 18mm 20mm;
  @frame footer {
    -pdf-frame-content: footerContent;
    bottom: 12mm; left: 18mm; right: 18mm; height: 10mm;
  }
}

body {
  font-family: Helvetica, Arial, sans-serif;
  color: #2b2b2b;
  font-size: 11.5pt;
  line-height: 1.55;
}

/* ── Capa — bloco azul Itaú full-bleed via @page nomeada. ── */
.cover {
  page: cover;
  background-color: #003399;
  color: #ffffff;
  height: 250mm;
  page-break-after: always;
}
.cover-inner { padding: 48mm 26mm 0; }

/* Barra-marca laranja característica do Itaú, acima do título.
   Usa border (não height) para não colapsar no xhtml2pdf. */
.brandmark {
  border-top: 8mm solid #EC7000;
  width: 48mm;
  margin-bottom: 26px;
  font-size: 1pt;
}

.cover-kicker {
  text-transform: uppercase;
  font-size: 11pt;
  font-weight: bold;
  letter-spacing: 1px;
  color: #ffb066;
  margin-bottom: 14px;
}
.cover-title {
  font-size: 34pt;
  font-weight: bold;
  line-height: 1.12;
  margin: 0 0 18px;
  color: #ffffff;
}
.cover-rule {
  width: 70px; height: 5px;
  background-color: #EC7000;
  margin: 0 0 22px;
}
.cover-sub { font-size: 13pt; color: #dbe4ff; line-height: 1.5; }
.cover-foot { font-size: 10pt; color: #aebef0; margin-top: 20px; }

/* ── Corpo ── */
.doc h1, .doc h2, .doc h3, .doc h4 {
  color: #003399;
  font-weight: bold;
  margin: 1.2em 0 .4em;
}
.doc h1 {
  font-size: 21pt;
  border-bottom: 3px solid #EC7000;
  padding-bottom: 5px;
}
.doc h2 { font-size: 16pt; }
.doc h3 { font-size: 13pt; color: #EC7000; }
.doc h4 { font-size: 11.5pt; color: #003399; }
.doc p { margin: .5em 0; }
.doc a { color: #EC7000; text-decoration: none; }
.doc ul, .doc ol { margin: .5em 0 .5em 1.1em; }
.doc li { margin: .2em 0; }

.doc blockquote {
  margin: 1em 0;
  padding: .6em 1em;
  border-left: 4px solid #EC7000;
  background-color: #fff6ee;
  color: #5a4632;
}

.doc code {
  font-family: Courier, monospace;
  font-size: 9.5pt;
  background-color: #fef0e3;
  color: #b35400;
}
.doc pre {
  background-color: #002a7a;
  color: #eef3ff;
  padding: 12px 14px;
  font-family: Courier, monospace;
  font-size: 9.5pt;
}
.doc pre code { background-color: #002a7a; color: #eef3ff; }

.doc table {
  width: 100%;
  margin: 1em 0;
  font-size: 10pt;
  -pdf-keep-with-next: true;
}
.doc th, .doc td { border: 1px solid #d8dcea; padding: 6px 9px; }
.doc th { background-color: #003399; color: #ffffff; font-weight: bold; }
.doc tr:nth-child(even) td { background-color: #f4f6fc; }

.doc img { max-width: 100%; }
.doc hr { border-top: 1px solid #f0d8c2; margin: 1.4em 0; }

#footerContent { color: #8a93a8; font-size: 9pt; text-align: center; }
.footer-bar { color: #EC7000; font-weight: bold; }
"""


def _build_html(titulo: str, subtitulo: str, body_html: str) -> str:
    from html import escape
    data = time.strftime("%d/%m/%Y")
    subtitle_block = f'<div class="cover-sub">{escape(subtitulo)}</div>' if subtitulo else ""
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>{escape(titulo)}</title></head>
<body>
  <div id="footerContent">
    <span class="footer-bar">Itaú</span> &middot; Multi-Agentes Auditoria &middot; página <pdf:pagenumber> de <pdf:pagecount>
  </div>
  <div class="cover"><div class="cover-inner">
    <div class="brandmark">&nbsp;</div>
    <div class="cover-kicker">Itaú &middot; Multi-Agentes Auditoria</div>
    <div class="cover-title">{escape(titulo)}</div>
    <div class="cover-rule"></div>
    {subtitle_block}
    <div class="cover-foot">Documento gerado em {data}</div>
  </div></div>
  <div class="doc">
    {body_html}
  </div>
</body></html>"""


# ── Tool ──────────────────────────────────────────────────────────────

@tool(
    description=(
        "Gera uma DOCUMENTAÇÃO em PDF bonita (com capa, tipografia, tabelas e "
        "blocos de código estilizados) e a exibe no chat como um card de "
        "download.\n\n"
        "FLUXO: quando o usuário pedir uma documentação/relatório/manual, "
        "PRIMEIRO pergunte se ele quer o resultado em PDF. Só chame esta tool "
        "depois que ele confirmar que quer o PDF.\n\n"
        "O parâmetro `markdown` recebe o conteúdo COMPLETO da documentação em "
        "Markdown (use títulos #, ##, listas, **negrito**, tabelas e blocos de "
        "código ```). Capriche na estrutura: título, seções, sumário se fizer "
        "sentido. NÃO inclua a capa (ela é gerada automaticamente a partir do "
        "`titulo`).\n\n"
        "Não cole o link nem repita o conteúdo na resposta — o card de download "
        "aparece sozinho no chat."
    ),
    icon="📕",
)
def gerar_documentacao_pdf(
    _session: dict,
    markdown: str,
    titulo: str = "Documentação",
    subtitulo: str = "",
    nome_arquivo: str = "",
) -> str:
    """Gera um PDF a partir de documentação em Markdown e devolve download_url.

    Args:
        markdown: Conteúdo completo da documentação em Markdown (sem a capa).
        titulo: Título exibido na capa do PDF. Default: 'Documentação'.
        subtitulo: Subtítulo/descrição curta exibido na capa (opcional).
        nome_arquivo: Nome base do arquivo, sem extensão (opcional).
    """
    md = _strip_fences(markdown or "")
    if not md:
        return _err("Conteúdo vazio. Forneça a documentação em `markdown`.")

    try:
        from xhtml2pdf import pisa
    except Exception as e:  # pragma: no cover
        return _err(f"xhtml2pdf indisponível no ambiente: {e}")

    titulo = (titulo or "").strip() or "Documentação"
    subtitulo = (subtitulo or "").strip()

    try:
        body_html = _markdown_to_html(md)
        # xhtml2pdf lê o CSS embutido no <head>; injeta o tema no HTML.
        full_html = _build_html(titulo, subtitulo, body_html).replace(
            "</head>", f"<style>{_PDF_CSS}</style></head>", 1
        )
    except Exception as e:
        return _err(f"Falha ao montar o documento: {e}")

    stem = _safe_filename(nome_arquivo) if nome_arquivo else _safe_filename(titulo)
    filename = f"{stem}_{uuid4().hex[:8]}.pdf"
    path = _exports_dir() / filename

    try:
        with open(path, "wb") as fh:
            result = pisa.CreatePDF(full_html, dest=fh, encoding="utf-8")
        if result.err:
            return _err(f"Falha ao gerar o PDF: {result.err} erro(s) na renderização.")
    except Exception as e:
        return _err(f"Falha ao gerar o PDF: {e}")

    size_kb = round(path.stat().st_size / 1024, 1)
    payload = {
        "ok": True,
        "filename": filename,
        "download_url": f"/api/exports/{filename}",
        "formato": "pdf",
        "titulo": titulo,
        "size_kb": size_kb,
    }

    # Publica como attachment: o frontend renderiza o card de download
    # (mesmo componente do CSV/XLSX), fora do bloco colapsável da tool.
    publish_attachment(_session, {"kind": "export", **payload})

    return json.dumps(payload, ensure_ascii=False)
