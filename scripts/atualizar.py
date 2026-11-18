"""Baixa fontes primárias e publica uma projeção pública de São Bernardo do Campo."""
import argparse
import concurrent.futures
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tributos import state_quotas

BASE = 'https://api-publica.transferegov.gestao.gov.br/downloads/dadosgov/'
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

def state_candidates(municipal_ids):
    def query(key, value):
        found = []
        stamp = None
        for page in range(1, 6):
            body = {'page': page, 'pageSize': 100, 'totalItens': 0, key: value}
            with request(STATE + '/Buscar', json.dumps(body).encode()) as res:
                response = json.load(res)
            found.extend(response['items'])
            stamp = response['ultimaAtualizacao']
            if not response.get('hasNext'):
                return found, stamp
        raise ValueError('A consulta estadual ultrapassou o limite de páginas')
    args = [('nomeBeneficiario', 'BERNARDO DO CAMPO'), *[('cnpjBeneficiario', cnpj) for cnpj in sorted(municipal_ids)]]
    candidates = {}
    source_date = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for found, stamp in pool.map(lambda arg: query(*arg), args):
            source_date = stamp or source_date
            for row in found:
                if row.get('cnpjBeneficiario') in municipal_ids:
                    candidates[row['numeroConvenio']] = row
    return len(candidates), source_date

def build(directory):
    proposals = {}
    for p in rows(directory, 'proposta', ['ID_PROPOSTA', 'COD_MUNIC_IBGE', 'NATUREZA_JURIDICA', 'IDENTIF_PROPONENTE']):
        if p['COD_MUNIC_IBGE'] in {IBGE, IBGE[:-1]} and municipal_proposal(p):
            proposals[p['ID_PROPOSTA']] = p
    if not proposals:
        raise ValueError('Nenhuma proposta municipal encontrada no recorte oficial')
    municipal_ids = {p['IDENTIF_PROPONENTE'] for p in proposals.values()} | {CITY_ID}
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
        list(pool.map(lambda name: download(directory, name), FILES))
    instruments, payments, ids = build(directory)
    payments.extend(state_quotas())
    try:
        state_count, state_date = state_candidates(ids)
    except (OSError, ValueError, KeyError):
        state_count, state_date = None, None
    if metadata() != version:
        raise RuntimeError('Arquivos mudaram durante a leitura. Execute uma nova carga.')
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {'generatedAt':dt.datetime.now(dt.timezone.utc).isoformat(), 'coverage':{'federalFiles':version,'stateUnverified':state_count,'stateUpdatedAt':state_date},
            'instruments':sorted(instruments,key=lambda r:(r['signed'],r['number']),reverse=True),
            'payments':sorted(payments,key=lambda r:(r.get('day',r.get('period','')),r['id']),reverse=True)}
    tmp = output.with_suffix('.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(output)
    print(json.dumps({k:len(data[k]) for k in ('instruments','payments')} | {'stateUnverified':state_count}))

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
