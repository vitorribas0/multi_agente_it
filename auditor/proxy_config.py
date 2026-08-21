"""
Configuração de rede para o docling/HuggingFace na rede do Itaú.

Estratégia em camadas, escolhida pela presença dos arquivos no projeto:

1. Se `arquivos_suporte/docling/` contém os modelos baixados manualmente
   (estrutura HF: `models--docling-project--*/snapshots/<commit>/...`),
   apontamos `HF_HOME` para essa pasta e ligamos `HF_HUB_OFFLINE=1`.
   Resultado: docling não faz NENHUM request à internet.

2. Caso contrário, e se `CORP_PROXY` estiver definido no .env, exportamos
   HTTP_PROXY/HTTPS_PROXY para que o docling consiga baixar os modelos
   pelo proxy corporativo.

3. Se nada for configurado (ex: SageMaker), deixamos como está.

Uso: chamar `apply_network_env(BASE_DIR)` no startup (settings.py).
"""
import os
from pathlib import Path


def docling_artifacts_path(base_dir: Path) -> Path | None:
    """
    Retorna o diretório `arquivos_suporte/docling/` se ele tiver a
    estrutura esperada pelo docling (artifacts_path direto), ou None.

    Estrutura esperada (sem cache HF, sem symlinks — portátil
    Linux/Windows):
        arquivos_suporte/docling/
        ├── docling-project--docling-layout-heron/
        │   ├── model.safetensors
        │   ├── config.json
        │   └── preprocessor_config.json
        └── docling-project--docling-models/
            ├── config.json
            └── model_artifacts/tableformer/{fast,accurate}/...
    """
    p = Path(base_dir) / "arquivos_suporte" / "docling"
    expected = [
        p / "docling-project--docling-layout-heron" / "model.safetensors",
        p / "docling-project--docling-models" / "model_artifacts"
          / "tableformer" / "fast" / "tableformer_fast.safetensors",
    ]
    return p if all(f.exists() for f in expected) else None


def apply_network_env(base_dir: Path) -> None:
    """
    Configura cache offline do HuggingFace ou proxy corporativo.

    Prioriza cache offline: se os modelos foram baixados manualmente
    para `arquivos_suporte/docling/`, desliga requests à internet —
    o docling será apontado para a pasta local em runtime via
    `artifacts_path` (ver auditor.views._build_docling_converter).
    """
    # SEMPRE configura CA bundle do Itaú (necessário para validar SSL)
    _setup_ca_bundle(base_dir)
    
    if docling_artifacts_path(base_dir) is not None:
        # Sem cache HF, mas precisamos garantir que o huggingface_hub não
        # tente buscar revisão online quando algum modelo extra for usado.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        return

    # Sem cache local — não alteramos proxies por padrão. Se o deploy
    # requer proxy corporativo, configure `HTTP_PROXY`/`HTTPS_PROXY`
    # explicitamente no ambiente em vez de usar `CORP_PROXY`.
    return


def _setup_ca_bundle(base_dir: Path) -> None:
    """
    Encontra e configura o CA bundle do Itaú para validação SSL.
    
    Ordem de busca:
    1. projeto/arquivos_suporte/cacert.pem (preferência local)
    2. ~/.aws/cacert.pem (CA Itaú oficial)
    3. ~/.aws/cacert-*.crt (CA alternativo)
    4. /etc/ssl/certs/ca_bundle.pem (Linux)
    """
    home = Path.home()
    ca_paths = [
        base_dir / "arquivos_suporte" / "cacert.pem",  # PRIORIDADE: local do projeto
        home / ".aws" / "cacert.pem",
        home / ".aws" / "cacert-987979f15e8bd2c573161b23c2885fda.crt",
        Path("/etc/ssl/certs/ca_bundle.pem"),
    ]
    
    ca_bundle = None
    for ca_path in ca_paths:
        if ca_path.exists():
            ca_bundle = str(ca_path)
            break
    
    if ca_bundle:
        # FORÇA o uso do CA Itaú (sobrescreve certifi)
        os.environ["AWS_CA_BUNDLE"] = ca_bundle
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["CURL_CA_BUNDLE"] = ca_bundle
        os.environ["BOTOCORE_CA_BUNDLE"] = ca_bundle
        # SSL_CERT_FILE é usado pelo Python ssl module (proxy handshake)
        os.environ["SSL_CERT_FILE"] = ca_bundle



# Mantém o nome antigo como alias para retrocompatibilidade.
def apply_proxy_env() -> None:
    apply_network_env(Path(__file__).resolve().parent.parent)
