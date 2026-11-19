# Recursos Recebidos

Consulta pública de instrumentos e repasses da União e do Estado para São Bernardo do Campo.

## Fontes

- **Federal.** As propostas são consultadas na API de Discricionárias e Legais do Transferegov. A rotina usa os arquivos oficiais do mesmo portal para instrumentos, desembolsos, empenhos, emendas, aditivos, prorrogações e metas. Se a consulta de propostas falhar ou vier incompleta, a rotina recorre ao arquivo oficial de propostas do mesmo portal e informa isso na cobertura. A API Fundo a Fundo fornece planos de ação autorizados, com parcela de emenda igual a zero. Os valores dos planos são previstos e não entram no total desembolsado.
- **Estadual.** Os convênios e seus valores total e concedido vêm do Portal da Transparência SP. A fonte não distingue a origem parlamentar, indicada como “Não identificada”. CNPJs são cruzados com as unidades municipais identificadas. Quando a Prefeitura consta pelo nome oficial e o CNPJ está em branco, o registro mantém o CNPJ vazio. As quotas-partes da Sefaz-SP são consolidadas por competência desde 2020. Cada linha mantém a modalidade e a base de cálculo publicada. O IPVA tem base bruta e o ICMS e o IPI Exportação têm base líquida do FUNDEB.
- Cada aba tem sua consulta e seu Motor Excel. Filtros, colunas e prévia pertencem à aba e à tabela selecionadas.

## Operação

Execute `python3 scripts/atualizar.py` para gerar `public/dados.json`. Na análise de um extrato local use `--archives <diretório>` com os arquivos ZIP já conferidos. Sem essa opção, a rotina usa um diretório temporário isolado e valida a versão do catálogo oficial no começo e no fim da carga.

O GitHub Actions executa a atualização diariamente, às 11h23 em São Paulo. Apenas a rotina de `.github/workflows/atualizar.yml` na branch `main` pode publicar a carga. O Worker verifica o token OIDC assinado pelo GitHub, a audiência, os IDs fixos do repositório e proprietário, a origem do disparo e a validade da credencial. A rotina preserva o último extrato disponível quando uma carga falha.

Execute `npm run check` e `node scripts/build-worker.mjs` para empacotar os arquivos do portal. O deploy usa `worker.deploy.mjs` como módulo principal. Vincule `SNAPSHOTS` a um namespace KV exclusivo do portal e `PUBLIC_LIMIT` e `SYNC_LIMIT` a limitadores distintos por IP. O domínio de produção é `recursosrecebidos.sigpc.com.br`.

A consulta usa somente dados públicos dos concedentes. O Motor Excel gera o arquivo no navegador, preserva CNPJ como texto, mantém valores monetários como números e não executa fórmulas a partir de campos textuais vindos das fontes.
