# Trava de análise massiva por LLM

Antes de qualquer classificação ou extração que faça uma chamada de modelo por linha:

1. Informe quantidade de linhas, coluna de entrada, colunas de saída, critério, modelo e modo.
2. Para até cerca de 500 linhas, proponha modo síncrono; para volumes maiores, recomende batch e explique latência/recuperação.
3. Diga explicitamente que serão aproximadamente N chamadas com custo.
4. Peça confirmação explícita.
5. Só execute depois do aceite; sem aceite, entregue apenas o plano.

Ao concluir, reporte falhas parciais e confira se o dataset exportado contém o shape e as colunas esperadas.
