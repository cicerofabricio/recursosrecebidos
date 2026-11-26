"""Consulta a série municipal publicada pela Secretaria da Fazenda e Planejamento de SP."""
import datetime as dt
import http.cookiejar
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

URL = 'https://www.fazenda.sp.gov.br/RepasseConsulta/Consulta/repasse.aspx'
PREFIX = 'ctl00$ConteudoPagina$'
MONTH = ['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro']
LABELS = [
    ('ICMS','Valor líquido do FUNDEB conforme nota da fonte.'),
    ('IPVA','Valor bruto informado pela fonte, antes do desconto do FUNDEB.'),
    ('IPI Exportação','Valor líquido do FUNDEB conforme nota da fonte.'),
    ('Compensação financeira estadual','Compensação financeira conforme classificação da fonte estadual.'),
]

class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hidden = {}
        self.rows = []
        self.grid = self.in_cell = False
        self.row = None
        self.select = None
        self.selected = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'input' and fields.get('type') == 'hidden' and 'name' in fields:
            self.hidden[fields['name']] = fields.get('value', '')
        if tag == 'select':
            self.select = fields.get('name')
        if tag == 'option' and self.select and 'selected' in fields:
            self.selected[self.select] = fields.get('value')
        if tag == 'table' and fields.get('id') == 'ConteudoPagina_gdvRepasse':
            self.grid = True
        if self.grid and tag == 'tr':
            self.row = []
        if self.grid and tag in ('td','th'):
            self.cell = []
            self.in_cell = True

    def handle_endtag(self, tag):
        if tag == 'select':
            self.select = None
        if not self.grid:
            return
        if tag in ('td','th') and self.row is not None:
            self.row.append(''.join(self.cell).strip())
            self.in_cell = False
        if tag == 'tr' and self.row:
            self.rows.append(self.row)
        if tag == 'table':
            self.grid = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)

def state_quotas():
    today = dt.datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers = {'User-Agent':'RecursosRecebidosSBC/1.0', 'Referer':URL}

    def open_page(body=None):
        data = urllib.parse.urlencode(body).encode() if body is not None else None
        for attempt in range(3):
            try:
                with client.open(urllib.request.Request(URL, data=data, headers=headers), timeout=30) as r:
                    return Page(r.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                if exc.code not in (500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
            time.sleep(2 ** (attempt + 1))

    blank = open_page()
    if not {'__VIEWSTATE','__EVENTVALIDATION'} <= blank.hidden.keys():
        raise ValueError('Formulário de cotas retornou campos inesperados')
    records = []
    for year in range(2020, today.year+1):
        query = blank.hidden | {PREFIX+'ddlMuni':'6350',PREFIX+'rblTipo':'ANO',
                                PREFIX+'ddlAno':str(year),PREFIX+'btnConfirmar':'Confirmar'}
        page = open_page(query)
        if page.selected.get(PREFIX+'ddlMuni') != '6350' or page.selected.get(PREFIX+'ddlAno') != str(year):
            raise ValueError(f'Consulta retornou município ou exercício diferente de {year}')
        if len(page.rows) < 3 or [x.split()[0] for x in page.rows[0]] != ['Meses','ICMS','IPVA','Fund.Exp-IPI','Comp.','Total']:
            raise ValueError(f'Leiaute da consulta tributária mudou em {year}')
        screened = set()
        def cents(raw):
            if not re.fullmatch(r'-?[0-9.]+,\d{2}',raw):
                raise ValueError(f'Valor estadual inválido no ano {year}')
            return int(Decimal(raw.replace('.','').replace(',','.'))*100)
        for label,*amount_fields in page.rows[1:]:
            label = label.lower()
            if label == 'total':
                continue
            month = MONTH.index(label)+1
            if month in screened or len(amount_fields)!=5:
                raise ValueError(f'Linha de repasse tributário inesperada no ano {year}')
            screened.add(month)
            values = [cents(raw) for raw in amount_fields]
            if abs(sum(values[:4])-values[4]) > 4:
                raise ValueError(f'Componentes e total divergentes no ano {year}, mês {month}, diferença {sum(values[:4])-values[4]} centavos')
            for idx,(value, (kind, basis)) in enumerate(zip(values, LABELS),1):
                if value == 0:
                    continue
                records.append({'id':f'sp-tributo-{year}-{month:02d}-{idx}', 'origin':'Estado', 'source':'Sefaz-SP',
                    'agency':'Secretaria da Fazenda e Planejamento do Estado de São Paulo',
                    'recipient':'MUNICIPIO DE SAO BERNARDO DO CAMPO','cnpj':'46523239000147',
                    'type':'Quota-parte estadual','object':kind,'year':year,'period':f'{year}-{month:02d}','paid':value,
                    'details':{'Base de cálculo':basis,'Granularidade':'Consolidado mensal'},'link':URL})
        if (year<today.year and len(screened)!=12) or (year==today.year and not set(range(1,today.month)).issubset(screened)):
            raise ValueError(f'Série estadual incompleta em {year}')
    return records
