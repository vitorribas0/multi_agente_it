"""
Reorganiza os modelos do docling em `arquivos_suporte/docling/` para o
formato simples (sem cache HF, sem symlinks) que o sistema usa em
runtime via `artifacts_path`.

Suporta como entrada:
- arquivos soltos em `arquivos_suporte/docling/`
- estrutura HF antiga `models--docling-project--*/blobs/...` + snapshots
- mistura dos dois

Saída esperada:
    arquivos_suporte/docling/
    ├── docling-project--docling-layout-heron/
    │   ├── model.safetensors
    │   ├── config.json
    │   └── preprocessor_config.json
    └── docling-project--docling-models/
        ├── config.json
        └── model_artifacts/tableformer/{fast,accurate}/...

Uso:
    python scripts/setup_docling_models.py
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCLING_DIR = ROOT / "arquivos_suporte" / "docling"

LAYOUT_DIR = DOCLING_DIR / "docling-project--docling-layout-heron"
MODELS_DIR = DOCLING_DIR / "docling-project--docling-models"

# Tamanhos esperados (em bytes) — usados pra identificar arquivos por
# tamanho quando os nomes são genéricos (`config.json`, `tm_config.json`).
TABLEFORMER_FAST_SIZE = 139 * 1024 * 1024     # ~139 MB
TABLEFORMER_ACCURATE_SIZE = 203 * 1024 * 1024  # ~203 MB
LAYOUT_MODEL_SIZE = 164 * 1024 * 1024          # ~164 MB


def _approx(size: int, target: int, tol: float = 0.05) -> bool:
    return abs(size - target) <= target * tol


def _looks_like_layout_config(text: str) -> bool:
    return '"RTDetrV2ForObjectDetection"' in text or '"rt_detr_v2"' in text


def _looks_like_models_config(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    keys = set(data.keys())
    return keys == {"_name_or_path"} or "docling-models" in text


def _looks_like_preprocessor_config(text: str) -> bool:
    return '"image_processor_type"' in text or '"do_resize"' in text


def _looks_like_tm_config(text: str) -> bool:
    return '"PubTabNet' in text or '"tableformer"' in text.lower()


def _all_files(p: Path):
    if not p.exists():
        return
    for f in p.rglob("*"):
        if f.is_file():
            yield f


def _classify(file: Path) -> str | None:
    """
    Retorna o destino canônico de um arquivo, ou None se não reconhecido.
    Destinos possíveis:
        layout/model.safetensors
        layout/config.json
        layout/preprocessor_config.json
        layout/README.md
        models/config.json
        models/README.md
        tf_fast/safetensors
        tf_fast/tm_config.json
        tf_accurate/safetensors
        tf_accurate/tm_config.json
    """
    name = file.name.lower()
    size = file.stat().st_size

    # Modelos grandes — identifica por tamanho (nome pode ser hash de blob)
    if file.suffix == ".safetensors" or size > 50 * 1024 * 1024:
        if _approx(size, LAYOUT_MODEL_SIZE):
            return "layout/model.safetensors"
        if _approx(size, TABLEFORMER_FAST_SIZE):
            return "tf_fast/tableformer_fast.safetensors"
        if _approx(size, TABLEFORMER_ACCURATE_SIZE):
            return "tf_accurate/tableformer_accurate.safetensors"

    # JSONs e textos pequenos — abre e classifica pelo conteúdo
    if size <= 1 * 1024 * 1024:
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        if _looks_like_layout_config(text):
            return "layout/config.json"
        if _looks_like_models_config(text):
            return "models/config.json"
        if _looks_like_preprocessor_config(text):
            return "layout/preprocessor_config.json"
        if _looks_like_tm_config(text):
            # Direciona pela pasta de origem se possível
            parent_str = str(file.parent).lower()
            if "accurate" in parent_str:
                return "tf_accurate/tm_config.json"
            if "fast" in parent_str:
                return "tf_fast/tm_config.json"
            # Sem dica: assume fast se ainda não foi colocado
            return "tf_fast/tm_config.json"

    return None


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return
    shutil.copy2(src, dst)


def main() -> int:
    if not DOCLING_DIR.exists():
        print(f"❌ Pasta não existe: {DOCLING_DIR}", file=sys.stderr)
        return 1

    # Coleta todos os arquivos sob arquivos_suporte/docling/
    candidates = list(_all_files(DOCLING_DIR))
    if not candidates:
        print(f"❌ Nenhum arquivo em {DOCLING_DIR}", file=sys.stderr)
        return 1

    # Mapa: destino canônico -> arquivo escolhido
    chosen: dict[str, Path] = {}
    placed: list[tuple[str, Path]] = []
    skipped: list[Path] = []

    for f in candidates:
        # Pula arquivos já no destino correto (idempotência)
        try:
            f.relative_to(LAYOUT_DIR)
            continue
        except ValueError:
            pass
        try:
            f.relative_to(MODELS_DIR)
            continue
        except ValueError:
            pass

        kind = _classify(f)
        if kind is None:
            skipped.append(f)
            continue

        # Resolve disputas: prefere arquivo numa subpasta com nome dica
        if kind in chosen:
            current = chosen[kind]
            score_new = _path_hint_score(f, kind)
            score_cur = _path_hint_score(current, kind)
            if score_new <= score_cur:
                continue
        chosen[kind] = f

    target_paths = {
        "layout/model.safetensors":          LAYOUT_DIR / "model.safetensors",
        "layout/config.json":                LAYOUT_DIR / "config.json",
        "layout/preprocessor_config.json":   LAYOUT_DIR / "preprocessor_config.json",
        "models/config.json":                MODELS_DIR / "config.json",
        "tf_fast/tableformer_fast.safetensors":
            MODELS_DIR / "model_artifacts" / "tableformer" / "fast" / "tableformer_fast.safetensors",
        "tf_fast/tm_config.json":
            MODELS_DIR / "model_artifacts" / "tableformer" / "fast" / "tm_config.json",
        "tf_accurate/tableformer_accurate.safetensors":
            MODELS_DIR / "model_artifacts" / "tableformer" / "accurate" / "tableformer_accurate.safetensors",
        "tf_accurate/tm_config.json":
            MODELS_DIR / "model_artifacts" / "tableformer" / "accurate" / "tm_config.json",
    }

    for kind, src in chosen.items():
        dst = target_paths[kind]
        _copy(src, dst)
        placed.append((kind, dst))

    # Validação: todos os obrigatórios estão presentes?
    required = [
        "layout/model.safetensors",
        "layout/config.json",
        "tf_fast/tableformer_fast.safetensors",
        "tf_fast/tm_config.json",
    ]
    missing = [k for k in required if not target_paths[k].exists()]

    print("\n=== Resultado ===")
    print(f"Arquivos analisados:       {len(candidates)}")
    print(f"Arquivos posicionados:     {len(placed)}")
    print(f"Arquivos não reconhecidos: {len(skipped)}")

    if placed:
        print("\nPosicionados:")
        for kind, dst in placed:
            rel = dst.relative_to(DOCLING_DIR)
            print(f"  ✓ {kind:48s} -> {rel}")

    if missing:
        print("\n❌ FALTAM arquivos obrigatórios:")
        for k in missing:
            print(f"  - {k}")
        print("\nBaixe os arquivos faltantes e rode o script de novo.")
        return 1

    print("\n✓ Estrutura mínima OK — pode subir o servidor.")

    # Limpa pasta de cache HF antigo, se existir
    for old in DOCLING_DIR.glob("models--docling-project--*"):
        if old.is_dir():
            print(f"\nRemovendo cache HF antigo: {old.relative_to(DOCLING_DIR)}")
            shutil.rmtree(old, ignore_errors=True)

    # Remove arquivos soltos na raiz que já foram copiados
    for f in DOCLING_DIR.iterdir():
        if f.is_file():
            print(f"Removendo arquivo solto na raiz: {f.name}")
            f.unlink()

    return 0


def _path_hint_score(file: Path, kind: str) -> int:
    """Maior = mais provável de ser o arquivo certo."""
    s = str(file).lower()
    score = 0
    if kind.startswith("tf_accurate") and "accurate" in s:
        score += 10
    if kind.startswith("tf_fast") and "fast" in s:
        score += 10
    # Arquivos com nome canônico ganham bônus
    if file.name in {"model.safetensors", "config.json", "preprocessor_config.json", "tm_config.json"}:
        score += 5
    if file.name.startswith("tableformer_"):
        score += 5
    return score


if __name__ == "__main__":
    raise SystemExit(main())
