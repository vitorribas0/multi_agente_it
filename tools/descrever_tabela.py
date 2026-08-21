"""Tool de inspeção de uma tabela no AWS Athena/Glue: schema + preview.

Antes de montar filtros/queries sobre uma base que o agente não conhece,
esta tool devolve o ESQUEMA (colunas + tipos, via catálogo Glue) e um
PREVIEW de 3 linhas (SELECT * ... LIMIT 3). É o "entender os dados" — o
passo anterior a `consulta_aws`.

Diferente de `descrever_dataset` (que descreve o dataset JÁ carregado em
sessão), esta tool inspeciona uma tabela REMOTA sem trazê-la para a sessão
nem sobrescrever o dataset corrente.
"""
import json

from .registry import tool
from .consulta_aws import _setup_aws_env, _DEFAULT_DATABASE, _WORKGROUP


@tool(
    description=(
        "Inspeciona uma TABELA no Athena: devolve o schema (colunas + tipos, "
        "do catálogo Glue) e um preview de 3 linhas reais.\n\n"
        "USE: ANTES de montar um filtro/consulta sobre uma base, para saber "
        "quais colunas existem, seus tipos e como os dados se parecem — não "
        "chute nomes de coluna. É o primeiro passo quando o usuário indica "
        "uma base/tabela para trabalhar.\n\n"
        "NÃO carrega a tabela na sessão nem altera o dataset corrente (para "
        "isso, use `consulta_aws`). Para o dataset JÁ em sessão, use "
        "`descrever_dataset`.\n\n"
        "Informe `tabela` (obrigatório) e, se não for a base FQ padrão "
        "(database_rt2), também o `database`."
    ),
    icon="🔎",
)
def descrever_tabela(
    tabela: str,
    database: str = _DEFAULT_DATABASE,
    _session: dict = None,
) -> str:
    """Retorna schema (colunas+tipos) e preview de 3 linhas de uma tabela Athena.

    Args:
        tabela: Nome da tabela a inspecionar (ex.: 'RT2_AI6_OCORRENCIA_FQ_001').
        database: Database do Athena onde a tabela vive. Default 'database_rt2' (base FQ).
    """
    if not tabela or not tabela.strip():
        return json.dumps(
            {"erro": "Informe o nome da `tabela` a inspecionar."},
            ensure_ascii=False,
        )

    tabela = tabela.strip()
    database = (database or _DEFAULT_DATABASE).strip()

    import awswrangler as wr

    _setup_aws_env()

    resultado = {"database": database, "tabela": tabela}

    # 1) Schema via catálogo Glue (não consome scan do Athena).
    try:
        types = wr.catalog.get_table_types(database=database, table=tabela)
        if not types:
            return json.dumps(
                {
                    "erro": (
                        f"Tabela '{tabela}' não encontrada no database "
                        f"'{database}' (catálogo Glue). Confira o nome."
                    )
                },
                ensure_ascii=False,
            )
        resultado["colunas"] = [
            {"nome": col, "tipo": tipo} for col, tipo in types.items()
        ]
        resultado["n_colunas"] = len(types)
    except Exception as e:  # noqa: BLE001
        return json.dumps(
            {"erro": f"Falha ao ler o schema de {database}.{tabela}: {e}"},
            ensure_ascii=False,
        )

    # 2) Preview de 3 linhas reais (SELECT *). Não sobrescreve o dataset
    #    corrente da sessão — é só inspeção.
    try:
        df = wr.athena.read_sql_query(
            sql=f'SELECT * FROM "{database}"."{tabela}" LIMIT 3',
            database=database,
            ctas_approach=False,
            workgroup=_WORKGROUP,
        )
        resultado["preview_3_linhas"] = json.loads(
            df.to_json(orient="records", date_format="iso", default_handler=str)
        )
    except Exception as e:  # noqa: BLE001
        # Schema já é útil mesmo sem preview (ex.: tabela vazia ou sem
        # permissão de scan) — devolve o que tem com um aviso.
        resultado["preview_3_linhas"] = []
        resultado["aviso_preview"] = f"Não foi possível obter o preview: {e}"

    return json.dumps(resultado, ensure_ascii=False, default=str)
