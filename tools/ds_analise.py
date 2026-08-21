"""
Tools de CIÊNCIA DE DADOS não-supervisionada (scikit-learn) sobre o dataset
corrente da sessão — complementam a clusterização:

- `executar_pca`        — Análise de Componentes Principais: variância
                          explicada por componente, acumulada, nº de
                          componentes p/ 90%/95% e loadings (peso das
                          features). Reduz dimensionalidade / acha os eixos
                          que mais separam os dados.
- `detectar_outliers`   — detecção dedicada de anomalias (Isolation Forest,
                          LOF, z-score ou IQR). Anexa coluna 'outlier'
                          (1 = anomalia, 0 = normal).
- `selecionar_features` — seleção de features NÃO-supervisionada: remove
                          colunas de variância ~zero e colunas altamente
                          correlacionadas (redundantes), explicando o porquê.

Boas práticas embutidas (o que torna a análise confiável, não só "rodar"):
as features numéricas são PADRONIZADAS (StandardScaler) antes de PCA /
detecção por distância — sem isso a coluna de maior escala domina. Linhas com
NaN nas features são descartadas do cálculo. Reaproveita os helpers canônicos
de `data_analysis` e `clusterizer` (mesma convenção de sessão) em vez de
duplicá-los.

scikit-learn é importado dentro das funções (não no topo) para que uma
eventual ausência da lib não derrube o autodiscovery das demais tools.
"""
import json

from .data_analysis import _get_df, _save_df, _err
from .clusterizer import _select_features, _fit_mask, _scaled_matrix
from .gerar_grafico import render_scatter_png
from .registry import tool, publish_attachment


@tool(
    description=(
        "Executa PCA (Análise de Componentes Principais) sobre o dataset "
        "corrente: padroniza as features numéricas e devolve a VARIÂNCIA "
        "EXPLICADA por componente, a acumulada, quantos componentes são "
        "necessários para reter 90%/95% da variância e os LOADINGS (o peso de "
        "cada feature em cada um dos 2 primeiros componentes).\n\n"
        "USE para: reduzir dimensionalidade antes de clusterizar, entender "
        "quais variáveis carregam a maior parte da informação, ou checar "
        "redundância entre features. Também publica um scatter dos dados "
        "projetados nos 2 primeiros componentes.\n\n"
        "PARÂMETROS:\n"
        "- `colunas` (opcional): features numéricas a usar; vazio = todas.\n"
        "- `n_componentes` (opcional, default 0 = todos): nº de componentes a "
        "reportar/projetar.\n"
        "- `desenhar` (opcional, default true): scatter 2D (PC1 × PC2).\n\n"
        "NÃO altera o dataset — é diagnóstico. Cite a variância explicada real "
        "do retorno; não invente."
    ),
    icon="🧭",
)
def executar_pca(
    colunas: list[str] = None,
    n_componentes: int = 0,
    desenhar: bool = True,
    _session: dict = None,
) -> str:
    """Executa PCA e reporta variância explicada + loadings (sem persistir).

    Args:
        colunas: Colunas numéricas a usar; vazio = todas as numéricas.
        n_componentes: Nº de componentes a reportar; 0 (default) = todos possíveis.
        desenhar: Se True, publica um scatter 2D dos dados projetados (PC1 × PC2).
    """
    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)
    if X_df.shape[1] < 2:
        return _err("PCA precisa de pelo menos 2 colunas numéricas.")

    mask = _fit_mask(X_df)
    if int(mask.sum()) < 2:
        return _err("Menos de 2 linhas sem valores ausentes nas features.")

    try:
        import numpy as np
        from sklearn.decomposition import PCA

        X_scaled = _scaled_matrix(X_df, mask)
        max_comp = min(X_scaled.shape[0], X_scaled.shape[1])
        k = max_comp if n_componentes <= 0 else min(n_componentes, max_comp)

        pca = PCA(n_components=k, random_state=42)
        coords = pca.fit_transform(X_scaled)

        ratio = [round(float(v), 4) for v in pca.explained_variance_ratio_]
        acum = [round(float(v), 4) for v in np.cumsum(pca.explained_variance_ratio_)]

        def _n_para(limite):
            for i, c in enumerate(acum, start=1):
                if c >= limite:
                    return i
            return len(acum)

        feats = list(X_df.columns)
        # Loadings dos 2 primeiros componentes: peso de cada feature.
        loadings = {}
        for ci in range(min(2, k)):
            comp = pca.components_[ci]
            loadings[f"PC{ci + 1}"] = {
                f: round(float(w), 4) for f, w in zip(feats, comp)
            }

        desenhado = False
        if desenhar and k >= 2:
            try:
                img = render_scatter_png(
                    coords[:, 0], coords[:, 1],
                    titulo="PCA — projeção nos 2 primeiros componentes",
                    rotulo_x=f"PC1 ({ratio[0] * 100:.1f}%)",
                    rotulo_y=f"PC2 ({ratio[1] * 100:.1f}%)")
                publish_attachment(_session, {
                    "kind": "chart",
                    "image": f"data:image/png;base64,{img}",
                    "chart_type": "dispersao",
                    "tipo": "dispersao",
                    "titulo": "PCA — projeção 2D",
                })
                desenhado = True
            except Exception:  # noqa: BLE001
                desenhado = False

        return json.dumps({
            "ok": True,
            "features_usadas": feats,
            "linhas_avaliadas": int(mask.sum()),
            "n_componentes": k,
            "variancia_explicada": ratio,
            "variancia_acumulada": acum,
            "componentes_para_90pct": _n_para(0.90),
            "componentes_para_95pct": _n_para(0.95),
            "loadings": loadings,
            "scatter_publicado": desenhado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao executar PCA: {e}")


@tool(
    description=(
        "DETECTA OUTLIERS/ANOMALIAS no dataset corrente com um método "
        "dedicado e anexa uma coluna 'outlier' (1 = anomalia, 0 = normal). "
        "Diferente do DBSCAN, foca só em achar pontos atípicos.\n\n"
        "USE em auditoria para destacar registros suspeitos/atípicos "
        "(valores fora do padrão, combinações raras).\n\n"
        "MÉTODOS (`metodo`):\n"
        "- 'isolation_forest' (default): multivariado, robusto, baseado em "
        "árvores; bom padrão geral. Padroniza as features.\n"
        "- 'lof': Local Outlier Factor — densidade local; pega anomalias "
        "relativas à vizinhança. Padroniza as features.\n"
        "- 'zscore': univariado/multivariado por desvio-padrão; marca linha "
        "se QUALQUER feature passar de `limite` desvios (default 3).\n"
        "- 'iqr': por amplitude interquartil; marca se alguma feature cair "
        "fora de [Q1 - 1.5·IQR, Q3 + 1.5·IQR].\n\n"
        "PARÂMETROS:\n"
        "- `metodo` (default 'isolation_forest').\n"
        "- `contaminacao` (default 0.05): fração esperada de outliers "
        "(isolation_forest/lof).\n"
        "- `limite` (default 3.0): nº de desvios (zscore).\n"
        "- `colunas` (opcional): features numéricas; vazio = todas.\n"
        "- `desenhar` (opcional, default true): scatter PCA 2D (normais × anomalias).\n\n"
        "RETORNA quantos outliers achou e o %. A coluna 'outlier' fica "
        "persistida. 'Detectei N anomalias' só é válido se este retorno "
        "trouxe n_outliers: N."
    ),
    icon="🚨",
)
def detectar_outliers(
    metodo: str = "isolation_forest",
    contaminacao: float = 0.05,
    limite: float = 3.0,
    colunas: list[str] = None,
    desenhar: bool = True,
    _session: dict = None,
) -> str:
    """Detecta outliers e anexa coluna 'outlier' (1=anomalia, 0=normal).

    Args:
        metodo: 'isolation_forest' (default), 'lof', 'zscore' ou 'iqr'.
        contaminacao: Fração esperada de outliers (isolation_forest/lof). Entre 0 e 0.5.
        limite: Número de desvios-padrão para o método 'zscore' (default 3.0).
        colunas: Colunas numéricas a usar; vazio = todas as numéricas.
        desenhar: Se True, publica um scatter PCA 2D destacando as anomalias.
    """
    import numpy as np

    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")

    metodo = (metodo or "isolation_forest").strip().lower()
    if metodo not in {"isolation_forest", "lof", "zscore", "iqr"}:
        return _err("`metodo` deve ser 'isolation_forest', 'lof', 'zscore' ou 'iqr'.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    mask = _fit_mask(X_df)
    n = int(mask.sum())
    if n < 3:
        return _err("Menos de 3 linhas sem valores ausentes nas features.")

    try:
        # flags_fit: 1 = outlier, 0 = normal (apenas nas linhas do fit).
        if metodo in {"isolation_forest", "lof"}:
            if not (0.0 < contaminacao < 0.5):
                return _err("`contaminacao` deve estar entre 0 e 0.5.")
            X_scaled = _scaled_matrix(X_df, mask)
            if metodo == "isolation_forest":
                from sklearn.ensemble import IsolationForest
                pred = IsolationForest(
                    contamination=contaminacao,
                    random_state=42).fit_predict(X_scaled)
            else:
                from sklearn.neighbors import LocalOutlierFactor
                k = min(20, max(2, n - 1))
                pred = LocalOutlierFactor(
                    n_neighbors=k,
                    contamination=contaminacao).fit_predict(X_scaled)
            flags_fit = (pred == -1).astype(int)
        else:
            # zscore / iqr trabalham sobre os valores originais (não padronizados).
            Xv = X_df[mask].to_numpy(dtype=float)
            if metodo == "zscore":
                if limite <= 0:
                    return _err("`limite` deve ser > 0.")
                mu = Xv.mean(axis=0)
                sd = Xv.std(axis=0)
                sd[sd == 0] = np.inf  # coluna constante nunca dispara
                z = np.abs((Xv - mu) / sd)
                flags_fit = (z > limite).any(axis=1).astype(int)
            else:  # iqr
                q1 = np.percentile(Xv, 25, axis=0)
                q3 = np.percentile(Xv, 75, axis=0)
                iqr = q3 - q1
                low = q1 - 1.5 * iqr
                high = q3 + 1.5 * iqr
                flags_fit = ((Xv < low) | (Xv > high)).any(axis=1).astype(int)

        # Persiste coluna 'outlier' (linhas com NaN nas features = 0/normal).
        full = np.zeros(len(df), dtype=int)
        full[mask.to_numpy()] = flags_fit
        out_df = df.copy()
        out_df["outlier"] = full
        _save_df(out_df, _session)

        n_out = int(flags_fit.sum())
        pct = round(100.0 * n_out / n, 1) if n else 0.0

        desenhado = False
        if desenhar and X_df.shape[1] >= 2:
            try:
                from sklearn.decomposition import PCA
                X_scaled = _scaled_matrix(X_df, mask)
                coords = PCA(n_components=2, random_state=42).fit_transform(X_scaled)
                img = render_scatter_png(
                    coords[:, 0], coords[:, 1], grupos=flags_fit,
                    titulo=f"Outliers ({metodo}) — {n_out} anomalias",
                    rotulo_x="Componente principal 1",
                    rotulo_y="Componente principal 2",
                    nomes_grupos={0: "Normal", 1: "Anomalia"})
                publish_attachment(_session, {
                    "kind": "chart",
                    "image": f"data:image/png;base64,{img}",
                    "chart_type": "dispersao",
                    "tipo": "dispersao",
                    "titulo": f"Outliers ({metodo})",
                })
                desenhado = True
            except Exception:  # noqa: BLE001
                desenhado = False

        return json.dumps({
            "ok": True,
            "metodo": metodo,
            "n_outliers": n_out,
            "pct_outliers": pct,
            "linhas_avaliadas": n,
            "linhas_descartadas_nan": int((~mask).sum()),
            "features_usadas": list(X_df.columns),
            "scatter_publicado": desenhado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao detectar outliers: {e}")


@tool(
    description=(
        "SELEÇÃO DE FEATURES não-supervisionada: identifica colunas numéricas "
        "REDUNDANTES ou inúteis para modelagem — variância ~zero (quase "
        "constantes) e pares altamente correlacionados (uma carrega a "
        "informação da outra).\n\n"
        "USE antes de clusterizar/PCA para enxugar o conjunto de features e "
        "evitar que variáveis correlacionadas dominem a distância. É um "
        "DIAGNÓSTICO: por default NÃO altera o dataset, só recomenda.\n\n"
        "PARÂMETROS:\n"
        "- `limiar_correlacao` (default 0.95): acima disso (|correlação|), o "
        "par é considerado redundante e uma das colunas é sugerida p/ remoção.\n"
        "- `limiar_variancia` (default 0.0): variância (no espaço padronizado) "
        "abaixo da qual a coluna é quase-constante.\n"
        "- `colunas` (opcional): restringe a análise a estas numéricas.\n"
        "- `aplicar` (default false): se true, REMOVE as colunas sugeridas do "
        "dataset da sessão (destrutivo).\n\n"
        "RETORNA as colunas mantidas, as removidas e o MOTIVO de cada remoção."
    ),
    icon="🧹",
)
def selecionar_features(
    limiar_correlacao: float = 0.95,
    limiar_variancia: float = 0.0,
    colunas: list[str] = None,
    aplicar: bool = False,
    _session: dict = None,
) -> str:
    """Recomenda (ou aplica) remoção de features redundantes/quase-constantes.

    Args:
        limiar_correlacao: |correlação| acima da qual um par é redundante (default 0.95).
        limiar_variancia: Variância no espaço padronizado abaixo da qual a coluna é quase-constante (default 0.0).
        colunas: Colunas numéricas a considerar; vazio = todas.
        aplicar: Se True, remove as colunas sugeridas do dataset (destrutivo). Default False.
    """
    import numpy as np

    df = _get_df(_session)
    if df is None or df.empty:
        return _err("Nenhum dataset na sessão.")

    X_df, motivo = _select_features(df, colunas)
    if X_df is None:
        return _err(motivo)

    mask = _fit_mask(X_df)
    if int(mask.sum()) < 2:
        return _err("Menos de 2 linhas sem valores ausentes nas features.")

    try:
        from sklearn.preprocessing import StandardScaler

        feats = list(X_df.columns)
        sub = X_df[mask]
        remover = {}  # coluna -> motivo

        # 1) Variância ~zero (no espaço padronizado: variância 0 = constante).
        scaled = StandardScaler().fit_transform(sub.to_numpy(dtype=float))
        var = scaled.var(axis=0)
        for f, v in zip(feats, var):
            if v <= limiar_variancia:
                remover[f] = f"variância ~zero ({v:.4g}) — coluna quase constante"

        # 2) Alta correlação: para cada par |corr| > limiar, remove a 2ª.
        restantes = [f for f in feats if f not in remover]
        pares_corr = []
        if len(restantes) >= 2:
            corr = sub[restantes].corr().abs()
            for i in range(len(restantes)):
                for j in range(i + 1, len(restantes)):
                    c = corr.iloc[i, j]
                    if c >= limiar_correlacao:
                        a, b = restantes[i], restantes[j]
                        pares_corr.append({"feature_a": a, "feature_b": b,
                                           "correlacao": round(float(c), 4)})
                        if b not in remover:
                            remover[b] = (
                                f"correlação |{c:.3f}| com '{a}' — redundante")

        mantidas = [f for f in feats if f not in remover]

        aplicado = False
        if aplicar and remover:
            novo = df.drop(columns=[c for c in remover if c in df.columns])
            _save_df(novo, _session)
            aplicado = True

        return json.dumps({
            "ok": True,
            "features_analisadas": feats,
            "features_mantidas": mantidas,
            "features_removidas": [
                {"coluna": c, "motivo": m} for c, m in remover.items()],
            "pares_alta_correlacao": pares_corr,
            "limiar_correlacao": limiar_correlacao,
            "aplicado_ao_dataset": aplicado,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao selecionar features: {e}")
