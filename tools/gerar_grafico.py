"""
Tool GENÉRICA de geração de gráficos (matplotlib) exibidos no chat.

Tool única de gráficos: cobre vários tipos via o parâmetro `tipo` —
barras, linha, pizza, dispersão,
histograma, boxplot, área e heatmap. Renderiza um PNG (backend Agg, paleta
da marca, fundo transparente p/ casar com o tema escuro) e publica como
attachment `kind: "chart"` — o mesmo card que o frontend já usa.

Também expõe `render_scatter_png(...)`, reaproveitado pela tool de
clusterização para desenhar o scatter PCA colorido por cluster.
"""
import base64
import json
from io import BytesIO

from .registry import tool, publish_attachment


# Paleta Itaú usada nos gráficos gerados pelo backend.
# Tons vivos/brilhantes — pensados p/ "saltar" sobre o fundo escuro do chat.
_PALETTE = [
    "#818cf8", "#a78bfa", "#22d3ee", "#34d399",
    "#fbbf24", "#fb7185", "#c4b5fd", "#67e8f9",
]
_TEXT = "#e6e7f0"
_MUTED = "#9a9cb0"
_GRID = "#ffffff22"

# DPI alto p/ renderização nítida (importante ao expandir em tela cheia).
_DPI = 220

# Tipos aceitos -> rótulo amigável exibido no card.
_TIPOS = {
    "barras": "Gráfico de barras",
    "linha": "Gráfico de linhas",
    "pizza": "Gráfico de pizza",
    "dispersao": "Gráfico de dispersão",
    "histograma": "Histograma",
    "boxplot": "Boxplot",
    "area": "Gráfico de área",
    "heatmap": "Mapa de calor",
}


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _coerce_floats(vals):
    out = []
    for v in vals:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out


def _parse_series(series_json: str, valores):
    """Normaliza séries em [{'nome': str, 'valores': [float]}].

    Aceita o atalho `valores` (série única) ou `series_json`
    (lista JSON de {nome, valores}). Lança ValueError com mensagem clara.
    """
    series = []
    if series_json and series_json.strip():
        try:
            parsed = json.loads(series_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"`series_json` não é um JSON válido: {e}")
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(
                "`series_json` deve ser uma lista de objetos "
                '{"nome": "...", "valores": [...]}.'
            )
        for i, s in enumerate(parsed):
            if not isinstance(s, dict) or "valores" not in s:
                raise ValueError(
                    f"Série #{i + 1} inválida — precisa de 'valores' (lista de números)."
                )
            series.append({
                "nome": str(s.get("nome") or f"Série {i + 1}"),
                "valores": _coerce_floats(s["valores"]),
            })
    elif valores:
        series.append({"nome": "", "valores": _coerce_floats(valores)})
    else:
        raise ValueError(
            "Forneça `valores` (série única) ou `series_json` (múltiplas séries)."
        )
    return series


def _style_axes(ax):
    """Aplica a estética escura/transparente padrão a um eixo."""
    ax.set_facecolor("none")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=10)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_color(_TEXT)


def _decorate(ax, titulo, rotulo_x, rotulo_y):
    if titulo and titulo.strip():
        ax.set_title(titulo.strip(), color=_TEXT, fontsize=14,
                     fontweight="bold", pad=14)
    if rotulo_x and rotulo_x.strip():
        ax.set_xlabel(rotulo_x.strip(), color=_TEXT, fontsize=11)
    if rotulo_y and rotulo_y.strip():
        ax.set_ylabel(rotulo_y.strip(), color=_TEXT, fontsize=11)


def _fig_to_b64(fig) -> str:
    import matplotlib.pyplot as plt
    buf = BytesIO()
    fig.savefig(buf, format="png", transparent=True, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── Helper público reaproveitado por clusterizer.py ─────────────────

def render_scatter_png(xs, ys, grupos=None, titulo="", rotulo_x="",
                       rotulo_y="", nomes_grupos=None) -> str:
    """Renderiza um scatter (opcionalmente colorido por grupo) e devolve b64.

    Args:
        xs, ys: coordenadas dos pontos.
        grupos: rótulo de grupo por ponto (ex.: labels de cluster) ou None.
        nomes_grupos: dict {label: nome_legenda} opcional.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(8, 5.4), dpi=_DPI)
    fig.patch.set_alpha(0)
    _style_axes(ax)

    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)

    if grupos is None:
        ax.scatter(xs, ys, s=30, c=_PALETTE[0], alpha=0.9,
                   edgecolors="none", zorder=3)
    else:
        grupos = np.asarray(grupos)
        for i, g in enumerate(sorted(set(grupos.tolist()))):
            mask = grupos == g
            # DBSCAN marca outliers como -1 — cinza, sem destaque.
            is_outlier = (g == -1)
            color = _MUTED if is_outlier else _PALETTE[i % len(_PALETTE)]
            label = (nomes_grupos or {}).get(g) or (
                "Outliers" if is_outlier else f"Cluster {g}")
            ax.scatter(xs[mask], ys[mask], s=32, c=color,
                       alpha=0.5 if is_outlier else 0.92,
                       edgecolors="none", label=str(label), zorder=3)
        leg = ax.legend(frameon=False, fontsize=10, labelcolor=_TEXT)
        if leg:
            leg.get_frame().set_alpha(0)

    ax.grid(color=_GRID, linewidth=0.8, zorder=0)
    _decorate(ax, titulo, rotulo_x, rotulo_y)
    fig.tight_layout()
    return _fig_to_b64(fig)


# ── Renderizadores por tipo ─────────────────────────────────────────

def _render_barras(ax, categorias, series, orientacao, empilhado):
    import numpy as np
    cats = [str(c) for c in categorias]
    n_cats = len(cats)
    n_series = len(series)
    for s in series:
        if len(s["valores"]) != n_cats:
            raise ValueError(
                f"A série '{s['nome'] or 'única'}' tem {len(s['valores'])} "
                f"valores, mas há {n_cats} categorias — devem bater 1:1.")
    horizontal = (orientacao or "vertical").strip().lower().startswith("h")
    idx = np.arange(n_cats)
    if empilhado:
        offset = np.zeros(n_cats)
        for si, s in enumerate(series):
            vals = np.nan_to_num(np.array(s["valores"], dtype=float))
            color = _PALETTE[si % len(_PALETTE)]
            if horizontal:
                ax.barh(idx, vals, left=offset, color=color, label=s["nome"],
                        height=0.7, zorder=3)
            else:
                ax.bar(idx, vals, bottom=offset, color=color, label=s["nome"],
                       width=0.7, zorder=3)
            offset += vals
    else:
        group_w = 0.8
        bar_w = group_w / n_series
        for si, s in enumerate(series):
            vals = np.nan_to_num(np.array(s["valores"], dtype=float))
            color = _PALETTE[si % len(_PALETTE)]
            pos = idx - group_w / 2 + bar_w * (si + 0.5)
            if horizontal:
                ax.barh(pos, vals, height=bar_w, color=color, label=s["nome"],
                        zorder=3)
            else:
                ax.bar(pos, vals, width=bar_w, color=color, label=s["nome"],
                       zorder=3)
    if horizontal:
        ax.set_yticks(idx)
        ax.set_yticklabels(cats)
        ax.invert_yaxis()
        ax.grid(axis="x", color=_GRID, linewidth=0.8, zorder=0)
    else:
        ax.set_xticks(idx)
        ax.set_xticklabels(cats, rotation=30 if n_cats > 6 else 0,
                           ha="right" if n_cats > 6 else "center")
        ax.grid(axis="y", color=_GRID, linewidth=0.8, zorder=0)


def _render_linha(ax, categorias, series, area=False):
    import numpy as np
    cats = [str(c) for c in categorias]
    n_cats = len(cats)
    for s in series:
        if len(s["valores"]) != n_cats:
            raise ValueError(
                f"A série '{s['nome'] or 'única'}' tem {len(s['valores'])} "
                f"valores, mas há {n_cats} categorias — devem bater 1:1.")
    idx = np.arange(n_cats)
    for si, s in enumerate(series):
        vals = np.array(s["valores"], dtype=float)
        color = _PALETTE[si % len(_PALETTE)]
        ax.plot(idx, vals, color=color, marker="o", markersize=4,
                linewidth=2, label=s["nome"], zorder=3)
        if area:
            ax.fill_between(idx, vals, color=color, alpha=0.28, zorder=2)
    ax.set_xticks(idx)
    ax.set_xticklabels(cats, rotation=30 if n_cats > 6 else 0,
                       ha="right" if n_cats > 6 else "center")
    ax.grid(color=_GRID, linewidth=0.8, zorder=0)


def _render_pizza(ax, categorias, series):
    import numpy as np
    if not series:
        raise ValueError("Pizza precisa de uma série de `valores`.")
    cats = [str(c) for c in categorias]
    vals = np.nan_to_num(np.array(series[0]["valores"], dtype=float))
    if len(vals) != len(cats):
        raise ValueError("Nº de valores deve bater com o nº de categorias.")
    if (vals < 0).any():
        raise ValueError("Pizza não aceita valores negativos.")
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(cats))]
    wedges, texts, autotexts = ax.pie(
        vals, labels=cats, colors=colors, autopct="%1.1f%%",
        startangle=90, counterclock=False,
        textprops={"color": _TEXT, "fontsize": 10},
        wedgeprops={"edgecolor": "#0000", "linewidth": 1},
    )
    for at in autotexts:
        at.set_color("#0b0d17")
        at.set_fontweight("bold")
    ax.axis("equal")


def _render_dispersao(ax, xs, ys, rotulo_x, rotulo_y):
    import numpy as np
    if not xs or not ys:
        raise ValueError("Dispersão precisa de `x` e `y` (listas numéricas).")
    if len(xs) != len(ys):
        raise ValueError(f"`x` ({len(xs)}) e `y` ({len(ys)}) devem ter o mesmo tamanho.")
    ax.scatter(np.array(xs, dtype=float), np.array(ys, dtype=float),
               s=30, c=_PALETTE[0], alpha=0.9, edgecolors="none", zorder=3)
    ax.grid(color=_GRID, linewidth=0.8, zorder=0)


def _render_histograma(ax, series, bins):
    import numpy as np
    if not series:
        raise ValueError("Histograma precisa de `valores` (ou séries).")
    for si, s in enumerate(series):
        vals = np.array([v for v in s["valores"] if v == v], dtype=float)  # drop NaN
        if vals.size == 0:
            continue
        ax.hist(vals, bins=max(1, int(bins)), color=_PALETTE[si % len(_PALETTE)],
                alpha=0.8 if len(series) > 1 else 0.95,
                label=s["nome"], edgecolor="#0000", zorder=3)
    ax.grid(axis="y", color=_GRID, linewidth=0.8, zorder=0)


def _render_boxplot(ax, categorias, series):
    import numpy as np
    if not series:
        raise ValueError("Boxplot precisa de `valores` (ou séries).")
    data = [np.array([v for v in s["valores"] if v == v], dtype=float)
            for s in series]
    labels = ([str(c) for c in categorias] if categorias and
              len(categorias) == len(series)
              else [s["nome"] or f"Série {i+1}" for i, s in enumerate(series)])
    bp = ax.boxplot(data, labels=labels, patch_artist=True,
                    medianprops={"color": _TEXT, "linewidth": 1.5},
                    whiskerprops={"color": _MUTED},
                    capprops={"color": _MUTED},
                    flierprops={"markeredgecolor": _MUTED, "markersize": 4})
    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(_PALETTE[i % len(_PALETTE)])
        box.set_alpha(0.85)
        box.set_edgecolor(_GRID)
    ax.grid(axis="y", color=_GRID, linewidth=0.8, zorder=0)


def _render_heatmap(ax, fig, matriz_json, categorias, series, titulo):
    import numpy as np
    if not matriz_json or not matriz_json.strip():
        raise ValueError(
            'Heatmap precisa de `matriz_json`: uma matriz 2D JSON, ex.: [[1,2],[3,4]].')
    try:
        mat = json.loads(matriz_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"`matriz_json` não é um JSON válido: {e}")
    arr = np.array(mat, dtype=float)
    if arr.ndim != 2:
        raise ValueError("`matriz_json` deve ser uma matriz 2D (lista de listas).")
    im = ax.imshow(arr, cmap="viridis", aspect="auto")
    cols = [str(c) for c in (categorias or [])]
    rows = [s["nome"] for s in series] if series else []
    if cols and len(cols) == arr.shape[1]:
        ax.set_xticks(range(arr.shape[1]))
        ax.set_xticklabels(cols, rotation=30, ha="right")
    if rows and len(rows) == arr.shape[0]:
        ax.set_yticks(range(arr.shape[0]))
        ax.set_yticklabels(rows)
    # Anota valores quando a matriz é pequena.
    if arr.size <= 100:
        for i in range(arr.shape[0]):
            for j in range(arr.shape[1]):
                ax.text(j, i, f"{arr[i, j]:.2g}", ha="center", va="center",
                        color="#fff", fontsize=8)
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(colors=_MUTED)


@tool(
    description=(
        "Gera um GRÁFICO (matplotlib) e o exibe no chat como uma imagem bonita, "
        "com botão de download em PNG. Tool genérica: escolha o tipo via `tipo`.\n\n"
        "TIPOS suportados (`tipo`):\n"
        "- 'barras'   — comparar categorias / ranking / contagem por grupo.\n"
        "- 'linha'    — evolução/tendência ao longo de uma ordem (tempo, etc.).\n"
        "- 'area'     — como linha, mas com a área preenchida.\n"
        "- 'pizza'    — composição (partes de um todo). Use poucas fatias.\n"
        "- 'dispersao'— relação entre duas variáveis numéricas (use `x` e `y`).\n"
        "- 'histograma'— distribuição de UMA variável numérica (use `valores`).\n"
        "- 'boxplot'  — distribuição/quartis/outliers de uma ou mais séries.\n"
        "- 'heatmap'  — matriz 2D de intensidades (use `matriz_json`).\n\n"
        "DADOS (conforme o tipo):\n"
        "- barras/linha/area: `categorias` (eixo) + `valores` (série única) "
        "OU `series_json` ([{\"nome\":...,\"valores\":[...]}]) p/ várias séries.\n"
        "- pizza: `categorias` (rótulos) + `valores` (fatias, não-negativos).\n"
        "- dispersao: `x` e `y` (listas numéricas do mesmo tamanho).\n"
        "- histograma: `valores` (ou `series_json`); `bins` controla as faixas.\n"
        "- boxplot: `series_json` (uma caixa por série) ou `valores`.\n"
        "- heatmap: `matriz_json` (lista de listas); `categorias`=colunas, "
        "nomes das séries=linhas (opcionais).\n\n"
        "EXTRAS: `orientacao` ('vertical'|'horizontal', só barras), `empilhado` "
        "(barras/empilhar séries). Use `titulo`, `rotulo_x`, `rotulo_y` p/ contexto.\n\n"
        "NÃO repita os dados na resposta — o card aparece sozinho."
    ),
    icon="📈",
)
def gerar_grafico(
    _session: dict,
    tipo: str,
    categorias: list[str] = None,
    valores: list[float] = None,
    series_json: str = "",
    x: list[float] = None,
    y: list[float] = None,
    matriz_json: str = "",
    bins: int = 10,
    titulo: str = "",
    rotulo_x: str = "",
    rotulo_y: str = "",
    orientacao: str = "vertical",
    empilhado: bool = False,
) -> str:
    """Renderiza um gráfico do `tipo` pedido e publica como card no chat.

    Args:
        tipo: Tipo do gráfico: barras, linha, area, pizza, dispersao,
            histograma, boxplot ou heatmap.
        categorias: Rótulos do eixo/fatias (barras, linha, area, pizza, heatmap).
        valores: Série única de valores (barras, linha, area, pizza, histograma, boxplot).
        series_json: JSON p/ múltiplas séries: [{"nome": "...", "valores": [...]}].
        x: Coordenadas X (dispersao).
        y: Coordenadas Y (dispersao).
        matriz_json: Matriz 2D em JSON (heatmap): [[...], [...]].
        bins: Nº de faixas do histograma (default 10).
        titulo: Título exibido no topo do gráfico e do card.
        rotulo_x: Rótulo do eixo X.
        rotulo_y: Rótulo do eixo Y.
        orientacao: 'vertical' (default) ou 'horizontal' — só para barras.
        empilhado: Empilha as séries (barras) em vez de agrupar lado a lado.
    """
    tipo_norm = (tipo or "").strip().lower()
    # Aceita acento/variações comuns.
    _ALIAS = {"dispersão": "dispersao", "área": "area", "linhas": "linha",
              "scatter": "dispersao", "bar": "barras", "line": "linha",
              "pie": "pizza", "hist": "histograma", "box": "boxplot",
              "mapa de calor": "heatmap"}
    tipo_norm = _ALIAS.get(tipo_norm, tipo_norm)
    if tipo_norm not in _TIPOS:
        return _err(
            f"Tipo '{tipo}' não suportado. Use um de: {', '.join(_TIPOS)}.")

    # Tipos que dependem de séries (categorias + valores/series_json).
    needs_series = tipo_norm in {"barras", "linha", "area", "pizza",
                                 "histograma", "boxplot"}
    needs_categorias = tipo_norm in {"barras", "linha", "area", "pizza"}

    if needs_categorias and not categorias:
        return _err("Forneça ao menos uma categoria em `categorias`.")

    series = []
    if needs_series:
        try:
            series = _parse_series(series_json, valores)
        except ValueError as e:
            return _err(str(e))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Tamanho da figura por tipo.
        if tipo_norm == "pizza":
            fig, ax = plt.subplots(figsize=(6.5, 6.5), dpi=_DPI)
        elif tipo_norm in {"barras", "linha", "area"}:
            n = len(categorias or [])
            n_series = max(1, len(series))
            horizontal = (orientacao or "").strip().lower().startswith("h")
            span = max(6.0, min(0.55 * n * n_series, 18.0))
            if tipo_norm == "barras" and horizontal:
                fig, ax = plt.subplots(figsize=(9, span / 1.3), dpi=_DPI)
            else:
                fig, ax = plt.subplots(figsize=(span, 4.8), dpi=_DPI)
        else:
            fig, ax = plt.subplots(figsize=(8, 5.2), dpi=_DPI)

        fig.patch.set_alpha(0)
        _style_axes(ax)

        if tipo_norm == "barras":
            _render_barras(ax, categorias, series, orientacao, empilhado)
        elif tipo_norm == "linha":
            _render_linha(ax, categorias, series, area=False)
        elif tipo_norm == "area":
            _render_linha(ax, categorias, series, area=True)
        elif tipo_norm == "pizza":
            _render_pizza(ax, categorias, series)
        elif tipo_norm == "dispersao":
            _render_dispersao(ax, x, y, rotulo_x, rotulo_y)
        elif tipo_norm == "histograma":
            _render_histograma(ax, series, bins)
        elif tipo_norm == "boxplot":
            _render_boxplot(ax, categorias, series)
        elif tipo_norm == "heatmap":
            _render_heatmap(ax, fig, matriz_json, categorias, series, titulo)

        if tipo_norm != "pizza":
            _decorate(ax, titulo, rotulo_x, rotulo_y)
        elif titulo and titulo.strip():
            ax.set_title(titulo.strip(), color=_TEXT, fontsize=14,
                         fontweight="bold", pad=14)

        # Legenda quando há séries nomeadas (não em pizza/heatmap/dispersão).
        if tipo_norm in {"barras", "linha", "area", "histograma"} and (
                len(series) > 1 or (series and series[0]["nome"])):
            leg = ax.legend(frameon=False, fontsize=10, labelcolor=_TEXT)
            if leg:
                leg.get_frame().set_alpha(0)

        fig.tight_layout()
        b64 = _fig_to_b64(fig)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        return _err(f"Falha ao renderizar o gráfico: {e}")

    titulo_final = (titulo or "").strip() or _TIPOS[tipo_norm]
    payload = {
        "ok": True,
        "tipo": tipo_norm,
        "titulo": titulo_final,
        "n_categorias": len(categorias) if categorias else None,
        "n_series": len(series) if series else None,
    }
    publish_attachment(_session, {
        "kind": "chart",
        "image": f"data:image/png;base64,{b64}",
        "chart_type": tipo_norm,
        **payload,
    })
    return json.dumps(payload, ensure_ascii=False)
