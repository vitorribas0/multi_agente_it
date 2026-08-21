# Segurança de SQL

Permita somente consultas de leitura iniciadas por `SELECT` ou `WITH`. Bloqueie múltiplas instruções e palavras de alteração, incluindo `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `MSCK`, `REPAIR`, `GRANT`, `REVOKE`, `CALL`, `UNLOAD` e `VACUUM`.

Use projeção explícita em vez de `SELECT *` para resultados finais. Em exploração, limite linhas e evite varreduras sem filtro. Não inclua chaves, tokens ou segredos em SQL, logs ou respostas.
