"""
Tool de geração de página HTML.

O agente escreve o HTML COMPLETO (documento standalone, com CSS embutido)
de uma apresentação/relatório/documentação. Esta tool valida, salva em
<BASE_DIR>/exports/ e publica como attachment `kind: "export"`
(formato "html") — o frontend renderiza o mesmo card de download usado
para CSV/XLSX/PDF.
"""
import json
import re
from uuid import uuid4

from .registry import tool, publish_attachment
from .session_artifacts import artifact_dir, artifact_download_url


# ── Helpers ───────────────────────────────────────────────────────────

def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _safe_filename(stem: str) -> str:
    """Sanitiza o nome do arquivo: só [a-z0-9_-]."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem.strip())[:60]
    return stem or "apresentacao"


def _strip_fences(html: str) -> str:
    """Remove uma cerca markdown externa ```html ... ``` que o modelo às
    vezes embrulha em volta do documento inteiro."""
    html = html.strip()
    if html.startswith("```"):
        first = html.split("\n", 1)[0].lower()
        if first in ("```", "```html", "```htm"):
            html = re.sub(r"^```[a-zA-Z]*\s*\n", "", html)
            html = re.sub(r"\n?```\s*$", "", html)
    return html.strip()


def _ensure_document(html: str, titulo: str) -> str:
    """Garante que o HTML seja um documento standalone. Se o modelo mandar
    só um fragmento (sem <html>/<body>), embrulha num shell mínimo com o
    título e um viewport responsivo — sem impor tema (o layout é do agente)."""
    from html import escape
    low = html.lower()
    if "<html" in low:
        return html
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(titulo)}</title>
</head>
<body>
{html}
</body>
</html>"""


# ── Papel timbrado Itaú (injetado via código, garantido em toda página) ──

# Identidade Itaú — mesma paleta do gerador de PDF (gerar_documentacao_pdf.py).
ITAU_ORANGE = "#EC7000"
ITAU_BLUE = "#003399"


def _inject_letterhead(html: str, titulo: str) -> str:
    """Injeta o papel timbrado Itaú no documento, por código.

    Garante marca em TODA página gerada, independente do design do agente:
    uma barra-marca fixa no topo (faixa laranja + selo 'itaú') e um rodapé
    fixo com a assinatura institucional. O CSS é escopado em ``itau-lh-*``
    para não colidir com os estilos do agente, e usa ``position:fixed`` para
    não empurrar o layout. Idempotente: não injeta se a marca já existe.
    """
    from html import escape

    low = html.lower()
    # já timbrado (reprocessamento) — não duplica
    if "itau-lh-bar" in low:
        return html

    titulo_safe = escape((titulo or "").strip())
    css = (
        "<style id=\"itau-lh-style\">"
        ":root{--itau-lh-orange:" + ITAU_ORANGE + ";--itau-lh-blue:" + ITAU_BLUE + ";}"
        "body{padding-top:52px !important;}"
        ".itau-lh-bar{position:fixed;top:0;left:0;right:0;height:52px;z-index:2147483000;"
        "display:flex;align-items:center;gap:14px;padding:0 22px;box-sizing:border-box;"
        "background:linear-gradient(90deg,#001a4d 0%," + ITAU_BLUE + " 60%,#0a2a7a 100%);"
        "border-bottom:3px solid " + ITAU_ORANGE + ";"
        "font-family:system-ui,'Segoe UI',Arial,sans-serif;color:#fff;"
        "box-shadow:0 4px 18px rgba(0,0,0,.25);}"
        ".itau-lh-badge{display:inline-grid;place-items:center;height:30px;padding:0 12px;"
        "border-radius:9px;background:linear-gradient(135deg," + ITAU_ORANGE + ",#ff9d00);"
        "color:#fff;font-weight:900;font-size:14px;letter-spacing:.3px;"
        "box-shadow:0 6px 16px rgba(236,112,0,.35);}"
        ".itau-lh-title{font-size:13px;font-weight:700;color:#eaf0ff;letter-spacing:.2px;"
        "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}"
        ".itau-lh-kicker{margin-left:auto;font-size:10px;font-weight:800;text-transform:uppercase;"
        "letter-spacing:1.6px;color:#ffb066;white-space:nowrap;}"
        ".itau-lh-footer{position:fixed;bottom:0;left:0;right:0;height:26px;z-index:2147483000;"
        "display:flex;align-items:center;justify-content:center;gap:6px;"
        "background:rgba(0,26,77,.94);border-top:2px solid " + ITAU_ORANGE + ";"
        "font-family:system-ui,'Segoe UI',Arial,sans-serif;font-size:10px;color:#c9d4ef;}"
        ".itau-lh-footer b{color:" + ITAU_ORANGE + ";font-weight:800;}"
        "body{padding-bottom:34px !important;}"
        "@media print{.itau-lh-bar,.itau-lh-footer{position:fixed;}}"
        "</style>"
    )
    bar = (
        "<div class=\"itau-lh-bar\">"
        "<span class=\"itau-lh-badge\">itaú</span>"
        + (f"<span class=\"itau-lh-title\">{titulo_safe}</span>" if titulo_safe else "")
        + "<span class=\"itau-lh-kicker\">Multi-Agentes Auditoria</span>"
        "</div>"
    )
    footer = (
        "<div class=\"itau-lh-footer\">"
        "<b>Itaú</b>&nbsp;·&nbsp;Multi-Agentes Auditoria&nbsp;·&nbsp;Documento interno"
        "</div>"
    )

    # CSS antes de </head> (ou no início se não houver head); marca dentro do <body>.
    if "</head>" in html:
        html = html.replace("</head>", css + "</head>", 1)
    else:
        html = css + html

    # abre a marca logo após <body ...>
    m = re.search(r"<body[^>]*>", html, flags=re.IGNORECASE)
    if m:
        html = html[: m.end()] + bar + html[m.end():]
    else:
        html = bar + html

    # rodapé antes de </body>
    if "</body>" in html.lower():
        idx = html.lower().rindex("</body>")
        html = html[:idx] + footer + html[idx:]
    else:
        html = html + footer

    return html


# ── Tool ──────────────────────────────────────────────────────────────

@tool(
    description=(
        "Gera uma PÁGINA HTML standalone (apresentação, relatório ou "
        "dashboard com KPIs, gráficos e tabelas) e a exibe no chat como um "
        "card: o usuário pode VISUALIZAR (abre renderizada, sem baixar) ou "
        "BAIXAR o arquivo.\n\n"
        "USE: quando o usuário pedir uma apresentação/relatório/dashboard em "
        "HTML, ou quiser um visual mais rico que um PDF.\n\n"
        "O parâmetro `html` recebe o DOCUMENTO HTML COMPLETO e autossuficiente: "
        "`<!DOCTYPE html>`, `<head>` com `<meta charset>` e um `<style>` "
        "embutido. Use os NÚMEROS REAIS da análise (nunca invente).\n\n"
        "REGRA CRÍTICA — TUDO EMBUTIDO, ZERO RECURSO EXTERNO: não use nenhuma "
        "URL externa (sem <link>/<script src> de CDN, sem @import, sem fontes "
        "ou imagens por http). O arquivo é aberto isolado/offline; recurso "
        "externo NÃO carrega. Faça os GRÁFICOS à mão como SVG inline (barras, "
        "linhas, pizza, donut) ou com <canvas>+JavaScript embutido no próprio "
        "<script>. Fontes: use apenas as do sistema "
        "(font-family: system-ui, Segoe UI, Arial, sans-serif).\n\n"
        "QUALIDADE VISUAL — mire em 'editorial premium / estúdio de design', "
        "NUNCA em 'template gerado por IA'. ADAPTE o formato ao conteúdo (uma "
        "página de relatório, um dashboard, uma apresentação em seções) — não "
        "existe um layout único obrigatório. O que deve estar SEMPRE presente é "
        "o alto padrão estético.\n\n"
        "PAPEL TIMBRADO (automático): a ferramenta INJETA por código uma barra-marca "
        "Itaú fixa no TOPO (52px de altura, azul com selo 'itaú') e um RODAPÉ fixo "
        "(26px). NÃO desenhe outro cabeçalho de marca 'itaú' no topo (evita "
        "duplicar) e deixe respiro no início e no fim do conteúdo. Você pode e deve "
        "ter títulos/seções próprios — só não repita o selo institucional.\n\n"
        "Repertório de recursos para elevar o visual (use os que servirem ao "
        "conteúdo, não todos de uma vez):\n"
        "• PALETA (defina como CSS custom properties em :root e reutilize): "
        "laranja Itaú #EC7000 (ou #FF6B00) + âmbar #FFB000 como destaque/CTA; "
        "azul #003399/#38BDF8 como apoio. Pode ser tema claro elegante (#F5F6FA) "
        "OU dark sofisticado — escolha o que combina com o conteúdo. Use a cor "
        "de destaque com PARCIMÔNIA (acentos, barras, números-chave).\n"
        "• PROFUNDIDADE (especialmente no dark): fundo com GRADIENTES RADIAIS "
        "suaves em cantos diferentes (laranja quente + azul/violeta bem sutis) "
        "sobre um gradiente de base; opcionalmente uma grade fina (linhas a "
        "~2-3% de opacidade) com máscara radial que desvanece nas bordas.\n"
        "• CARDS DE VIDRO (glassmorphism): fundo semitransparente com leve "
        "gradiente, borda clara 1px, border-radius generoso (16–28px), "
        "backdrop-filter: blur(...) e sombra suave/profunda conforme o tema.\n"
        "• TIPOGRAFIA COM PRESENÇA: títulos fortes (font-weight 700–900, "
        "letter-spacing levemente negativo, clamp() para escalar), kicker/eyebrow "
        "em caixa-alta pequena com tracking largo. KPIs com números grandes; "
        "gradient-text (background-clip:text) é um toque bonito nos destaques.\n"
        "• COMPONENTES bem-acabados: grid de cards de KPI (número grande + rótulo "
        "+ contexto), barras de ranking com trilho arredondado e preenchimento em "
        "gradiente, tabelas com linhas espaçadas e cantos arredondados, cabeçalho "
        "com título+subtítulo e um selo/marca 'itaú'.\n"
        "• Espaçamento generoso e consistente, contraste acessível, e RESPONSIVO "
        "(grids que colapsam para 1 coluna em telas menores).\n"
        "• O essencial: entregue algo elegante e caprichado. Elementos como "
        "profundidade, vidro, gradiente sutil e hierarquia tipográfica forte são "
        "o que separam um resultado bonito de um chapado e genérico — mas o "
        "formato e a intensidade seguem o conteúdo e o pedido do usuário.\n\n"
        "Não cole o HTML nem repita o conteúdo na resposta — o card aparece "
        "sozinho no chat."
    ),
    icon="🌐",
)
def gerar_html(
    _session: dict,
    html: str,
    titulo: str = "Apresentação",
    nome_arquivo: str = "",
) -> str:
    """Gera uma página HTML standalone e devolve o download_url.

    Args:
        html: Documento HTML completo e autossuficiente (com <style> embutido, sem recursos externos).
        titulo: Título da página (usado no <title> se faltar e no nome do arquivo). Default: 'Apresentação'.
        nome_arquivo: Nome base do arquivo, sem extensão (opcional).
    """
    content = _strip_fences(html or "")
    if not content:
        return _err("Conteúdo vazio. Forneça o documento em `html`.")

    titulo = (titulo or "").strip() or "Apresentação"
    content = _ensure_document(content, titulo)
    # Papel timbrado Itaú garantido por código (marca no topo + rodapé fixo).
    content = _inject_letterhead(content, titulo)

    stem = _safe_filename(nome_arquivo) if nome_arquivo else _safe_filename(titulo)
    filename = f"{stem}_{uuid4().hex[:8]}.html"
    path = artifact_dir(_session) / filename

    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        return _err(f"Falha ao salvar o HTML: {e}")

    size_kb = round(path.stat().st_size / 1024, 1)
    payload = {
        "ok": True,
        "filename": filename,
        "download_url": artifact_download_url(_session, filename),
        "formato": "html",
        "titulo": titulo,
        "size_kb": size_kb,
    }

    # Publica como attachment: o frontend renderiza o card de download
    # (mesmo componente do CSV/XLSX/PDF), fora do bloco colapsável da tool.
    publish_attachment(_session, {"kind": "export", **payload})

    return json.dumps(payload, ensure_ascii=False)
