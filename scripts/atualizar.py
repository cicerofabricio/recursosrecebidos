"""Baixa fontes primárias e publica uma projeção pública de São Bernardo do Campo."""
import argparse
import concurrent.futures
import csv
import datetime as dt
import io
import json
import re
import shutil
import tempfile
import unicodedata
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
IBGE = '3548708'
CITY = 'SAO BERNARDO DO CAMPO'
CITY_ID = '46523239000147'
REQUEST_HEADERS = {'User-Agent': 'RecursosRecebidosSBC/1.0', 'Accept': '*/*'}
FILES = ('proposta', 'convenio', 'emenda', 'desembolso', 'empenho', 'termo_aditivo', 'prorroga_oficio', 'meta_crono_fisico')

def request(url, body=None):
    headers = dict(REQUEST_HEADERS)
    if body is not None:
        headers.update({'Content-Type': 'application/json', 'Referer': STATE, 'X-Requested-With': 'XMLHttpRequest'})
    return urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=45)

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
    return ' '.join((value or '').split())

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
            if amount(plan['valor_repasse_emenda_plano_acao']) != 0:
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
            result[plan_id] = {
                'id':f'faf-{plan_id}', 'origin':'União', 'source':'Transferegov.br · Fundo a Fundo',
                'number':str(plan['codigo_plano_acao']), 'agency':public_text(plan['nome_orgao_repassador_plano_acao']),
                'recipient':public_text(plan['nome_fundo_vinculado_plano_acao'] if fund_is_municipal else plan['nome_ente_recebedor_plano_acao']),
                'cnpj':linked_fund if fund_is_municipal else beneficiary,'type':'Plano de ação fundo a fundo',
                'object':public_text(plan['objetivos_plano_acao'] or plan['diagnostico_plano_acao']),
                'status':'Autorizado','year':int(begin[:4]) if begin else None,'start':begin,'end':date(plan['data_fim_vigencia_plano_acao']),
                'total':amount(plan['valor_total_plano_acao']), 'transfer':amount(plan['valor_total_repasse_plano_acao']), 'paid':None,
                'details':{'Fundo repassador':public_text(plan['nome_fundo_repassador_plano_acao']),
                           'Valores':'Previsão do plano de ação. Desembolso e data de pagamento não foram apurados nesta fonte.'},
                'link':TRANSFEREGOV+'fundoafundo/planos-acao?id_plano_acao='+plan_id,
            }
    return list(result.values()), api_stamp('fundoafundo')

def state_agreements(municipal_ids):
    municipal_names = {'PREFEITURA MUNICIPAL DE '+CITY, 'MUNICIPIO DE '+CITY, 'FUNDO MUNICIPAL DE SAUDE DE '+CITY}
    def query(key, value):
        found = []
        for page in range(1, 6):
            body = {'page':page, 'pageSize':100, 'totalItens':0, key:value}
            with request(STATE + '/Buscar', json.dumps(body).encode()) as res:
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
                if doc not in municipal_ids and (doc or plain(name) not in municipal_names):
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

def build(directory, proposals):
    municipal_ids = {p['IDENTIF_PROPONENTE'] for p in proposals.values()} | {CITY_ID, '13961905000170'}
    linked = {e['ID_PROPOSTA'] for e in rows(directory, 'emenda', ['ID_PROPOSTA', 'NR_EMENDA'], ('ID_PROPOSTA', proposals))}
    candidates = {}
    for c in rows(directory, 'convenio', ['NR_CONVENIO', 'ID_PROPOSTA', 'VL_DESEMBOLSADO_CONV'], ('ID_PROPOSTA', proposals)):
        if c['NR_CONVENIO'] and c['ID_PROPOSTA'] not in linked:
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
        if number in budget_amendment:
            continue
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
            'details':{'Órgão superior':public_text(p['DESC_ORGAO_SUP']), 'Publicação':date(c['DIA_PUBL_CONV']),
                       'Situação da publicação':clean(c['SITUACAO_PUBLICACAO']), 'Vigência final original':date(c['DIA_FIM_VIGENC_ORIGINAL_CONV']),
                       'Prazo da prestação de contas':date(c['DIA_LIMITE_PREST_CONTAS']), 'Proposta':clean(p['NR_PROPOSTA']),
                       'Situação da contratação':clean(c['SITUACAO_CONTRATACAO']), 'UG emitente':clean(c['UG_EMITENTE']),
                       'Processo':clean(c['NR_PROCESSO'])},
            'sections': {'Empenhos': notes.get(number, []), 'Aditivos':[], 'Prorrogações':[], 'Metas':[]},
            'link': f'https://portaldatransparencia.gov.br/convenios/{number}',
        }
    if not instruments:
        raise ValueError('A carga federal não retornou instrumentos sem emenda')
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        quotas = pool.submit(state_quotas)
        agreed = pool.submit(state_agreements, ids)
        funds = pool.submit(federal_fund_plans, ids)
        payments.extend(quotas.result())
        state_rows, state_date = agreed.result()
        plans, fund_date = funds.result()
    instruments.extend(state_rows)
    instruments.extend(plans)
    if metadata() != version:
        raise RuntimeError('Arquivos mudaram durante a leitura. Execute uma nova carga.')
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {'schemaVersion':2, 'generatedAt':dt.datetime.now(dt.timezone.utc).isoformat(),
            'coverage':{'federalFiles':version,'federalProposalMode':proposal_mode,'federalProposalUpdatedAt':proposal_date,
                        'federalFundUpdatedAt':fund_date,'stateUpdatedAt':state_date},
            'instruments':sorted(instruments,key=lambda r:(r.get('signed') or r.get('start',''),r['number']),reverse=True),
            'payments':sorted(payments,key=lambda r:(r.get('day',r.get('period','')),r['id']),reverse=True)}
    tmp = output.with_suffix('.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(output)
    print(json.dumps({k:len(data[k]) for k in ('instruments','payments')} | {'stateAgreements':len(state_rows),'fundPlans':len(plans),'proposalMode':proposal_mode}))

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
