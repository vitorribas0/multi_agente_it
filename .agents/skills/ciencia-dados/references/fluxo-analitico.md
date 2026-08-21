# Fluxo analítico

## Análise tabular

Inspecione schema e qualidade, decomponha a pergunta, execute transformações reproduzíveis e faça sanity checks. Confirme que contagens não excedem a população e que percentuais fecham com o denominador declarado.

## Clustering e anomalias

- Remova IDs, anos usados como códigos, chaves e features quase constantes.
- Padronize variáveis e documente linhas descartadas por nulos.
- Compare K por cotovelo, silhueta, Davies-Bouldin e Calinski-Harabasz.
- Use K-Means para grupos aproximadamente esféricos, aglomerativo para estabilidade/estrutura hierárquica e DBSCAN para densidade.
- Para anomalias, avalie Isolation Forest, LOF, z-score ou IQR conforme distribuição e objetivo.
- Reporte features, parâmetros, tamanho dos grupos, métricas e limitações.

## Visualização

Use barras para ranking, linha para evolução, dispersão para relações, histograma/boxplot para distribuição e heatmap para matrizes. Todo gráfico deve conter título, unidade, período, fonte e denominador quando aplicável.
