"""
Tools de CLUSTERIZAÇÃO (scikit-learn) sobre o dataset corrente da sessão.

- `executar_kmeans`        — K-Means (você informa o nº de clusters).
- `executar_dbscan`        — DBSCAN (descobre o nº de clusters; marca outliers).
- `executar_agglomerative` — clustering hierárquico aglomerativo.
- `comparar_clusters`      — varre um range de K (elbow + métricas) p/ escolher K.
- `avaliar_clusters`       — métricas ricas de um agrupamento já feito.
- `calcular_silhouette`    — qualidade dos clusters já atribuídos.

Boas práticas embutidas (o que faz a clusterização ser confiável, não só
"rodar"): as features numéricas são PADRONIZADAS (StandardScaler) antes do
fit — sem isso, a coluna de maior escala domina a distância euclidiana e os
grupos saem sem sentido. Linhas com NaN nas features são descartadas do fit
(e marcadas como cluster -1). O agente pode restringir as features via
`colunas`; senão usamos todas as numéricas (excluindo uma eventual coluna
'cluster' anterior).

As tools NÃO despejam o dataset inteiro de volta — devolvem um RESUMO
(tamanho de cada cluster, % de outliers, silhueta) e persistem a coluna
'cluster' no dataset da sessão. Opcionalmente publicam um scatter PCA 2D
colorido por cluster (card no chat).

scikit-learn é importado dentro das funções (não no topo) para que uma
eventual ausência da lib não derrube o autodiscovery das demais tools.
"""
import json

# Reaproveita os helpers canônicos do módulo de análise (mesma convenção
# de sessão) em vez de duplicá-los.
from .data_analysis import _get_df, _save_df, _err
from .gerar_grafico import render_scatter_png
from .registry import tool, publish_attachment


def _select_features(df, colunas):
    """Devolve (X_df, motivo_erro). Seleciona features numéricas válidas.

    Exclui as colunas de resultado de rodadas anteriores ('cluster',
    'outlier') — elas são rótulos produzidos pelas tools, não features. Se
    `colunas` for informado, restringe a elas (as que existirem e forem
    numéricas).
    """
    import numpy as np

    num = df.select_dtypes(include=[np.number])
    derivadas = [c for c in ("cluster", "outlier") if c in num.columns]
    if derivadas:
        num = num.drop(columns=derivadas)
    if colunas:
        pedidas = [c for c in colunas if c in num.columns]
        if not pedidas:
            return None, (
                "Nenhuma das colunas informadas é numérica/existe. "
                f"Numéricas disponíveis: {list(num.columns)}.")
        num = num[pedidas]
    if num.empty or num.shape[1] == 0:
        return None, "Dataset não possui colunas numéricas para clusterização."
    return num, None


def _fit_mask(X_df):
    """Linhas sem NaN nas features (as que entram no fit)."""
    return X_df.notna().all(axis=1)


def _scaled_matrix(X_df, mask):
    from sklearn.preprocessing import StandardScaler
    X = X_df[mask].to_numpy(dtype=float)
    return StandardScaler().fit_transform(X)


def _cluster_summary(labels):
    """Resumo {label: contagem} + nº de clusters + outliers (DBSCAN)."""
    import numpy as np

    uniq, counts = np.unique(labels, return_counts=True)
    tamanhos = {int(u): int(c) for u, c in zip(uniq, counts)}
    n_outliers = tamanhos.get(-1, 0)
    n_clusters = len([u for u in uniq if u != -1])
    return tamanhos, n_clusters, n_outliers


def _internal_metrics(X_scaled, labels):
    """Métricas internas de qualidade (ignora outliers -1).

    Devolve {silhouette, davies_bouldin, calinski_harabasz} — qualquer uma
    pode vir None se não houver >=2 clusters reais com >=1 ponto cada.
    silhouette: ↑ melhor (perto de 1). davies_bouldin: ↓ melhor (perto de 0).
    calinski_harabasz: ↑ melhor.
    """
    import numpy as np
    from sklearn.metrics import (
        silhouette_score, davies_bouldin_score, calinski_harabasz_score)

    out = {"silhouette": None, "davies_bouldin": None,
           "calinski_harabasz": None}
    labels = np.asarray(labels)
    keep = labels != -1
    X = X_scaled[keep]
    lab = labels[keep]
    if len(set(lab.tolist())) < 2 or X.shape[0] < 3:
        return out
    try:
        out["silhouette"] = float(silhouette_score(X, lab))
        out["davies_bouldin"] = float(davies_bouldin_score(X, lab))
        out["calinski_harabasz"] = float(calinski_harabasz_score(X, lab))
    except Exception:  # noqa: BLE001
        pass
    return out


def _maybe_scatter(X_scaled, labels_fit, _session, titulo):
    """Publica um scatter PCA 2D colorido por cluster, se houver ≥2 features."""
    try:
        if X_scaled.shape[1] < 2:
            return False
        from sklearn.decomposition import PCA
        coords = PCA(n_components=2, random_state=42).fit_transform(X_scaled)
        img = render_scatter_png(
            coords[:, 0], coords[:, 1], grupos=labels_fit,
            titulo=titulo, rotulo_x="Componente principal 1",
            rotulo_y="Componente principal 2")
        publish_attachment(_session, {
            "kind": "chart",
            "image": f"data:image/png;base64,{img}",
            "chart_type": "dispersao",
            "tipo": "dispersao",
            "titulo": titulo,
        })
        return True
    except Exception:
        # Visualização é best-effort: nunca derruba a clusterização.
        return False


def _persist_labels(df, mask, labels_fit, _session):
    """Anexa coluna 'cluster' ao df (linhas com NaN viram -1) e salva."""
    import numpy as np

    full = np.full(len(df), -1, dtype=int)
    full[mask.to_numpy()] = labels_fit
    df = df.copy()
    df["cluster"] = full
    _save_df(df, _session)
    return df


@tool(
    description=(
        "Executa K-MEANS sobre o dataset corrente e anexa uma coluna 'cluster' "
        "com o rótulo de cada linha.\n\n"
        "USE quando o usuário quer SEGMENTAR/agrupar em um número conhecido de "
        "grupos (ex.: 'divida os clientes em 4 perfis').\n\n"
        "As features numéricas são padronizadas (StandardScaler) antes do fit. "
        "Linhas com valores ausentes nas features são descartadas do cálculo e "
        "marcadas como cluster -1.\n\n"
        "PARÂMETROS:\n"
        "- `n_clusters` (obrigatório): número de grupos (>= 2).\n"
        "- `colunas` (opcional): lista de colunas numéricas a usar; se vazio, "
        "usa todas as numéricas.\n"
        "- `desenhar` (opcional, default true): publica um scatter PCA 2D "
        "colorido por cluster no chat.\n\n"
        "RETORNA um RESUMO (tamanho de cada cluster, silhueta) — não o dataset "
        "inteiro. A coluna 'cluster' fica persistida na sessão."
    ),
    icon="🟦",
)
def executar_kmeans(
    n_clusters: int,
    colunas: list[str] = None,
    desenhar: bool = True,
    _session: dict = None,
) -> str:
    """Executa K-Means no dataset corrente da sessão.

    Args:
        n_clusters: Número de clusters desejado (>= 2).
        colunas: Colunas numéricas a usar; vazio = todas as numéricas.
        desenhar: Se True, publica um scatter PCA 2D colorido por cluster.
    """
    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")
    if n_clusters < 2:
        return _err("`n_clusters` deve ser >= 2.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    mask = _fit_mask(X_df)
    if int(mask.sum()) < n_clusters:
        return _err(
            f"Apenas {int(mask.sum())} linhas sem valores ausentes nas features "
            f"— insuficiente para {n_clusters} clusters.")

    try:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score

        X_scaled = _scaled_matrix(X_df, mask)
        labels_fit = KMeans(n_clusters=n_clusters, n_init=10,
                            random_state=42).fit_predict(X_scaled)

        sil = None
        if len(set(labels_fit.tolist())) >= 2:
            sil = float(silhouette_score(X_scaled, labels_fit))

        df = _persist_labels(df, mask, labels_fit, _session)
        tamanhos, n_cl, n_out = _cluster_summary(labels_fit)

        desenhado = False
        if desenhar:
            desenhado = _maybe_scatter(
                X_scaled, labels_fit, _session,
                f"K-Means — {n_cl} clusters")

        return json.dumps({
            "ok": True,
            "algoritmo": "kmeans",
            "n_clusters": n_cl,
            "features_usadas": list(X_df.columns),
            "linhas_clusterizadas": int(mask.sum()),
            "linhas_descartadas_nan": int((~mask).sum()),
            "tamanho_por_cluster": tamanhos,
            "silhouette_score": sil,
            "scatter_publicado": desenhado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao executar K-Means: {e}")


@tool(
    description=(
        "Executa DBSCAN sobre o dataset corrente e anexa uma coluna 'cluster' "
        "com o rótulo de cada linha (outliers = -1).\n\n"
        "USE quando o usuário quer DESCOBRIR grupos sem saber quantos são, ou "
        "DETECTAR OUTLIERS/anomalias (auditoria!). Não exige nº de clusters.\n\n"
        "As features numéricas são padronizadas (StandardScaler) antes do fit — "
        "por isso o `eps` é em unidades de desvio-padrão (0.5 é um bom ponto de "
        "partida nesse espaço). Linhas com valores ausentes são marcadas como -1.\n\n"
        "PARÂMETROS:\n"
        "- `eps` (default 0.5): distância máxima (no espaço padronizado) entre "
        "vizinhos do mesmo cluster.\n"
        "- `min_samples` (default 5): mínimo de pontos para formar um núcleo.\n"
        "- `colunas` (opcional): colunas numéricas a usar; vazio = todas.\n"
        "- `desenhar` (opcional, default true): scatter PCA 2D no chat.\n\n"
        "RETORNA um RESUMO (nº de clusters, % de outliers, silhueta) — não o "
        "dataset inteiro. A coluna 'cluster' fica persistida na sessão."
    ),
    icon="🖧",
)
def executar_dbscan(
    eps: float = 0.5,
    min_samples: int = 5,
    colunas: list[str] = None,
    desenhar: bool = True,
    _session: dict = None,
) -> str:
    """Executa DBSCAN no dataset corrente da sessão.

    Args:
        eps: Distância máxima entre vizinhos no espaço padronizado (default 0.5).
        min_samples: Mínimo de pontos para formar um núcleo (default 5).
        colunas: Colunas numéricas a usar; vazio = todas as numéricas.
        desenhar: Se True, publica um scatter PCA 2D colorido por cluster.
    """
    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    mask = _fit_mask(X_df)
    if int(mask.sum()) < min_samples:
        return _err(
            f"Apenas {int(mask.sum())} linhas sem valores ausentes nas features "
            f"— insuficiente para min_samples={min_samples}.")

    try:
        import numpy as np
        from sklearn.cluster import DBSCAN
        from sklearn.metrics import silhouette_score

        X_scaled = _scaled_matrix(X_df, mask)
        labels_fit = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(X_scaled)

        # Silhueta só faz sentido com >=2 clusters reais (ignora outliers).
        sil = None
        non_outlier = labels_fit != -1
        if len(set(labels_fit[non_outlier].tolist())) >= 2:
            sil = float(silhouette_score(X_scaled[non_outlier],
                                         labels_fit[non_outlier]))

        df = _persist_labels(df, mask, labels_fit, _session)
        tamanhos, n_cl, n_out = _cluster_summary(labels_fit)
        total_fit = int(mask.sum())
        pct_out = round(100.0 * n_out / total_fit, 1) if total_fit else 0.0

        desenhado = False
        if desenhar:
            desenhado = _maybe_scatter(
                X_scaled, labels_fit, _session,
                f"DBSCAN — {n_cl} clusters, {n_out} outliers")

        return json.dumps({
            "ok": True,
            "algoritmo": "dbscan",
            "eps": eps,
            "min_samples": min_samples,
            "n_clusters": n_cl,
            "n_outliers": n_out,
            "pct_outliers": pct_out,
            "features_usadas": list(X_df.columns),
            "linhas_clusterizadas": total_fit,
            "linhas_descartadas_nan": int((~mask).sum()),
            "tamanho_por_cluster": tamanhos,
            "silhouette_score": sil,
            "scatter_publicado": desenhado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao executar DBSCAN: {e}")


@tool(
    description=(
        "Calcula o SILHOUETTE SCORE médio dos clusters já atribuídos ao dataset "
        "corrente (coluna 'cluster').\n\n"
        "USE para avaliar a qualidade de um agrupamento feito antes (K-Means/"
        "DBSCAN): quanto mais próximo de 1, melhor a separação; perto de 0 = "
        "clusters sobrepostos; negativo = pontos provavelmente no cluster errado.\n\n"
        "As mesmas features numéricas (padronizadas) são usadas; outliers do "
        "DBSCAN (cluster -1) são ignorados no cálculo.\n\n"
        "REQUISITO: o dataset precisa ter uma coluna 'cluster' com >= 2 clusters "
        "distintos, cada um com >= 2 membros."
    ),
    icon="📏",
)
def calcular_silhouette(
    colunas: list[str] = None,
    _session: dict = None,
) -> str:
    """Calcula o silhouette score médio dos clusters do dataset corrente.

    Args:
        colunas: Colunas numéricas a usar; vazio = todas (exceto 'cluster').
    """
    import numpy as np

    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")
    if "cluster" not in df.columns:
        return _err("Dataset não possui coluna 'cluster'. Rode um clustering antes.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    labels = df["cluster"].to_numpy()
    # Ignora outliers (-1) e linhas com NaN nas features.
    mask = _fit_mask(X_df).to_numpy() & (labels != -1)
    labels = labels[mask]

    n_clusters = len(set(labels.tolist()))
    if n_clusters < 2:
        return _err("É necessário pelo menos 2 clusters distintos (fora outliers).")

    uniq, counts = np.unique(labels, return_counts=True)
    if (counts < 2).any():
        pequenos = [int(u) for u, c in zip(uniq, counts) if c < 2]
        return _err(f"Clusters com menos de 2 membros: {pequenos}. "
                    "Todos precisam de >= 2 para o silhouette.")

    try:
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import silhouette_score

        X = StandardScaler().fit_transform(X_df[mask].to_numpy(dtype=float))
        score = float(silhouette_score(X, labels))
        return json.dumps({
            "ok": True,
            "silhouette_score": score,
            "n_clusters": n_clusters,
            "features_usadas": list(X_df.columns),
            "linhas_avaliadas": int(mask.sum()),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao calcular silhouette score: {e}")


@tool(
    description=(
        "Executa CLUSTERING HIERÁRQUICO AGLOMERATIVO sobre o dataset corrente e "
        "anexa uma coluna 'cluster'.\n\n"
        "USE quando quiser COMPARAR com o K-Means (outro modelo para o mesmo "
        "número de grupos) ou quando os grupos têm forma não-esférica/aninhada. "
        "Útil para confirmar se a segmentação é estável entre algoritmos.\n\n"
        "As features são padronizadas (StandardScaler); linhas com NaN viram "
        "cluster -1. PARÂMETROS:\n"
        "- `n_clusters` (obrigatório, >= 2).\n"
        "- `linkage` (opcional, default 'ward'): 'ward'|'complete'|'average'|"
        "'single' — como a distância entre grupos é medida.\n"
        "- `colunas` (opcional): features numéricas; vazio = todas.\n"
        "- `desenhar` (opcional, default true): scatter PCA 2D.\n\n"
        "RETORNA um RESUMO com silhueta, Davies-Bouldin e Calinski-Harabasz."
    ),
    icon="🌳",
)
def executar_agglomerative(
    n_clusters: int,
    linkage: str = "ward",
    colunas: list[str] = None,
    desenhar: bool = True,
    _session: dict = None,
) -> str:
    """Executa clustering hierárquico aglomerativo no dataset corrente.

    Args:
        n_clusters: Número de clusters desejado (>= 2).
        linkage: Critério de ligação: 'ward' (default), 'complete', 'average' ou 'single'.
        colunas: Colunas numéricas a usar; vazio = todas as numéricas.
        desenhar: Se True, publica um scatter PCA 2D colorido por cluster.
    """
    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")
    if n_clusters < 2:
        return _err("`n_clusters` deve ser >= 2.")
    linkage = (linkage or "ward").strip().lower()
    if linkage not in {"ward", "complete", "average", "single"}:
        return _err("`linkage` deve ser 'ward', 'complete', 'average' ou 'single'.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    mask = _fit_mask(X_df)
    if int(mask.sum()) < n_clusters:
        return _err(
            f"Apenas {int(mask.sum())} linhas sem valores ausentes nas features "
            f"— insuficiente para {n_clusters} clusters.")

    try:
        from sklearn.cluster import AgglomerativeClustering

        X_scaled = _scaled_matrix(X_df, mask)
        labels_fit = AgglomerativeClustering(
            n_clusters=n_clusters, linkage=linkage).fit_predict(X_scaled)

        metrics = _internal_metrics(X_scaled, labels_fit)
        df = _persist_labels(df, mask, labels_fit, _session)
        tamanhos, n_cl, n_out = _cluster_summary(labels_fit)

        desenhado = False
        if desenhar:
            desenhado = _maybe_scatter(
                X_scaled, labels_fit, _session,
                f"Aglomerativo ({linkage}) — {n_cl} clusters")

        return json.dumps({
            "ok": True,
            "algoritmo": "agglomerative",
            "linkage": linkage,
            "n_clusters": n_cl,
            "features_usadas": list(X_df.columns),
            "linhas_clusterizadas": int(mask.sum()),
            "linhas_descartadas_nan": int((~mask).sum()),
            "tamanho_por_cluster": tamanhos,
            "silhouette_score": metrics["silhouette"],
            "davies_bouldin_score": metrics["davies_bouldin"],
            "calinski_harabasz_score": metrics["calinski_harabasz"],
            "scatter_publicado": desenhado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao executar clustering aglomerativo: {e}")


@tool(
    description=(
        "VARRE um intervalo de K (nº de clusters) com K-Means e devolve, para "
        "cada K, a INÉRCIA (método do cotovelo/elbow), a SILHUETA, o "
        "Davies-Bouldin e o Calinski-Harabasz — para você ESCOLHER o melhor K "
        "(e discutir a escolha com o usuário) ANTES de rodar o clustering "
        "final.\n\n"
        "USE quando o usuário não sabe quantos grupos existem, ou quando quiser "
        "justificar numericamente o K. NÃO persiste coluna 'cluster' nem altera "
        "o dataset — é só diagnóstico.\n\n"
        "Como ler: a inércia sempre cai com K — procure o 'cotovelo' (onde a "
        "queda desacelera). A silhueta e o Calinski-Harabasz: MAIOR é melhor. O "
        "Davies-Bouldin: MENOR é melhor. O `melhor_k_por_silhueta` é uma "
        "sugestão, não uma ordem.\n\n"
        "PARÂMETROS:\n"
        "- `k_min` (default 2), `k_max` (default 8): intervalo a testar.\n"
        "- `colunas` (opcional): features numéricas; vazio = todas.\n"
        "- `desenhar` (opcional, default true): publica a curva do cotovelo "
        "(inércia × K) como gráfico de linha."
    ),
    icon="📉",
)
def comparar_clusters(
    k_min: int = 2,
    k_max: int = 8,
    colunas: list[str] = None,
    desenhar: bool = True,
    _session: dict = None,
) -> str:
    """Varre K de K-Means e reporta inércia + métricas por K (sem persistir).

    Args:
        k_min: Menor K a testar (>= 2).
        k_max: Maior K a testar.
        colunas: Colunas numéricas a usar; vazio = todas as numéricas.
        desenhar: Se True, publica a curva do cotovelo (inércia × K).
    """
    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")
    if k_min < 2:
        return _err("`k_min` deve ser >= 2.")
    if k_max < k_min:
        return _err("`k_max` deve ser >= `k_min`.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    mask = _fit_mask(X_df)
    n = int(mask.sum())
    # Silhueta exige n > K; limita K_max a n-1 para não estourar.
    k_max = min(k_max, n - 1)
    if k_max < k_min:
        return _err(
            f"Apenas {n} linhas sem NaN nas features — insuficiente para "
            f"testar de K={k_min} a K={k_max}.")

    try:
        from sklearn.cluster import KMeans

        X_scaled = _scaled_matrix(X_df, mask)
        resultados = []
        for k in range(k_min, k_max + 1):
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(X_scaled)
            m = _internal_metrics(X_scaled, labels)
            resultados.append({
                "k": k,
                "inercia": float(km.inertia_),
                "silhouette": m["silhouette"],
                "davies_bouldin": m["davies_bouldin"],
                "calinski_harabasz": m["calinski_harabasz"],
            })

        com_sil = [r for r in resultados if r["silhouette"] is not None]
        melhor = max(com_sil, key=lambda r: r["silhouette"])["k"] if com_sil else None

        desenhado = False
        if desenhar:
            try:
                from .gerar_grafico import gerar_grafico
                gerar_grafico(
                    _session=_session, tipo="linha",
                    categorias=[str(r["k"]) for r in resultados],
                    valores=[r["inercia"] for r in resultados],
                    titulo="Método do cotovelo (inércia × K)",
                    rotulo_x="Número de clusters (K)", rotulo_y="Inércia")
                desenhado = True
            except Exception:  # noqa: BLE001
                desenhado = False

        return json.dumps({
            "ok": True,
            "features_usadas": list(X_df.columns),
            "linhas_avaliadas": n,
            "k_testados": list(range(k_min, k_max + 1)),
            "por_k": resultados,
            "melhor_k_por_silhueta": melhor,
            "curva_cotovelo_publicada": desenhado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao comparar clusters: {e}")


@tool(
    description=(
        "Avalia a QUALIDADE de um agrupamento já atribuído (coluna 'cluster') "
        "com VÁRIAS métricas internas de uma vez, mais o balanceamento dos "
        "grupos.\n\n"
        "Métricas: silhueta (↑ melhor, perto de 1), Davies-Bouldin (↓ melhor, "
        "perto de 0) e Calinski-Harabasz (↑ melhor). Reporta também o tamanho "
        "de cada cluster, o % de outliers (-1) e a razão entre o maior e o "
        "menor grupo (balanceamento — desbalanceado se >> 1).\n\n"
        "USE depois de rodar K-Means/DBSCAN/Aglomerativo para um veredito "
        "numérico mais completo que só a silhueta. Outliers (-1) são ignorados "
        "no cálculo das métricas internas.\n\n"
        "REQUISITO: coluna 'cluster' com >= 2 clusters reais."
    ),
    icon="🎯",
)
def avaliar_clusters(
    colunas: list[str] = None,
    _session: dict = None,
) -> str:
    """Avalia um agrupamento já feito com múltiplas métricas + balanceamento.

    Args:
        colunas: Colunas numéricas a usar; vazio = todas (exceto 'cluster').
    """
    import numpy as np

    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")
    if "cluster" not in df.columns:
        return _err("Dataset não possui coluna 'cluster'. Rode um clustering antes.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    labels_all = df["cluster"].to_numpy()
    fit_mask = _fit_mask(X_df).to_numpy()
    tamanhos, n_cl, n_out = _cluster_summary(labels_all[fit_mask])

    # Métricas internas no espaço padronizado (só linhas sem NaN).
    keep = fit_mask & (labels_all != -1)
    if len(set(labels_all[keep].tolist())) < 2:
        return _err("É necessário pelo menos 2 clusters reais (fora outliers).")

    try:
        from sklearn.preprocessing import StandardScaler

        X = StandardScaler().fit_transform(X_df[keep].to_numpy(dtype=float))
        metrics = _internal_metrics(X, labels_all[keep])

        # Balanceamento: razão maior/menor grupo (ignora outliers).
        tam_reais = {k: v for k, v in tamanhos.items() if k != -1}
        maior = max(tam_reais.values()) if tam_reais else 0
        menor = min(tam_reais.values()) if tam_reais else 0
        razao = round(maior / menor, 2) if menor else None
        total_fit = int(fit_mask.sum())
        pct_out = round(100.0 * n_out / total_fit, 1) if total_fit else 0.0

        return json.dumps({
            "ok": True,
            "n_clusters": n_cl,
            "features_usadas": list(X_df.columns),
            "linhas_avaliadas": int(keep.sum()),
            "tamanho_por_cluster": tamanhos,
            "n_outliers": n_out,
            "pct_outliers": pct_out,
            "balanceamento_maior_menor": razao,
            "silhouette_score": metrics["silhouette"],
            "davies_bouldin_score": metrics["davies_bouldin"],
            "calinski_harabasz_score": metrics["calinski_harabasz"],
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao avaliar clusters: {e}")
