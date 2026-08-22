"""
Tool de leitura de artefato gerado.

Fecha o ciclo da "memória externa": as tools de geração (gerar_html,
gerar_documentacao_pdf, exportar_dataset...) salvam arquivos em
<BASE_DIR>/exports/ e devolvem apenas o `download_url` — o conteúdo pesado
NÃO volta ao contexto. Quando, num turno seguinte, o agente precisa REVISAR
ou reaproveitar o que já produziu (ex.: "ajuste a seção 3 daquele HTML"),
ele usa esta tool para reabrir o conteúdo a partir do download_url.

SEGURANÇA: a leitura é restrita ao diretório exports/. O caminho é resolvido
e validado com `Path.resolve()` + verificação de que fica DENTRO de exports/,
bloqueando path traversal (`../../etc/passwd`, paths absolutos, symlinks que
escapem). Só arquivos-texto são lidos; binários (PDF/XLSX/imagem) retornam
apenas metadados, pois seu conteúdo não é útil como texto no contexto.
"""
import json
import os
import re
from pathlib import Path

from .registry import tool
from .session_artifacts import artifact_dir, conversation_id


# Extensões cujo conteúdo faz sentido devolver como texto ao modelo.
_TEXT_EXTS = {".html", ".htm", ".csv", ".txt", ".md", ".json", ".svg", ".xml"}
# Teto de tamanho devolvido ao contexto (evita reentupir a janela).
_MAX_CHARS = 60_000


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


def _resolve_artifact(nome: str, session: dict | None) -> Path | None:
    """Resolve uma referência para o artefato da conversa ou legado.

    Aceita tanto o download_url ("/api/exports/arquivo.html") quanto o
    filename puro. Bloqueia qualquer path que escape do diretório exports/.
    """
    nome = (nome or "").strip()
    local_match = re.fullmatch(r"/?api/conversations/(\d+)/artifacts/([A-Za-z0-9_-]+\.[A-Za-z0-9]+)", nome)
    if local_match:
        requested_conv, filename = int(local_match.group(1)), local_match.group(2)
        if conversation_id(session) != requested_conv:
            return None
        root = artifact_dir(session).resolve()
        candidate = (root / filename).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    # Referências antigas permanecem compatíveis com exports/.
    for prefix in ("/api/exports/", "api/exports/", "/exports/", "exports/"):
        if nome.startswith(prefix):
            nome = nome[len(prefix):]
            break
    nome = nome.lstrip("/")
    if not nome:
        return None

    exports = _exports_dir().resolve()
    candidate = (exports / nome).resolve()

    # Guard anti-path-traversal: o alvo resolvido tem que estar contido em exports/.
    try:
        candidate.relative_to(exports)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


@tool(
    description=(
        "Reabre o CONTEÚDO de um artefato que VOCÊ (ou outro agente) já gerou e "
        "salvou em exports/ — HTML, CSV, TXT, MD, JSON, SVG. USE quando precisar "
        "revisar, corrigir ou reaproveitar um arquivo produzido em um turno "
        "anterior (o histórico só te lembra do nome/URL, não do conteúdo). "
        "Passe o download_url (ex.: '/api/exports/relatorio_ab12.html') ou só o "
        "nome do arquivo. Leitura restrita a exports/ — não acessa outros "
        "diretórios. Arquivos binários (PDF/XLSX/imagem) retornam só metadados."
    ),
    icon="📂",
)
def ler_artefato(referencia: str, _session: dict) -> str:
    """Lê o conteúdo de um artefato salvo em exports/.

    Args:
        referencia: O download_url (ex.: "/api/exports/arquivo.html") ou o nome do arquivo do artefato a reabrir.
    """
    path = _resolve_artifact(referencia, _session)
    if path is None:
        return _err(
            f"Artefato não encontrado ou fora da conversa atual: {referencia!r}. "
            "Confira o download_url exatamente como foi gerado."
        )

    ext = path.suffix.lower()
    size_kb = round(path.stat().st_size / 1024, 1)

    if ext not in _TEXT_EXTS:
        return json.dumps({
            "ok": True,
            "filename": path.name,
            "formato": ext.lstrip("."),
            "size_kb": size_kb,
            "conteudo": None,
            "aviso": (
                "Arquivo binário — conteúdo não legível como texto. "
                "Referencie-o pelo download_url; não é possível reabrir o texto."
            ),
        }, ensure_ascii=False)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _err(f"Falha ao ler o artefato: {e}")

    truncated = len(content) > _MAX_CHARS
    if truncated:
        content = content[:_MAX_CHARS]

    return json.dumps({
        "ok": True,
        "filename": path.name,
        "formato": ext.lstrip("."),
        "size_kb": size_kb,
        "truncated": truncated,
        "conteudo": content,
    }, ensure_ascii=False)
