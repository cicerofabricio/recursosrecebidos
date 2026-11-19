"""Baixa fontes primárias e publica uma projeção pública de São Bernardo do Campo."""
import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from tributos import state_quotas

TRANSFEREGOV = 'https://api-publica.transferegov.gestao.gov.br/'
BASE = TRANSFEREGOV + 'downloads/dadosgov/'
STATE = 'https://www.transparencia.sp.gov.br/Convenios'
STATE_AMENDMENTS = 'https://www.transparencia.sp.gov.br/EmendasParlamentares'
STATE_AMENDMENTS_CSV = 'https://www.transparencia.sp.gov.br/Dados%20Abertos/Emendas/emendas.zip'
PUBLIC_SNAPSHOT = 'https://recursosrecebidos.sigpc.com.br/api/dados'
SPECIAL_SOURCE = 'Transferegov.br · Transferências Especiais'
MUNICIPAL_NAMES = {
    'PREFEITURA MUNICIPAL DE SAO BERNARDO DO CAMPO', 'MUNICIPIO DE SAO BERNARDO DO CAMPO',
    'FUNDO MUNICIPAL DE SAUDE DE SAO BERNARDO DO CAMPO',
}

IBGE = '3548708'
CITY = 'SAO BERNARDO DO CAMPO'
CITY_ID = '46523239000147'
REQUEST_HEADERS = {'User-Agent': 'RecursosRecebidosSBC/1.0', 'Accept': '*/*'}
FILES = ('proposta', 'convenio', 'emenda', 'desembolso', 'empenho', 'termo_aditivo', 'prorroga_oficio', 'meta_crono_fisico')

def request(url, body=None, referer=STATE, *, timeout=25, attempts=3):
    headers = dict(REQUEST_HEADERS)
    if body is not None:
        headers.update({'Content-Type': 'application/json', 'Referer': referer, 'X-Requested-With': 'XMLHttpRequest'})
    query = urllib.request.Request(url, data=body, headers=headers)
    for attempt in range(attempts):
        try:
            return urllib.request.urlopen(query, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)

def download(directory, name):
    file = directory / f'{name}.zip'
    if file.exists():
        return
    with request(f'{BASE}siconv_{name}.zip') as response, file.with_suffix('.part').open('wb') as out:
        shutil.copyfileobj(response, out)
    file.with_suffix('.part').replace(file)

def rows(directory, name, required, fast_key=None):
    with zipfile.ZipFile(directory / f'{name}.zip') as archive:
        csv_names = [path for path in archive.namelist() if path.lower().endswith('.csv')]
        if len(csv_names) != 1:
            raise ValueError(f'Leiaute ZIP inesperado em {name}')
        with archive.open(csv_names[0]) as entry:
            reader = csv.DictReader(io.TextIOWrapper(entry, encoding='utf-8-sig', newline=''), delimiter=';')
            if not set(required).issubset(reader.fieldnames or []):
                raise ValueError(f'Cabeçalho inesperado em {name}')
            for row in reader:
                if not fast_key or row.get(fast_key[0]) in fast_key[1]:
                    yield row

def clean(value):
    return ' '.join(str(value).split()) if isinstance(value, (str, int, float)) else ''

def plain(s):
    return ''.join(c for c in unicodedata.normalize('NFD', clean(s).upper()) if not unicodedata.combining(c))

def date(value):
    value = clean(value)
    try:
        if '/' in value:
            return dt.datetime.strptime(value, '%d/%m/%Y').date().isoformat()
        return dt.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ''

def amount(value):
    if value is None or value == '':
        return None
    try:
        parsed = Decimal(str(value).replace(',', '.'))
        return int((parsed * 100).quantize(Decimal(1))) if parsed.is_finite() else None
    except InvalidOperation:
        return None

def format_brl(cents):
    return f'R$ {cents / 100:,.2f}'.translate(str.maketrans(',.', '.,')) if cents is not None else ''

def public_text(value, limit=3500):
    s = clean(value)[:limit]
    s = re.sub(r'(?<!\d)(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})(?!\d)', '[dado pessoal suprimido]', s)
    return re.sub(r'(?i)[\w.+-]+@[\w.-]+\.[a-z]{2,}', '[contato suprimido]', s)

def municipal_proposal(p):
    nature = plain(p['NATUREZA_JURIDICA'])
    name = plain(p['NM_PROPONENTE'])
    cnpj = p['IDENTIF_PROPONENTE']
    return re.fullmatch(r'\d{14}', cnpj or '') and (
        cnpj == CITY_ID or ('MUNICIPAL' in nature and 'CONSORCIO' not in nature)
        or (CITY in name and name.startswith(('FUNDO MUNICIPAL', 'CAMARA MUNICIPAL', 'PREFEITURA MUNICIPAL', 'MUNICIPIO DE')))
    )

def cnpj(value):
    return re.sub(r'\D', '', str(value or ''))

def api_json(path, query=None):
    url = TRANSFEREGOV + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    with request(url) as response:
        payload = response.read(8_000_001)
    if len(payload) > 8_000_000:
        raise ValueError('Resposta do Transferegov maior que o limite da consulta')
    return json.loads(payload)

def api_pages(path, **filters):
    count = None
    result = []
    for page in range(1, 31):
        payload = api_json(path, filters | {'pagina':page, 'tamanho_da_pagina':100})
        collection = payload['data']
        total_pages = payload['total_pages']
        total = payload['total_items']
        if (not isinstance(collection, list) or total_pages > 30 or payload['page_number'] != page
                or (count is not None and count != total)):
            raise ValueError('Paginação inconsistente no Transferegov')
        count = total
        result.extend(collection)
        if page >= total_pages:
            if len(result) != count:
                raise ValueError('Consulta incompleta no Transferegov')
            return result
    raise ValueError('Consulta do Transferegov ultrapassou o limite de páginas')

def api_stamp(module):
    raw = api_json(module+'/data-atualizacao')['data_ultima_atualizacao']
    stamp = dt.datetime.fromisoformat(raw)
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=ZoneInfo('America/Sao_Paulo'))).isoformat()

def federal_proposals(directory):
    columns = ('ID_PROPOSTA','COD_MUNIC_IBGE','NATUREZA_JURIDICA','IDENTIF_PROPONENTE',
               'NM_PROPONENTE','NR_PROPOSTA','DESC_ORGAO','DESC_ORGAO_SUP','MODALIDADE','OBJETO_PROPOSTA')
    try:
        stamp = api_stamp('discricionarias')
        by_id = {}
        for code in (IBGE, IBGE[:-1]):
            for row in api_pages('discricionarias/propostas', cod_munic_ibge=code):
                normalized = {column: str(row.get(column.lower()) or '') for column in columns}
                normalized['IDENTIF_PROPONENTE'] = cnpj(row.get('cd_identif_proponente'))
                if normalized['COD_MUNIC_IBGE'] not in {IBGE, IBGE[:-1]}:
                    raise ValueError('A consulta federal retornou outro município')
                if normalized['ID_PROPOSTA'] and municipal_proposal(normalized):
                    by_id[normalized['ID_PROPOSTA']] = normalized
        if not by_id:
            raise ValueError('API sem propostas municipais no recorte')
        return by_id, stamp, 'api'
    except (OSError, ValueError, KeyError, TypeError):
        download(directory, 'proposta')
        by_id = {p['ID_PROPOSTA']:p for p in rows(directory,'proposta',columns)
                 if p['COD_MUNIC_IBGE'] in {IBGE, IBGE[:-1]} and municipal_proposal(p)}
        if not by_id:
            raise ValueError('Nenhuma proposta municipal encontrada no recorte do Transferegov')
        return by_id, None, 'csv'

def federal_fund_plans(municipal_ids):
    result = {}
    for beneficiary in sorted(municipal_ids):
        for plan in api_pages('fundoafundo/planos-acao', cnpj_ente_recebedor_plano_acao=beneficiary):
            if (cnpj(plan['cnpj_ente_recebedor_plano_acao']) != beneficiary
                    or str(plan['codigo_ibge_municipio_ente_recebedor_plano_acao']) != IBGE):
                continue
            if (plan['situacao_plano_acao'] != 'AUTORIZADO' or plan['id_ente_repassador_plano_acao']
                    or not plan['nome_orgao_repassador_plano_acao']):
                continue
            plan_id = str(plan['id_plano_acao'])
            if not re.fullmatch(r'\d{1,12}', plan_id):
                raise ValueError('Identificador inesperado no Transferegov Fundo a Fundo')
            linked_fund = cnpj(plan['cnpj_fundo_vinculado_plano_acao'])
            fund_is_municipal = (len(linked_fund) == 14
                and str(plan['codigo_ibge_fundo_vinculado_plano_acao']) == IBGE
                and 'MUNICIPAL' in plain(plan['nome_fundo_vinculado_plano_acao']))
            begin = date(plan['data_inicio_vigencia_plano_acao'])
            amendment_value = amount(plan['valor_repasse_emenda_plano_acao'])
            result[plan_id] = {
                'id':f'faf-{plan_id}', 'origin':'União', 'source':'Transferegov.br · Fundo a Fundo',
                'number':str(plan['codigo_plano_acao']), 'agency':public_text(plan['nome_orgao_repassador_plano_acao']),
                'recipient':public_text(plan['nome_fundo_vinculado_plano_acao'] if fund_is_municipal else plan['nome_ente_recebedor_plano_acao']),
                'cnpj':linked_fund if fund_is_municipal else beneficiary,'type':'Plano de ação fundo a fundo',
                'object':public_text(plan['objetivos_plano_acao'] or plan['diagnostico_plano_acao']),
                'status':'Autorizado','year':int(begin[:4]) if begin else None,'start':begin,'end':date(plan['data_fim_vigencia_plano_acao']),
                'amendment':'Identificada' if amendment_value and amendment_value > 0 else 'Não identificada',
                'total':amount(plan['valor_total_plano_acao']), 'transfer':amount(plan['valor_total_repasse_plano_acao']), 'paid':None,
                'details':{'Fundo repassador':public_text(plan['nome_fundo_repassador_plano_acao']),
                           'Parcela de emenda prevista': format_brl(amendment_value),
                           'Valores':'Previsão do plano de ação. Desembolso e data de pagamento não foram apurados nesta fonte.'},
                'link':TRANSFEREGOV+'fundoafundo/planos-acao?id_plano_acao='+plan_id,
            }
    return list(result.values()), api_stamp('fundoafundo')

def special_plans(municipal_ids):
    def lookup(endpoint, key, key_value):
        matches = api_pages(f'especiais/{endpoint}-especiais', **{key:key_value})
        if any(str(row[key]) != str(key_value) for row in matches):
            raise ValueError(f'Filtro não respeitado na consulta de {endpoint}')
        return matches

    plans = {}
    for doc in sorted(municipal_ids):
        for beneficiary in lookup('beneficiarios','cnpj_beneficiario',doc):
            if beneficiary['uf_beneficiario'] != 'SP':
                continue
            for plan in lookup('planos-acao','id_beneficiario',beneficiary['id_beneficiario']):
                value = (amount(plan['valor_custeio_plano_acao']) or 0) + (amount(plan['valor_investimento_plano_acao']) or 0)
                plan_id = str(plan['id_plano_acao'])
                if not re.fullmatch(r'\d{1,12}', plan_id) or plan_id in plans:
                    raise ValueError('Identificador duplicado ou inesperado em transferências especiais')
                plans[plan_id] = (doc, beneficiary, plan, value)

    def detail(entry):
        plan_id, (doc, beneficiary, plan, value) = entry
        source_link = TRANSFEREGOV+'especiais/planos-acao-especiais?id_plano_acao='+plan_id
        row = {
            'id':'pix-'+plan_id, 'origin':'União', 'source':SPECIAL_SOURCE,
            'number':public_text(plan['codigo_plano_acao'],70), 'agency':'',
            'recipient':public_text(beneficiary['nome_beneficiario'],250),'cnpj':doc,
            'type':'Transferência especial', 'object':public_text(plan.get('detalhamento_objeto') or plan.get('nome_objeto')),
            'status':public_text(plan['situacao_plano_acao'],120), 'year':int(plan['ano_plano_acao']),
            'total':value,'transfer':value,'paid':None,'amendment':'Identificada',
            'amendmentCode':str(plan['numero_emenda_parlamentar_plano_acao'] or ''),
            'author':public_text(plan['nome_parlamentar_emenda_plano_acao'],180),
            'details':{'Aceite':date(plan.get('data_aceite_plano_acao')),
                       'Área de política pública':public_text(plan.get('codigo_descricao_areas_politicas_publicas_plano_acao'),800),
                       'Custeio previsto':format_brl(amount(plan['valor_custeio_plano_acao'])),
                       'Investimento previsto':format_brl(amount(plan['valor_investimento_plano_acao'])),
                       'Valores':'Desembolso identificado pela ordem bancária enviada e pelo valor do documento hábil vinculado.'},
            'sections':{'Empenhos':[], 'Ordens bancárias':[]},
            'link':source_link,
        }
        transactions = []
        for commitment in lookup('empenhos','id_plano_acao',plan_id):
            row['agency'] = public_text(commitment.get('descricao_ug_emitente_empenho'),250)
            row['sections']['Empenhos'].append({'Empenho':public_text(commitment['numero_empenho'],40),
                'Data':date(commitment['data_emissao_empenho']), 'Situação':public_text(commitment['descricao_situacao_empenho'],100),
                'Valor':amount(commitment['valor_empenho'])})
            for document in lookup('documentos-habeis','id_empenho',commitment['id_empenho']):
                orders = lookup('ordens-pagamentos-ordens-bancarias','id_dh',document['id_dh'])
                for order in orders:
                    row['sections']['Ordens bancárias'].append({'Documento hábil':public_text(document['numero_documento_habil'],40),
                        'Ordem bancária':public_text(order['numero_ordem_bancaria'],40),
                        'Data':date(order['data_emissao_ob']), 'Situação':public_text(order['descricao_situacao_op'],160)})
                sent = [o for o in orders if o['situacao_op'] == 5 and o['numero_ordem_bancaria'] and date(o['data_emissao_ob'])]
                paid = amount(document['valor_dh'])
                if len(sent) != 1 or paid is None or document['situacao_dh'] != 3:
                    continue
                order = sent[0]
                day = date(order['data_emissao_ob'])
                transactions.append({
                    'id':f"pix-ob-{int(order['id_op_ob'])}",'instrumentId':row['id'],'origin':'União','source':SPECIAL_SOURCE,
                    'number':row['number'],'agency':row['agency'],'recipient':row['recipient'],'cnpj':doc,
                    'type':row['type'],'object':row['object'],'day':day,'period':day[:7],'year':int(day[:4]),
                    'document':public_text(order['numero_ordem_bancaria'],40),'paid':paid,'link':source_link,
                    'details':{'Documento hábil':public_text(document['numero_documento_habil'],40),
                               'Situação da ordem bancária':public_text(order['descricao_situacao_op'],160)},
                })
        if transactions:
            row['paid'] = sum(t['paid'] for t in transactions)
        return row, transactions

    instruments, payments = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for plan, transactions in pool.map(detail, sorted(plans.items())):
            instruments.append(plan)
            payments.extend(transactions)
    return instruments, payments, api_stamp('especiais')


def federal_partnerships(municipal_ids):
    def lookup(endpoint, key, value):
        matches = api_pages(f'parcerias/{endpoint}', **{key:value})
        if any(str(row[key]) != str(value) for row in matches):
            raise ValueError(f'Filtro não respeitado na consulta de {endpoint}')
        return matches

    proposals = {}
    for doc in sorted(municipal_ids):
        for proposal in lookup('proposta','cnpj_ente_recebedor',doc):
            if str(proposal['cd_ibge_recebedor']) in {IBGE, IBGE[:-1]} and proposal['sg_uf_recebedor'] == 'SP':
                proposals[proposal['id_proposta']] = proposal

    def describe(proposal):
        proposal_id = proposal['id_proposta']
        records = lookup('parceria','id_proposta',proposal_id)
        if not records:
            return [], []
        transfers = lookup('distribuicao-recurso-proposta','id_proposta',proposal_id)
        amendments = [{'Emenda':public_text(r.get('nr_emenda_proposta'),50),
                       'Autoria':public_text(r.get('nm_parlamentar_proposta'),180), 'Valor':amount(r['valor_emenda'])}
                       for r in transfers if r.get('nr_emenda_proposta')]
        instruments, payments = [], []
        doc = proposal['cnpj_ente_recebedor']
        for record in records:
            partnership_id = str(record['id_parceria'])
            if not re.fullmatch(r'\d{1,12}', partnership_id):
                raise ValueError('Identificador inesperado na Gestão de Parcerias')
            row = {
                'id':'parceria-'+partnership_id, 'origin':'União', 'source':'Transferegov.br · Gestão de Parcerias',
                'number':str(record.get('cd_parceria') or partnership_id),
                'agency':public_text(proposal.get('nm_unidade_gestora'),250),
                'recipient':public_text(proposal['nm_ente_recebedor'],250), 'cnpj':doc,
                'type':'Parceria federal', 'object':public_text(proposal.get('ds_objeto')),
                'status':public_text(record['in_situacao_parceria'],120), 'signed':date(record['dh_assinatura']),
                'year':int(proposal['ano_proposta']), 'proposal':str(proposal_id),
                'process':public_text(str(record.get('nu_externo') or record.get('cd_processo_sei') or ''),80),
                'total':amount(proposal['nr_vlr_total']), 'transfer':amount(proposal['nr_vlr_total']), 'paid':None,
                'amendment':'Identificada' if amendments else 'Não identificada',
                'amendmentCode':' · '.join(dict.fromkeys(a['Emenda'] for a in amendments)),
                'author':' · '.join(dict.fromkeys(a['Autoria'] for a in amendments if a['Autoria'])),
                'details':{'Origem do registro':public_text(record['tp_origem'],120),
                           'Valores':'O desembolso soma ordens pagas que constam no módulo Gestão de Parcerias.'},
                'sections':{'Emendas vinculadas':amendments}, 'link':TRANSFEREGOV+'parcerias/parceria?id_parceria='+partnership_id,
            }
            transactions = []
            for document in lookup('documento-habil','id_parceria',partnership_id):
                if cnpj(document['cd_credor_devedor']) != doc:
                    continue
                for order in lookup('ordem-pagamento','id_documento_habil',document['id_documento_habil']):
                    day = date(order['dt_emissao_ordem_bancaria'])
                    paid = amount(order['vl_ordem_pagamento'])
                    if plain(order['in_situacao_op']) != 'PAGA' or not day or not order['nr_ordem_bancaria'] or paid is None:
                        continue
                    transactions.append({
                        'id':f"parceria-ob-{int(order['id_op'])}", 'instrumentId':row['id'], 'origin':'União',
                        'source':row['source'], 'number':row['number'], 'agency':row['agency'], 'recipient':row['recipient'],
                        'cnpj':doc, 'type':row['type'], 'object':row['object'], 'day':day,'period':day[:7],'year':int(day[:4]),
                        'document':public_text(order['nr_ordem_bancaria'],40),'paid':paid,'link':row['link'],
                        'details':{'Documento hábil':public_text(document['nr_documento_habil'],40)},
                    })
            if transactions:
                row['paid'] = sum(t['paid'] for t in transactions)
            instruments.append(row)
            payments.extend(transactions)
        return instruments, payments

    instruments, payments = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for records, transactions in pool.map(describe, proposals.values()):
            instruments.extend(records)
            payments.extend(transactions)
    return instruments, payments, api_stamp('parcerias')


def state_agreements(municipal_ids):
    def query(key, value):
        found = []
        for page in range(1, 6):
            body = {'page':page, 'pageSize':100, 'totalItens':0, key:value}
            with request(STATE + '/Buscar', json.dumps(body).encode(), timeout=12, attempts=2) as res:
                response = json.load(res)
            found.extend(response['items'])
            if not response.get('hasNext'):
                if len(found) != response['totalItems']:
                    raise ValueError('Consulta de convênios estaduais incompleta')
                return found, response['ultimaAtualizacao']
        raise ValueError('A consulta estadual ultrapassou o limite de páginas')
    args = [('nomeBeneficiario', 'BERNARDO DO CAMPO'), *[('cnpjBeneficiario', doc) for doc in sorted(municipal_ids)]]
    agreements = {}
    source_date = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for found, stamp in pool.map(lambda arg:query(*arg), args):
            if source_date and stamp != source_date:
                raise ValueError('Fonte estadual mudou durante a consulta')
            source_date = stamp
            for row in found:
                doc = cnpj(row.get('cnpjBeneficiario'))
                name = clean(row.get('nomeBeneficiario'))
                if doc not in municipal_ids and (doc or plain(name) not in MUNICIPAL_NAMES):
                    continue
                number = str(row['numeroConvenio'])
                if not re.fullmatch(r'\d{6,16}',number):
                    raise ValueError('Identificador de convênio estadual inesperado')
                candidate = {
                    'id':f'sp-convenio-{number}','origin':'Estado','source':'Portal da Transparência SP','number':number,
                    'agency':public_text(row['orgaoCelebrante']),'agencyUnit':public_text(row['unidadeCelebrante']),
                    'recipient':name,'cnpj':doc,'type':public_text(row['tipoInstrumentoJuridico']),'object':public_text(row['objeto']),
                    'year':int(row['anoCelebracao']),'start':date(row['dtInicioVigenciaProxy']),'end':date(row['dtFimVigenciaProxy']),
                    'total':amount(row['vlRecebidoTotal']),'granted':amount(row['vlLiquidadoTotal']),
                    'amendment':'Não identificada','details':{},
                    'link':row['linkDocumentoProxy'] if (row['linkDocumentoProxy'] or '').startswith(('https://demandas.spsempapel.sp.gov.br/','https://www.transparencia.sp.gov.br/')) else STATE,
                }
                if not doc:
                    candidate['details']['Identificação da unidade'] = 'A fonte identifica o Município pelo nome e não preenche o CNPJ neste registro.'
                previous = agreements.get(number)
                if previous and (previous['total'],previous['granted']) != (candidate['total'],candidate['granted']):
                    raise ValueError(f'Convênio estadual {number} com valores divergentes')
                if not previous or (doc and not previous['cnpj']):
                    agreements[number] = candidate
    return list(agreements.values()), source_date

def state_agreements_or_previous(municipal_ids):
    try:
        records, stamp = state_agreements(municipal_ids)
        if not records:
            raise ValueError('A consulta estadual retornou zero convênios')
        return records, stamp, 'api'
    except (OSError, ValueError, KeyError) as error:
        print(f'Consulta de convênios estaduais indisponível ({type(error).__name__}); preservando a lista publicada.', flush=True)
        with request(PUBLIC_SNAPSHOT, timeout=20, attempts=2) as response:
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError('A carga anterior ultrapassou o limite')
        previous = json.loads(raw)
        kept = []
        for row in previous['instruments']:
            if row['origin'] != 'Estado' or row['source'] != 'Portal da Transparência SP':
                continue
            if row['cnpj'] not in municipal_ids and (row['cnpj'] or plain(row['recipient']) not in MUNICIPAL_NAMES):
                continue
            row['amendment'] = 'Não identificada'
            row.pop('amendmentCode', None)
            row.pop('sections', None)
            kept.append(row)
        stamp = previous['coverage']['stateUpdatedAt']
        if not kept or not stamp:
            raise ValueError('A carga anterior não contém convênios estaduais utilizáveis')
        return kept, stamp, 'previous'


def state_amendment_archive():
    with request(STATE_AMENDMENTS_CSV) as response:
        raw = response.read(12_000_001)
    if len(raw) > 12_000_000:
        raise ValueError('Arquivo estadual excede o limite de download')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = [entry for entry in archive.infolist() if entry.filename.lower().endswith('.csv')]
        if len(files) != 1 or files[0].file_size > 80_000_000:
            raise ValueError('Leiaute inesperado no arquivo de emendas estaduais')
        stamp = dt.datetime(*files[0].date_time,tzinfo=ZoneInfo('America/Sao_Paulo')).isoformat()
        with archive.open(files[0]) as file:
            reader = csv.reader(io.TextIOWrapper(file,encoding='utf-8-sig',newline=''),delimiter=';')
            header = [plain(title) for title in next(reader)]
            layout = [
                'FILTRO - TIPO DE CONSULTA','ANO DE REFERÊNCIA','ÓRGÃO OU ENTIDADE EXECUTORA','ORIGEM DOS RECURSOS',
                'NÚMERO DA EMENDA','STATUS DA EMENDA','TIPO DE EMENDA','FORMA DE REPASSE','AUTORIA','PARTIDO POLÍTICO',
                'NÚMERO DO INSTRUMENTO JURÍDICO','BENEFICIÁRIO','OBJETO','TIPO DE DESPESA','FUNÇÃO DE GOVERNO',
                'LOCALIZAÇÃO DO GASTO','VALOR TOTAL DO PLANO DE AÇÃO','VALOR PREVISTO','VALOR EMPENHADO',
                'VALOR LIQUIDADO','VALOR PAGO','VALOR LIBERADO','VALOR CONTRAPARTIDA','PLANO DE TRABALHO',
                'NOTA DE EMPENHO','NOTA DE LIQUIDAÇÃO','NOTA DE PAGAMENTO','CNPJ DO BENEFICIÁRIO',
            ]
            if len(header) != len(layout) or any(
                    not re.fullmatch(re.escape(actual).replace('�','.'), plain(expected))
                    for actual, expected in zip(header, layout)):
                raise ValueError('Cabeçalho inesperado no arquivo de emendas estaduais')
            result = []
            for record in reader:
                if len(record) != len(layout):
                    raise ValueError('Linha inválida no arquivo de emendas estaduais')
                r = [None if item == '-' else item for item in record]
                if r[0] != 'Concedidas':
                    continue
                result.append({
                    'anoReferencia':r[1], 'orgaoEntidadeExecutora':r[2], 'numeroEmenda':r[4], 'estagioAtualDescricao':r[5],
                    'tipoEmendaDescricao':r[6], 'formaRepasse':r[7], 'autoria':r[8], 'partidoPolitico':r[9],
                    'numeroInstrumentoJuridico':r[10], 'beneficiario':r[11], 'objeto':r[12], 'funcaoGoverno':r[14],
                    'valorPrevisto':r[17].replace('.','') if r[17] else None,
                    'valorEmpenhado':r[18].replace('.','') if r[18] else None,
                    'valorLiquidado':r[19].replace('.','') if r[19] else None,
                    'valorPago':r[20].replace('.','') if r[20] else None,
                    'notaEmpenho':r[24], 'notaPagamento':r[26], 'cnpjBeneficiario':r[27],
                })
    return result, stamp


def state_amendments(agreements, municipal_ids):
    units = {row['cnpj']:row['recipient'] for row in agreements if row['cnpj']}
    units[CITY_ID] = 'PREFEITURA MUNICIPAL DE SAO BERNARDO DO CAMPO'

    def fetch(field, value):
        found = []
        stamp = None
        for page in range(1, 11):
            query = {'tipoConcedidaRealizada':'Concedidas', 'page':page, 'pageSize':100, 'totalItens':0, field:value}
            with request(STATE_AMENDMENTS+'/Buscar', json.dumps(query).encode(), STATE_AMENDMENTS+'/Concedidas', timeout=12, attempts=2) as response:
                result = json.load(response)
            if stamp and stamp != result['ultimaAtualizacao']:
                raise ValueError('Fonte de emendas estaduais mudou durante a consulta')
            stamp = result['ultimaAtualizacao']
            found.extend(result['items'])
            if not result.get('hasNext'):
                if len(found) != result['totalItems']:
                    raise ValueError('Consulta de emendas estaduais incompleta')
                return found, stamp
        raise ValueError('Consulta de emendas estaduais ultrapassou o limite de páginas')

    seen = {}
    source_date = None
    by_number = {row['number']:row for row in agreements}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            batches = list(pool.map(lambda arg:fetch(*arg), [
                ('beneficiario','BERNARDO DO CAMPO'), ('localizacaoGasto','SÃO BERNARDO DO CAMPO')]))
        mode = 'api'
    except (OSError, ValueError, KeyError) as error:
        print(f'Consulta de emendas estaduais indisponível ({type(error).__name__}); usando o CSV oficial.', flush=True)
        batches = [state_amendment_archive()]
        mode = 'csv'
    for entries, stamp in batches:
        if source_date and source_date != stamp:
            raise ValueError('Fonte de emendas estaduais mudou entre os filtros')
        source_date = stamp
        for row in entries:
            doc = cnpj(row.get('cnpjBeneficiario'))
            name = clean(row.get('beneficiario'))
            official_name = plain(name)
            if doc not in municipal_ids and (doc or official_name not in MUNICIPAL_NAMES):
                continue
            code = str(row.get('numeroEmenda') or '')
            if not re.fullmatch(r'\d{4}\.A?\d{1,10}\.\d{1,12}', code):
                raise ValueError('Número de emenda estadual inesperado')
            number = public_text(row.get('numeroInstrumentoJuridico'),80)
            effective_doc = doc or (CITY_ID if official_name in {units[CITY_ID], 'MUNICIPIO DE '+CITY} else '13961905000170')
            unique = (code, effective_doc, number, clean(row.get('orgaoEntidadeExecutora')))
            row_id = 'sp-emenda-' + hashlib.sha256('|'.join(unique).encode()).hexdigest()[:24]
            item = {
                'id':row_id, 'origin':'Estado', 'source':'Portal da Transparência SP · Emendas',
                'amendmentCode':code, 'number':code, 'relatedNumber':number,
                'author':public_text(row.get('autoria'),200), 'recipient':public_text(name or units.get(effective_doc)),
                'cnpj':doc, 'agency':public_text(row.get('orgaoEntidadeExecutora'),250),
                'type':public_text(row.get('formaRepasse') or row.get('tipoEmendaDescricao') or row.get('tipoEmenda'),120),
                'object':public_text(row.get('objeto')), 'status':public_text(row.get('estagioAtualDescricao'),120),
                'year':int(row['anoReferencia'] or code[:4]), 'total':amount(row['valorPrevisto']), 'granted':amount(row['valorPago']),
                'details':{'Partido':public_text(row.get('partidoPolitico'),100), 'Função de governo':public_text(row.get('funcaoGoverno'),200),
                           'Valor empenhado':format_brl(amount(row.get('valorEmpenhado'))),
                           'Valor liquidado':format_brl(amount(row.get('valorLiquidado'))),
                           'Nota de empenho':public_text(row.get('notaEmpenho'),500),
                           'Nota de pagamento':public_text(row.get('notaPagamento'),500)},
                'link':STATE_AMENDMENTS+'/Concedidas',
            }
            agreement = by_number.get(number)
            same_name = agreement and plain(agreement['recipient']) == official_name
            if agreement and ((doc and doc == agreement['cnpj']) or (same_name and (not doc or not agreement['cnpj']))):
                item['instrumentId'] = agreement['id']
            previous = seen.get(row_id)
            if previous and (previous['total'], previous['granted']) != (item['total'], item['granted']):
                raise ValueError(f'Emenda estadual {code} retornou valores divergentes')
            if not previous or (doc and not previous['cnpj']):
                seen[row_id] = item
    if not seen:
        raise ValueError('A consulta de emendas estaduais não retornou beneficiários municipais')
    for item in seen.values():
        if item.get('instrumentId'):
            agreement = by_number[item['relatedNumber']]
            agreement['amendment'] = 'Identificada'
            section = agreement.setdefault('sections', {}).setdefault('Emendas vinculadas', [])
            section.append({'Emenda':item['amendmentCode'], 'Autoria':item['author'], 'Valor':item['granted']})
            agreement['amendmentCode'] = ' · '.join(dict.fromkeys(r['Emenda'] for r in section))
    return list(seen.values()), source_date, mode


def build(directory, proposals):
    municipal_ids = {p['IDENTIF_PROPONENTE'] for p in proposals.values()} | {CITY_ID, '13961905000170'}
    linked = {}
    for e in rows(directory, 'emenda', ['ID_PROPOSTA', 'NR_EMENDA', 'NOME_PARLAMENTAR', 'VALOR_REPASSE_PROPOSTA_EMENDA'], ('ID_PROPOSTA', proposals)):
        item = {'Emenda':public_text(e['NR_EMENDA'],50), 'Autoria':public_text(e['NOME_PARLAMENTAR'],180),
                'Valor':amount(e['VALOR_REPASSE_PROPOSTA_EMENDA'])}
        group = linked.setdefault(e['ID_PROPOSTA'], [])
        if item not in group:
            group.append(item)
    candidates = {}
    for c in rows(directory, 'convenio', ['NR_CONVENIO', 'ID_PROPOSTA', 'VL_DESEMBOLSADO_CONV'], ('ID_PROPOSTA', proposals)):
        if c['NR_CONVENIO']:
            candidates[c['NR_CONVENIO']] = c
    budget_amendment = set()
    notes = {}
    for e in rows(directory, 'empenho', ['NR_CONVENIO', 'NR_EMPENHO', 'DESCRICAO_EMENDA_SIAFI', 'RESULTADO_PRIMARIO'], ('NR_CONVENIO', candidates)):
        k = e['NR_CONVENIO']
        if plain(e['DESCRICAO_EMENDA_SIAFI']) not in {'', 'NAO INFORMADO', 'NAO SE APLICA', '-'} or e['RESULTADO_PRIMARIO'] in {'6','7','8','9'}:
            budget_amendment.add(k)
        notes.setdefault(k, []).append({'Empenho': clean(e['NR_EMPENHO']), 'Tipo': clean(e['DESC_TIPO_NOTA']), 'Data de emissão': date(e['DATA_EMISSAO']), 'Situação': clean(e['DESC_SITUACAO_EMPENHO']), 'Valor': amount(e['VALOR_EMPENHO'])})
    instruments = {}
    for number, c in candidates.items():
        amendments = linked.get(c['ID_PROPOSTA'], [])
        p = proposals[c['ID_PROPOSTA']]
        key = f'tg-{number}'
        instruments[number] = {
            'id': key, 'origin':'União', 'source':'Transferegov.br', 'number': number,
            'agency': public_text(p['DESC_ORGAO']), 'recipient':public_text(p['NM_PROPONENTE']), 'cnpj':p['IDENTIF_PROPONENTE'],
            'type':clean(p['MODALIDADE']), 'object':public_text(p['OBJETO_PROPOSTA']),
            'status':clean(c['SIT_CONVENIO']), 'year':int(c['ANO']) if c['ANO'].isdigit() else None,
            'signed':date(c['DIA_ASSIN_CONV']), 'start':date(c['DIA_INIC_VIGENC_CONV']), 'end':date(c['DIA_FIM_VIGENC_CONV']),
            'total':amount(c['VL_GLOBAL_CONV']), 'transfer':amount(c['VL_REPASSE_CONV']), 'counterpart':amount(c['VL_CONTRAPARTIDA_CONV']), 'paid':amount(c['VL_DESEMBOLSADO_CONV']),
            'proposal':clean(p['NR_PROPOSTA']), 'process':clean(c['NR_PROCESSO']),
            'amendment':'Identificada' if amendments or number in budget_amendment else 'Não identificada',
            'amendmentCode':' · '.join(dict.fromkeys(a['Emenda'] for a in amendments if a['Emenda'])),
            'author':' · '.join(dict.fromkeys(a['Autoria'] for a in amendments if a['Autoria'])),
            'details':{'Órgão superior':public_text(p['DESC_ORGAO_SUP']), 'Publicação':date(c['DIA_PUBL_CONV']),
                       'Situação da publicação':clean(c['SITUACAO_PUBLICACAO']), 'Vigência final original':date(c['DIA_FIM_VIGENC_ORIGINAL_CONV']),
                       'Prazo da prestação de contas':date(c['DIA_LIMITE_PREST_CONTAS']), 'Proposta':clean(p['NR_PROPOSTA']),
                       'Situação da contratação':clean(c['SITUACAO_CONTRATACAO']), 'UG emitente':clean(c['UG_EMITENTE']),
                       'Processo':clean(c['NR_PROCESSO'])},
            'sections': {'Emendas vinculadas': amendments, 'Empenhos': notes.get(number, []), 'Aditivos':[], 'Prorrogações':[], 'Metas':[]},
            'link': f'https://portaldatransparencia.gov.br/convenios/{number}',
        }
    if not instruments:
        raise ValueError('A carga federal não retornou instrumentos')
    supplemental = {
        'termo_aditivo':('Aditivos', {'Aditivo':'NUMERO_TA','Tipo':'TIPO_TA','Assinatura':'DT_ASSINATURA_TA','Início':'DT_INICIO_TA','Fim':'DT_FIM_TA','Valor':'VL_GLOBAL_TA','Justificativa':'JUSTIFICATIVA_TA'}),
        'prorroga_oficio':('Prorrogações', {'Prorrogação':'NR_PRORROGA','Início':'DT_INICIO_PRORROGA','Fim':'DT_FIM_PRORROGA','Assinatura':'DT_ASSINATURA_PRORROGA','Situação':'SIT_PRORROGA'}),
        'meta_crono_fisico':('Metas', {'Meta':'NR_META','Descrição':'DESC_META','Início':'DATA_INICIO_META','Fim':'DATA_FIM_META','Valor':'VL_META'}),
    }
    for filename, (label, fields) in supplemental.items():
        for e in rows(directory, filename, ['NR_CONVENIO', *fields.values()], ('NR_CONVENIO', instruments)):
            instruments[e['NR_CONVENIO']]['sections'][label].append({
                k: amount(e[v]) if v.startswith('VL_') else date(e[v]) if v.startswith(('DT_', 'DATA_')) else public_text(e[v], 1400)
                for k,v in fields.items()
            })
    payments = {}
    for d in rows(directory, 'desembolso', ['ID_DESEMBOLSO', 'NR_CONVENIO', 'DATA_DESEMBOLSO', 'VL_DESEMBOLSADO'], ('NR_CONVENIO', instruments)):
        i = instruments[d['NR_CONVENIO']]
        day, value = date(d['DATA_DESEMBOLSO']), amount(d['VL_DESEMBOLSADO'])
        if not day or value is None:
            continue
        key = f"ob-{d['ID_DESEMBOLSO']}"
        row = {'id':key, 'instrumentId':i['id'], 'origin':i['origin'], 'source':i['source'], 'number':i['number'],
               'agency':i['agency'], 'recipient':i['recipient'], 'cnpj':i['cnpj'], 'object':i['object'], 'type':i['type'],
               'day':day,'period':day[:7],'year':int(day[:4]), 'document':clean(d['NR_SIAFI']), 'paid':value,
               'details':{'UG emitente':clean(d['UG_EMITENTE_DH'])}, 'link':i['link']}
        if key in payments and payments[key] != row:
            raise ValueError(f'Desembolso com identificação divergente: {key}')
        payments[key] = row
    return list(instruments.values()), list(payments.values()), municipal_ids

def metadata():
    with request(BASE + '?restype=container&comp=list') as res:
        root = ET.parse(res)
    expected = {f'siconv_{name}.zip' for name in FILES}
    stamps = {item.findtext('Name'):item.findtext('Properties/Last-Modified') for item in root.findall('.//Blob')}
    if expected - stamps.keys():
        raise ValueError('O catálogo oficial não contém todos os arquivos esperados')
    return {name:stamps[name] for name in sorted(expected)}

def main(directory, output):
    version = metadata()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda name: download(directory, name), (name for name in FILES if name != 'proposta')))
    proposals, proposal_date, proposal_mode = federal_proposals(directory)
    instruments, payments, ids = build(directory, proposals)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        quotas = pool.submit(state_quotas)
        agreed = pool.submit(state_agreements_or_previous, ids)
        funds = pool.submit(federal_fund_plans, ids)
        special = pool.submit(special_plans, ids)
        partnerships = pool.submit(federal_partnerships, ids)
        state_rows, state_date, state_mode = agreed.result()
        amendments, state_amendment_date, state_amendment_mode = state_amendments(state_rows, ids)
        plans, fund_date = funds.result()
        special_rows, special_payments, special_date = special.result()
        partnership_rows, partnership_payments, partnership_date = partnerships.result()
        payments.extend(quotas.result())
        payments.extend(special_payments)
    instruments.extend(state_rows)
    instruments.extend(plans)
    instruments.extend(special_rows)
    instruments.extend(partnership_rows)
    payments.extend(partnership_payments)
    if metadata() != version:
        raise RuntimeError('Arquivos mudaram durante a leitura. Execute uma nova carga.')
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {'schemaVersion':3, 'generatedAt':dt.datetime.now(dt.timezone.utc).isoformat(),
            'coverage':{'federalFiles':version,'federalProposalMode':proposal_mode,'federalProposalUpdatedAt':proposal_date,
                        'federalFundUpdatedAt':fund_date,'stateUpdatedAt':state_date,
                        'stateAmendmentUpdatedAt':state_amendment_date, 'stateAmendmentMode':state_amendment_mode, 'stateMode':state_mode,'federalSpecialUpdatedAt':special_date, 'federalPartnershipUpdatedAt':partnership_date},
            'instruments':sorted(instruments,key=lambda r:(r.get('signed') or r.get('start',''),r['number']),reverse=True),
            'amendments':sorted(amendments,key=lambda r:(r['year'],r['amendmentCode']),reverse=True),
            'payments':sorted(payments,key=lambda r:(r.get('day',r.get('period','')),r['id']),reverse=True)}
    tmp = output.with_suffix('.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(output)
    print(json.dumps({k:len(data[k]) for k in ('instruments','payments','amendments')} | {'stateAgreements':len(state_rows),'fundPlans':len(plans),'proposalMode':proposal_mode}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archives', type=Path)
    parser.add_argument('--output', type=Path, default=Path('public/dados.json'))
    args = parser.parse_args()
    if args.archives:
        main(args.archives, args.output)
    else:
        with tempfile.TemporaryDirectory() as temp:
            main(Path(temp), args.output)
