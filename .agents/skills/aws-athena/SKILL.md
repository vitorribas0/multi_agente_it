---
name: aws-athena
description: "Orienta consultas seguras e rastreáveis no AWS Athena e catálogo Glue do ambiente de auditoria. Use para descobrir tabelas e schemas, escrever ou revisar SQL, consultar reclamações/FQ e diagnosticar conexão, proxy ou workgroup."
---

# Aws Athena

1. Descubra database, tabela, colunas e tipos antes de escrever a consulta; nunca suponha schema.
2. Gere apenas `SELECT` ou `WITH ... SELECT`. Recuse DDL, DML, CTAS, UNLOAD e alterações de catálogo.
3. Aplique filtro de período e `LIMIT` exploratório sempre que possível.
4. Mostre a SQL final e descreva database, tabela, filtros e limite.
5. Só diga que consultou o Athena após receber resultado real da ferramenta autorizada.
6. Nunca leia nem exiba credenciais da `.env`; a autenticação pertence ao processo controlado do backend.

Leia [ambiente.md](references/ambiente.md) para as convenções locais e [seguranca-sql.md](references/seguranca-sql.md) antes de executar ou revisar SQL.
