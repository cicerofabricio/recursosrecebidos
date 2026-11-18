# Recursos Recebidos

Consulta pública de instrumentos e repasses da União e do Estado para São Bernardo do Campo.

## Fontes

- Transferegov.br. A rotina cruza os arquivos oficiais de propostas, instrumentos, emendas, empenhos, desembolsos, aditivos, prorrogações e metas. Filtra a administração pública municipal pelo município e pela natureza jurídica. Propostas que constam da base de emendas e instrumentos com emenda identificada em empenho ficam fora. O valor de cada desembolso é mantido em centavos. Os totais pactuados e desembolsados do cadastro são apresentados separadamente.
- Sefaz-SP. Quotas-partes estaduais consolidadas por competência desde 2020. Cada linha mantém a modalidade e a base de cálculo publicada. A linha Total e os componentes zerados da fonte ficam fora da captura. IPVA tem base bruta e ICMS e IPI Exportação têm base líquida do FUNDEB na origem.
- Portal da Transparência de SP. A consulta de convênios é verificada separadamente. O conjunto consultado não fornece classificação suficiente para retirar emendas com segurança. Candidatos ficam fora dos registros e dos totais publicados. A página Fontes e cobertura também dá acesso às fontes complementares ainda sem integração.

## Operação

Execute `python3 scripts/atualizar.py` para gerar `public/dados.json`. Na análise de um extrato local use `--archives <diretório>` com os oito arquivos ZIP já conferidos. Sem essa opção, a rotina usa um diretório temporário isolado e valida a versão do catálogo oficial no começo e no fim da carga.

O GitHub Actions executa a atualização diariamente, às 11h23 em São Paulo. Apenas a rotina de `.github/workflows/atualizar.yml` na branch `main` pode publicar a carga. O Worker verifica o token OIDC assinado pelo GitHub, a audiência, os IDs fixos do repositório e proprietário, a origem do disparo e a validade da credencial. A rotina preserva o último extrato disponível quando uma carga falha.

Execute `npm run check` e `node scripts/build-worker.mjs` para empacotar os arquivos do portal. O deploy usa `worker.deploy.mjs` como módulo principal. Vincule `SNAPSHOTS` a um namespace KV exclusivo do portal e `PUBLIC_LIMIT` e `SYNC_LIMIT` a limitadores distintos por IP. O domínio de produção é `recursosrecebidos.sigpc.com.br`.

A consulta usa somente dados públicos dos concedentes. O Motor Excel gera o arquivo no navegador, preserva CNPJ como texto, mantém valores monetários como números e não executa fórmulas a partir de campos textuais vindos das fontes.
