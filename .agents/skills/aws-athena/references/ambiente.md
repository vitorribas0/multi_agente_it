# Convenções do ambiente Athena

- Database padrão conhecido: `database_rt2`.
- Base FQ conhecida no sistema legado: `database_rt2.RT2_AI6_OCORRENCIA_FQ_001`.
- Para outra base, qualifique explicitamente `database.tabela`.
- Datas `anomesdia` costumam ser texto `YYYYMMDD`; valide o tipo antes e use `LIKE 'YYYYMM%'` ou `BETWEEN 'YYYYMMDD' AND 'YYYYMMDD'` quando aplicável.
- A integração existente está em `tools/consulta_aws.py` e `tools/descrever_tabela.py`; conexão, workgroup, região, proxy e CA são responsabilidade do backend.
- Quando a tabela for desconhecida, inspecione o Glue/schema e uma prévia pequena antes da query analítica.
