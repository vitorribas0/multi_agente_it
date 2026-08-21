"""Tool de consulta a bases no AWS Athena (genérica — qualquer database)."""
import json
import os
from .registry import tool, publish_attachment

# Database default (base FQ de reclamações) — mantido p/ retrocompat: quem
# chamar sem `database` continua caindo na FQ. O workgroup é fixo (todas as
# bases consultadas usam o mesmo).
_DEFAULT_DATABASE = "database_rt2"
_WORKGROUP = "analytics-workgroup-v3"

# Diretório base do projeto (tools/ está dentro do projeto)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup_aws_env() -> None:
    """Configura proxy e CA bundle do Itaú no ambiente p/ chamar a AWS.

    Reaproveitado por todas as tools que falam com o Athena/Glue.
    """
    from pathlib import Path
    
    # Do not force corporate proxy. If a proxy is required in your
    # environment, set `HTTP_PROXY`/`HTTPS_PROXY` externally.

    if "AWS_REGION" not in os.environ and "AWS_DEFAULT_REGION" not in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = os.getenv("AWS_DEFAULT_REGION", "sa-east-1")

    # Buscar CA bundle do Itaú (SEMPRE, sobrescrevendo certifi)
    # Ordem de prioridade: arquivos_suporte > ~/.aws > linux
    home = Path.home()
    base_dir = Path(_BASE_DIR)
    ca_paths = [
        base_dir / "arquivos_suporte" / "cacert.pem",  # PRIORIDADE: local do projeto
        home / ".aws" / "cacert.pem",  # Windows + Linux: CA Itaú
        home / ".aws" / "cacert-987979f15e8bd2c573161b23c2885fda.crt",  # Windows: CA alternativo
        Path("/etc/ssl/certs/ca_bundle.pem"),  # Linux: fallback sistema
    ]
    
    ca_bundle = None
    for ca_path in ca_paths:
        if ca_path.exists():
            ca_bundle = str(ca_path)
            break
    
    # FORÇA o CA do Itaú (sobrescreve certifi ou qualquer outra configuração)
    if ca_bundle:
        os.environ["AWS_CA_BUNDLE"] = ca_bundle
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["CURL_CA_BUNDLE"] = ca_bundle
        # Também configura para botocore
        os.environ["BOTOCORE_CA_BUNDLE"] = ca_bundle
        # SSL_CERT_FILE é usado pelo Python ssl module (proxy handshake)
        os.environ["SSL_CERT_FILE"] = ca_bundle


@tool(
    description=(
        "Executa uma query SQL (somente SELECT) em uma base no AWS Athena e "
        "salva o resultado como o dataset corrente da sessão.\n\n"
        "USE quando: precisar extrair, contar ou agrupar registros de uma "
        "base do Athena. ANTES de filtrar/consultar uma base que você não "
        "conhece, chame `descrever_tabela` p/ ver o schema (colunas+tipos) e "
        "um preview — não chute nomes de coluna. NÃO use para análise sobre "
        "dataset já em sessão (use as tools de análise nesse caso).\n\n"
        "Por padrão consulta a base FQ de reclamações "
        "(`database_rt2.RT2_AI6_OCORRENCIA_FQ_001`: idassuntoocorrido, "
        "documento, anomesdia [YYYYMMDD string], nomeassunto, nometipoassunto, "
        "descricao, relato, tipopessoa [PF/PJ]). Para OUTRA base, informe "
        "`database` e qualifique a tabela na query (ex.: FROM outro_db.tabela).\n\n"
        "Apenas SELECT — DELETE/DROP/UPDATE/INSERT são proibidos. O resultado "
        "fica em _session['athena_last_result'] (lista de dicts) e "
        "_session['athena_last_columns'], permitindo que tools e sub-agentes "
        "posteriores trabalhem sobre ele sem reexecutar a query."
    ),
    icon="🗄️",
    name="Consulta AWS",
)
def consulta_aws(
    query_sql: str,
    _session: dict,
    database: str = _DEFAULT_DATABASE,
    limit: int = None,
) -> str:
    """Consulta uma base no Athena e salva o resultado como dataset da sessão.

    Args:
        query_sql: Query SQL completa (somente SELECT). Datas em colunas tipo anomesdia costumam ser strings 'YYYYMMDD' — use LIKE '202605%' ou BETWEEN '20260501' AND '20260531'.
        database: Database do Athena a consultar. Default 'database_rt2' (base FQ de reclamações). Para outra base, passe o nome do database aqui.
        limit: Quantidade de linhas. Omitir = 20 (preview seguro). Use -1 para retornar TODAS as linhas (extração completa, peça confirmação ao usuário antes).
    """
    import awswrangler as wr
    import boto3
    import urllib3
    import botocore.client
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _setup_aws_env()

    # If a corporate proxy is present in the environment, AWS SSL interception
    # may break certificate validation. Only apply the botocore monkey-patch
    # when an explicit proxy is configured (preserve secure defaults otherwise).
    if (os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("CORP_PROXY")) \
       and not getattr(botocore.client.ClientCreator, '_verify_patched', False):
        _orig_create_client = botocore.client.ClientCreator.create_client
        def _patched_create_client(self, service_name, region_name, is_secure=True,
                                     endpoint_url=None, verify=None, credentials=None,
                                     scoped_config=None, api_version=None,
                                     client_config=None, auth_token=None):
            return _orig_create_client(
                self, service_name, region_name, is_secure=is_secure,
                endpoint_url=endpoint_url, verify=False, credentials=credentials,
                scoped_config=scoped_config, api_version=api_version,
                client_config=client_config, auth_token=auth_token,
            )
        botocore.client.ClientCreator.create_client = _patched_create_client
        botocore.client.ClientCreator._verify_patched = True

    try:
        lowered = query_sql.lower()
        final_query = query_sql

        if "limit" not in lowered:
            if limit is None:
                final_query = query_sql.rstrip(";") + " LIMIT 20;"
            elif limit == -1:
                final_query = query_sql
            else:
                final_query = query_sql.rstrip(";") + f" LIMIT {limit};"

        df = wr.athena.read_sql_query(
            sql=final_query,
            database=database or _DEFAULT_DATABASE,
            ctas_approach=False,
            workgroup=_WORKGROUP,
        )

        if df.empty:
            return json.dumps({"aviso": "A consulta não retornou registros."}, ensure_ascii=False)

        # Serialização JSON-safe (NaN→null, datas→ISO) — necessária pro
        # SQLite (CHECK constraint do JSONField rejeita `NaN` literal) e pro
        # frontend.
        records = json.loads(
            df.to_json(orient="records", date_format="iso", default_handler=str)
        )
        preview_rows = json.loads(
            df.head(100).to_json(orient="records", date_format="iso", default_handler=str)
        )

        # Salva o dataset anterior como nomeado (se existia) para não perder
        prev_source = _session.get("athena_last_source") or {}
        prev_records = _session.get("athena_last_result")
        if prev_records and prev_source:
            prev_name = prev_source.get("filename") or prev_source.get("query", "")[:40] or "anterior"
            prev_name = prev_name.replace(" ", "_").replace("/", "_")[:60]
            if "named_datasets" not in _session:
                _session["named_datasets"] = {}
            _session["named_datasets"][prev_name] = prev_records

        # Salva no estado de sessão para outras tools usarem
        _session["athena_last_result"] = records
        _session["athena_last_columns"] = list(df.columns)
        _session["athena_last_source"] = {
            "kind": "athena", "database": database or _DEFAULT_DATABASE,
            "query": final_query,
        }

        # Anexa um card-tabela à mensagem do assistente — o run_agent/view
        # drena estes cards via __pending_attachments.
        publish_attachment(_session, {
            "kind": "table",
            "filename": "Resultado da consulta Athena",
            "rows": int(len(df)),
            "columns": list(df.columns),
            "dtypes": {c: str(df[c].dtype) for c in df.columns},
            "preview": preview_rows,
            "preview_rows": len(preview_rows),
            "truncated": False,
        })

        return json.dumps(
            {
                "total_registros": len(df),
                "colunas": list(df.columns),
                "preview_3_linhas": df.head(3).to_dict(orient="records"),
                "nota": (
                    f"{len(df)} registros salvos em _session['athena_last_result']. "
                    "Outras tools podem ler esses dados."
                ),
            },
            ensure_ascii=False,
            default=str,
        )

    except Exception as e:
        return json.dumps({"erro": f"Erro ao consultar a base no Athena: {str(e)}"}, ensure_ascii=False)
