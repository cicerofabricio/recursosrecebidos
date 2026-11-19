import { makeWorkbook } from './xlsx.js'

const $ = id => document.getElementById(id)
const element = (tag, value, className) => {
  const node = document.createElement(tag)
  if (value !== undefined) node.textContent = value
  if (className) node.className = className
  return node
}
const button = (name, action, css = 'btn secondary') => {
  const b = element('button', name, css)
  b.type = 'button'
  b.addEventListener('click', action)
  return b
}
function link(name, target) {
  const a = element('a', name)
  a.href = target
  return a
}
const dateTime = new Intl.DateTimeFormat('pt-BR', {dateStyle:'short', timeStyle:'short',timeZone:'America/Sao_Paulo'})
const currency = new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL'})
const integer = new Intl.NumberFormat('pt-BR')
const collator = new Intl.Collator('pt-BR', {numeric:true,sensitivity:'base'})
const norm = value => String(value ?? '').normalize('NFD').replace(/\p{M}/gu,'').toLowerCase()
const labelList = {
  origin:'Origem', source:'Fonte oficial', number:'Nº do instrumento', agency:'Órgão concedente',
  recipient:'Unidade favorecida', cnpj:'CNPJ favorecido', object:'Objeto', type:'Modalidade',
  status:'Situação', year:'Exercício', signed:'Assinatura', start:'Início da vigência', end:'Fim da vigência',
  total:'Valor global pactuado', transfer:'Repasse pactuado ou previsto', counterpart:'Contrapartida pactuada',
  paid:'Valor desembolsado', proposal:'Nº da proposta', process:'Nº do processo',
  day:'Data do repasse', period:'Competência', document:'Documento financeiro',
  granted:'Valor concedido', amendment:'Origem parlamentar', agencyUnit:'Unidade celebrante',
}
const areas = {
  federal: {
    name:'Federal', heading:'Recursos federais',
    description:'Convênios, contratos de repasse, planos fundo a fundo e desembolsos da União para São Bernardo do Campo.',
    help:'O Transferegov publica propostas e planos fundo a fundo em APIs. Convênios e desembolsos são cruzados com os arquivos oficiais do mesmo portal. O recorte federal exclui instrumentos com vínculo parlamentar identificado.',
    names:{instruments:'Instrumentos e planos',payments:'Desembolsos'},
    subtitles:{instruments:'Consulte convênios, contratos de repasse e planos fundo a fundo. Veja os valores pactuados ou previstos em cada instrumento.',payments:'Consulte os desembolsos federais por instrumento e documento financeiro.'},
    fields:{
      instruments:['number','agency','recipient','cnpj','type','object','status','year','signed','start','end','total','transfer','counterpart','paid','proposal','process','source'],
      payments:['period','day','number','document','agency','recipient','cnpj','type','object','year','paid','source'],
    },
    defaults:{instruments:['number','agency','recipient','object','status','transfer','paid'],payments:['period','day','number','recipient','object','paid']},
    filters:{instruments:['year','recipient','type','status','source'],payments:['year','recipient','type','agency']},
  },
  estadual: {
    name:'Estadual', heading:'Recursos estaduais',
    description:'Convênios do Estado de São Paulo e quotas-partes repassadas a São Bernardo do Campo.',
    help:'Os convênios estaduais entram nos totais. A consulta pública estadual não distingue, registro a registro, quais têm origem parlamentar.',
    names:{instruments:'Convênios',payments:'Quotas-partes'},
    subtitles:{instruments:'Consulte convênios estaduais, seus valores previstos e concedidos e a vigência.',payments:'Consulte ICMS, IPVA, IPI Exportação e compensação financeira por competência.'},
    fields:{
      instruments:['number','agency','agencyUnit','recipient','cnpj','type','object','amendment','year','start','end','total','granted','source'],
      payments:['period','agency','recipient','cnpj','type','object','year','paid','source'],
    },
    defaults:{instruments:['number','agency','recipient','object','year','total','granted','amendment'],payments:['period','object','recipient','paid']},
    filters:{instruments:['year','agency','recipient','amendment'],payments:['year','period','object']},
  },
}
const amounts = new Set(['total','transfer','counterpart','paid','granted'])
const dates = new Set(['signed','start','end','day'])
const wide = new Set(['recipient','object','agency'])
const absent = value => value === undefined || value === null || value === ''
const textValue = (key,value) => {
  if (absent(value)) return '—'
  if (amounts.has(key)) return currency.format(value / 100)
  if (dates.has(key) || key==='period') return String(value).split('-').reverse().join('/')
  if (key === 'cnpj' && /^\d{14}$/.test(value)) return value.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5')
  return String(value)
}
const tableTypes = ['instruments','payments']
const initial = (area,type) => ({
  q:'', fields:new Map(), sort:type==='instruments'?'year':'period',
  direction:-1, columns:[...areas[area].defaults[type]], page:0, size:15,
})
const state = {
  area:'federal', data:null, error:false,
  workspaces:Object.fromEntries(Object.entries(areas).map(([area,config])=>[area,{
    table:'instruments', exportTable:'instruments', exportScope:'filtered',
    grids:Object.fromEntries(tableTypes.map(type=>[type,initial(area,type)])),
    order:Object.fromEntries(tableTypes.map(type=>[type,[...config.defaults[type]]])),
  }])),
}
const workspace = () => state.workspaces[state.area]
const areaConfig = () => areas[state.area]
const rows = (key,area=state.area) => state.data?.groups[area][key] || []
const columnLabel = key => state.area==='estadual' && key==='paid'?'Valor da quota-parte':labelList[key]
try {
  const current = JSON.parse(localStorage.getItem('recursos-excel-v2') || 'null')
  const saved = current || {federal:JSON.parse(localStorage.getItem('recursos-excel-v1') || '{}')}
  for (const [area,config] of Object.entries(areas)) for (const type of tableTypes) {
    if (Array.isArray(saved[area]?.[type])) {
      const chosen = [...new Set(saved[area][type].filter(key=>config.fields[type].includes(key)))]
      if (chosen.length || saved[area][type].length===0) state.workspaces[area].order[type]=chosen
    }
  }
} catch {}
const keyFor = value => absent(value) ? null : value
const withTerm = (list, query) => {
  const words = norm(query).trim().split(/\s+/).filter(Boolean)
  return words.length ? list.filter(r => words.every(term => r.search.includes(term))) : list
}
function filtered(type, ignored) {
  const config = workspace().grids[type]
  return withTerm(rows(type),config.q).filter(row => {
    for (const [key,values] of config.fields) {
      if (key !== ignored && !values.has(keyFor(row[key]))) return false
    }
    return true
  })
}
function ordered(type, source) {
  const {sort,direction} = workspace().grids[type]
  return [...source].sort((a,b) => {
    const x = a[sort], y = b[sort]
    if (absent(x)) return absent(y)?0:1
    if (absent(y)) return -1
    return direction * (typeof x === 'number' && typeof y === 'number' ? x-y : collator.compare(x,y))
  })
}
function setStats() {
  const type = workspace().table
  const list = filtered(type)
  const formatCount = num => state.data ? integer.format(num) : '—'
  const sum = (items, key) => items.some(i => typeof i[key]==='number') ? currency.format(items.reduce((out,i) => out+(i[key]||0),0)/100) : '—'
  const namesById = new Map(list.filter(i=>i.cnpj).map(i=>[norm(i.recipient),i.cnpj]))
  const count = [areaConfig().names[type],formatCount(list.length),'Registros no recorte']
  const recipients = ['Unidades favorecidas',formatCount(new Set(list.map(i=>i.cnpj || namesById.get(norm(i.recipient)) || norm(i.recipient)).filter(Boolean)).size),'Município e unidades identificadas']
  const stats = type==='instruments'
    ? state.area==='estadual'
      ? [count,recipients,['Valor total dos convênios',sum(list,'total'),'Previsto nos instrumentos do recorte'],['Valor concedido',sum(list,'granted'),'Repassado segundo o Portal da Transparência SP']]
      : [count,recipients,['Repasse pactuado ou previsto',sum(list,'transfer'),'Inclui os planos fundo a fundo'],['Desembolsado',sum(list,'paid'),'Valores informados para os instrumentos']]
    : [count,recipients,['Competências',formatCount(new Set(list.map(i=>i.period).filter(Boolean)).size),'Meses com registro no recorte'],[
      state.area==='estadual'?'Total das quotas-partes':'Total desembolsado',sum(list,'paid'),
      state.area==='estadual'?'Valores conforme a base indicada na ficha':'Desembolsos do recorte',
    ]]
  $('stats').replaceChildren(...stats.map(([title,value,subtitle]) => {
    const stat = element('article',undefined,'stat')
    stat.append(element('span',title),element('strong',value),element('small',subtitle))
    return stat
  }))
}
function table(cols, list, type, actionable) {
  const t = element('table')
  const header = t.createTHead().insertRow()
  const body = t.createTBody()
  for (const k of cols) {
    const th = element('th')
    th.scope = 'col'
    th.textContent = columnLabel(k)
    if (actionable) {
      const config = workspace().grids[type]
      const sort = button(columnLabel(k), () => {
        config.direction = config.sort === k ? -config.direction : (k === 'period' || dates.has(k) || amounts.has(k) || k === 'year' ? -1 : 1)
        config.sort = k
        config.page = 0
        renderResults()
      }, '')
      if (k === config.sort) {
        th.setAttribute('aria-sort',config.direction === 1?'ascending':'descending')
        sort.append(element('span',config.direction===1?'↑':'↓'))
      }
      th.replaceChildren(sort)
    }
    header.append(th)
  }
  for (const item of list) {
    const row = body.insertRow()
    for (const k of cols) {
      const cell = row.insertCell()
      const value = textValue(k,item[k])
      cell.textContent = value
      if (amounts.has(k)) cell.className = 'amount'
      if (wide.has(k)) cell.className = 'wide'
      if (k === 'number' || k === 'document' || dates.has(k)) cell.classList.add('tight')
      if (actionable && (k === cols[0] && !cols.includes('number') || k === 'number')) {
        cell.replaceChildren(button(value === '—' ? 'Abrir ficha' : value, () => openDetail(item,type), 'link-button row-link'))
      } else if (k === 'origin') {
        cell.replaceChildren(element('span',value,item.origin==='Estado'?'scope state':'scope'))
      }
    }
  }
  return t
}
function renderResults() {
  const type = workspace().table
  const config = workspace().grids[type]
  const list = ordered(type,filtered(type))
  const n = list.length
  const pages = Math.max(1,Math.ceil(n/config.size))
  config.page = Math.min(config.page,pages-1)
  const start = config.page*config.size
  $('result-info').textContent = state.error ? 'A base está temporariamente indisponível.' : !state.data ? 'Carregando dados oficiais…' : `${integer.format(n)} ${n===1?'registro encontrado':'registros encontrados'}`
  const blank = state.error?'Atualize a página para tentar novamente.':!state.data?'Aguarde enquanto a consulta carrega.':'Nenhum registro corresponde aos filtros.'
  $('grid').replaceChildren(n ? table(config.columns,list.slice(start,start+config.size),type,true) : element('p',blank,'empty'))
  const pager = $('pagination')
  pager.replaceChildren()
  if (n) {
    const controls = element('div',undefined,'pager-controls')
    const size = element('select')
    size.setAttribute('aria-label','Registros por página')
    for (const count of [15,30,60,100]) size.add(new Option(`${count} por página`,count,false,count===config.size))
    size.addEventListener('change',() => {config.size=Number(size.value);config.page=0;renderResults()})
    const back = button('Anterior',()=>{config.page--;renderResults()})
    const next = button('Próxima',()=>{config.page++;renderResults()})
    back.disabled = config.page===0
    next.disabled = config.page+1===pages
    controls.append(size,back,next)
    pager.append(element('span',`${start+1}–${Math.min(start+config.size,n)} de ${n}`),controls)
  }
  renderChips()
  setStats()
  if (viewName() === 'excel') renderExcel()
}
function closePeers(active) {
  for (const d of document.querySelectorAll('.facet[open], .columns[open]')) if (d!==active) d.open = false
}
function populateFacet(panel,key,query) {
  const type = workspace().table
  const config = workspace().grids[type]
  const selected = config.fields.get(key) || new Set()
  const counts = new Map()
  for (const row of filtered(type,key)) {
    const value = keyFor(row[key])
    counts.set(value,(counts.get(value)||0)+1)
  }
  for (const value of selected) if (!counts.has(value)) counts.set(value,0)
  const itemList = panel.lastChild
  itemList.replaceChildren()
  const options = [...counts].filter(([v]) => norm(v===null?'Não informado':textValue(key,v)).includes(norm(query)))
    .sort(([a],[b]) => a===null?1:b===null?-1:collator.compare(a,b)*(key==='year'?-1:1))
  for (const [value,count] of options) {
    const label = element('label')
    const checkbox = element('input')
    checkbox.type='checkbox'
    checkbox.checked=selected.has(value)
    checkbox.addEventListener('change',()=>{
      if (checkbox.checked) selected.add(value)
      else selected.delete(value)
      if (selected.size) config.fields.set(key,selected)
      else config.fields.delete(key)
      config.page = 0
      updateFacetLabels()
      renderResults()
    })
    label.append(checkbox,element('span',`${value===null?'Não informado':textValue(key,value)} (${count})`))
    itemList.append(label)
  }
  if (!options.length) itemList.append(element('p','Sem valores disponíveis.','muted'))
}
function facet(k) {
  const detail = element('details',undefined,'facet')
  detail.dataset.field = k
  const summary = element('summary',columnLabel(k))
  const pop = element('div',undefined,'popover')
  const search = element('input')
  search.type='search'
  search.placeholder='Procurar opção'
  search.setAttribute('aria-label',`Procurar em ${columnLabel(k)}`)
  search.addEventListener('input',()=>populateFacet(pop,k,search.value))
  pop.append(search,element('div'))
  detail.append(summary,pop)
  detail.addEventListener('toggle',()=>{if(detail.open){closePeers(detail);search.value='';populateFacet(pop,k,'')}})
  return detail
}
function updateFacetLabels() {
  for (const d of document.querySelectorAll('.facet')) {
    const key = d.dataset.field
    const n = workspace().grids[workspace().table].fields.get(key)?.size || 0
    d.dataset.active = String(n>0)
    d.firstChild.textContent = columnLabel(key)+(n?` · ${n}`:'')
  }
}
function renderChips() {
  const config = workspace().grids[workspace().table]
  const chips = $('chips')
  chips.replaceChildren()
  let count = 0
  for (const [k,values] of config.fields) {
    count += values.size
    for (const value of values) {
      chips.append(button(`${columnLabel(k)} · ${value===null?'Não informado':textValue(k,value)} ×`,()=>{
        values.delete(value)
        if (!values.size) config.fields.delete(k)
        config.page=0
        closePeers()
        updateFacetLabels()
        renderResults()
      },'chip'))
    }
  }
  if (count || config.q) chips.append(button('Limpar filtros',()=>{
    config.fields.clear()
    config.q=''
    config.page=0
    $('q').value=''
    closePeers()
    updateFacetLabels()
    renderResults()
  },'link-button'))
  chips.hidden = !(count || config.q)
  $('filter-count').textContent = count ? `(${count})` : ''
}
function renderColumns() {
  const type = workspace().table
  const config = workspace().grids[type]
  const parent = $('column-options')
  parent.replaceChildren()
  parent.append(button('Restaurar colunas',()=>{config.columns=[...defaults[type]];renderColumns();renderResults()},'link-button'))
  for (const key of areaConfig().fields[type]) {
    const label = element('label')
    const input = element('input')
    input.type='checkbox'
    input.checked=config.columns.includes(key)
    input.addEventListener('change',()=>{
      const chosen = new Set(config.columns)
      if (input.checked) chosen.add(key)
      else if (chosen.size > 1) chosen.delete(key)
      else input.checked=true
      config.columns=areaConfig().fields[type].filter(k=>chosen.has(k))
      renderResults()
    })
    label.append(input,element('span',columnLabel(key)))
    parent.append(label)
  }
}
function renderTable() {
  const type = workspace().table
  for (const b of document.querySelectorAll('[data-table]')) {
    b.textContent = areaConfig().names[b.dataset.table]
    b.setAttribute('aria-pressed',String(b.dataset.table===type))
  }
  $('table-definition').textContent = areaConfig().subtitles[type]
  $('q').value=workspace().grids[type].q
  $('quick').replaceChildren(...areaConfig().filters[type].map(facet))
  $('filters').replaceChildren(...areaConfig().fields[type].filter(k=>!areaConfig().filters[type].includes(k)).map(facet))
  $('columns').open=false
  renderColumns()
  updateFacetLabels()
  renderResults()
}
function openDetail(row,type) {
  const d = $('detail')
  $('detail-source').textContent = `${row.origin} · ${row.source}`
  $('detail-title').textContent = type==='instruments' ? `${row.origin==='Estado'?'Convênio':row.id.startsWith('faf-')?'Plano de ação':'Instrumento'} ${row.number}` : `Repasse ${row.document || row.number || row.object || ''}`
  const fields = element('dl',undefined,'dialog-grid')
  function field(label,value, full=false) {
    const node = element('div',undefined,full?'full':'')
    node.append(element('dt',label),element('dd',value))
    fields.append(node)
  }
  for (const k of areaConfig().fields[type]) if (k!=='object') field(columnLabel(k),textValue(k,row[k]))
  if (!absent(row.object)) field('Objeto',row.object,true)
  for (const [label,value] of Object.entries(row.details || {})) {
    if (!absent(value)) field(label,/^\d{4}-\d{2}-\d{2}$/.test(value)?textValue('day',value):String(value))
  }
  const content = $('detail-body')
  content.replaceChildren(fields)
  if (row.link && /^https:\/\/(portaldatransparencia\.gov\.br|www\.transparencia\.sp\.gov\.br|demandas\.spsempapel\.sp\.gov\.br|api-publica\.transferegov\.gestao\.gov\.br|www\.fazenda\.sp\.gov\.br|www\.tesourotransparente\.gov\.br)\//.test(row.link)) {
    const a = link('Abrir fonte oficial',row.link)
    a.target='_blank'
    a.rel='noopener noreferrer'
    a.className='btn secondary'
    content.append(a)
  }
  if (type === 'payments') {
    const i = rows('instruments').find(x=>x.id===row.instrumentId)
    if (i) content.append(button('Ver instrumento',()=>openDetail(i,'instruments')))
  }
  const sections = {...row.sections}
  if (type==='instruments' && state.area==='federal') {
    sections.Repasses = rows('payments').filter(x=>x.instrumentId===row.id).map(x=>({Data:textValue('day',x.day),Documento:x.document,Valor:x.paid}))
  }
  for (const [name,list] of Object.entries(sections)) {
    if (!list.length) continue
    const section = element('section',undefined,'dialog-section')
    const heading = element('h3',`${name} · ${list.length}`)
    const grid = element('div',undefined,'grid')
    const t = element('table')
    const header = t.createTHead().insertRow()
    const keys = Object.keys(list[0])
    for (const k of keys) header.append(element('th',k))
    const tbody=t.createTBody()
    for (const item of list) {
      const r = tbody.insertRow()
      for (const k of keys) {
        const value=item[k]
        r.insertCell().textContent=k==='Valor' ? textValue('paid',value) : /^\d{4}-\d{2}-\d{2}$/.test(value)?textValue('day',value):value || '—'
      }
    }
    grid.append(t)
    section.append(heading,grid)
    content.append(section)
  }
  d.scrollTop=0
  if (!d.open) d.showModal()
}
function saveExcel() {
  try { localStorage.setItem('recursos-excel-v2',JSON.stringify(Object.fromEntries(Object.entries(state.workspaces).map(([area,config])=>[area,config.order])))) } catch {}
}
const exportRows = () => ordered(workspace().exportTable,workspace().exportScope==='all'?rows(workspace().exportTable):filtered(workspace().exportTable))
function renderColumnOrder(list, fields, onMove, {tag = 'li', locked = false} = {}) {
  const disabled = locked || fields.length < 2;
  const handles = [];
  const rows = fields.map(([key, label], index) => {
    const row = document.createElement(tag);
    row.className = 'column-order-row';
    const name = document.createElement('span');
    name.textContent = label;
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'column-order-handle';
    handle.disabled = disabled;
    handle.setAttribute('aria-label', 'Mover coluna ' + label);
    handle.setAttribute('aria-describedby', 'column-order-help');
    const icon = document.createElement('span');
    icon.className = 'column-order-grip';
    icon.setAttribute('aria-hidden', 'true');
    handle.append(icon);
    row.append(name, handle);
    handles.push(handle);
    return row;
  });

  function move(from, to, keyboard) {
    if (to === from) return;
    const keys = fields.map(([key]) => key);
    keys.splice(to, 0, keys.splice(from, 1)[0]);
    onMove(keys);
    list.children[to]?.querySelector('button')?.focus({preventScroll: !keyboard});
    const status = document.getElementById('column-order-status');
    if (status) status.textContent = fields[from][1] + ', posição ' + (to + 1) + ' de ' + fields.length + '.';
  }

  handles.forEach((handle, from) => {
    let active = null;
    let frame;
    let marker;
    function mark(clientY) {
      const others = rows.filter((_, i) => i !== from);
      const next = others.findIndex(row => {
        const box = row.getBoundingClientRect();
        return clientY < box.top + box.height / 2;
      });
      const target = next < 0 ? others.length : next;
      const before = others[target];
      const anchor = before || others.at(-1);
      const className = before ? 'column-drop-before' : 'column-drop-after';
      if (marker?.anchor !== anchor || marker?.className !== className || target === from) {
        if (marker) marker.anchor.classList.remove(marker.className);
        marker = null;
        if (anchor && target !== from) {
          anchor.classList.add(className);
          marker = {anchor, className};
        }
      }
      return target;
    }
    function scrollList() {
      frame = undefined;
      if (!active?.dragging || !handle.isConnected) return;
      const box = list.getBoundingClientRect();
      const y = active.y;
      const top = Math.max(0, box.top);
      const bottom = Math.min(window.innerHeight, box.bottom);
      const speed = y < top + 28 ? -12 : y > bottom - 28 ? 12 : 0;
      if (!speed) return;
      const before = list.scrollTop;
      list.scrollTop += speed;
      if (before === list.scrollTop) return;
      mark(y);
      frame = requestAnimationFrame(scrollList);
    }
    function clear() {
      if (frame !== undefined) cancelAnimationFrame(frame);
      frame = undefined;
      if (marker) marker.anchor.classList.remove(marker.className);
      marker = null;
      rows[from].classList.remove('column-dragging');
      active = null;
    }
    handle.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0 || active) return;
      handle.setPointerCapture(event.pointerId);
      active = {id: event.pointerId, start: event.clientY, y: event.clientY, dragging: false};
    });
    handle.addEventListener('pointermove', event => {
      if (active?.id !== event.pointerId) return;
      active.y = event.clientY;
      if (!active.dragging && Math.abs(active.y - active.start) < 4) return;
      active.dragging = true;
      rows[from].classList.add('column-dragging');
      mark(active.y);
      if (frame === undefined) frame = requestAnimationFrame(scrollList);
    });
    handle.addEventListener('pointerup', event => {
      if (active?.id !== event.pointerId) return;
      const box = list.getBoundingClientRect();
      const drop = active.dragging && event.clientX >= box.left && event.clientX <= box.right &&
        event.clientY >= box.top - 16 && event.clientY <= box.bottom + 16;
      const target = drop ? mark(event.clientY) : from;
      clear();
      move(from, target, false);
    });
    handle.addEventListener('pointercancel', clear);
    handle.addEventListener('lostpointercapture', clear);
    handle.addEventListener('keydown', event => {
      if (event.key === 'Escape' && active) {
        clear();
        return;
      }
      const destination = {
        ArrowUp: from - 1,
        ArrowDown: from + 1,
        Home: 0,
        End: fields.length - 1,
      };
      if (!Object.hasOwn(destination, event.key)) return;
      event.preventDefault();
      if (active) return;
      const to = destination[event.key];
      if (to >= 0 && to < fields.length) move(from, to, true);
    });
  });

  list.replaceChildren(...rows);
}

function renderExcel() {
  const type = workspace().exportTable
  const list = exportRows()
  const fields = workspace().order[type]
  for (const option of $('export-table').options) option.textContent=areaConfig().names[option.value]
  $('export-table').value=type
  $('export-scope').value=workspace().exportScope
  const fieldList = $('excel-fields')
  fieldList.className = 'field-list'
  fieldList.replaceChildren()
  for (const key of areaConfig().fields[type]) {
    const label = element('label')
    const input = element('input')
    input.type='checkbox'
    input.checked=fields.includes(key)
    input.addEventListener('change',()=>{
      workspace().order[type] = input.checked?[...fields,key]:fields.filter(f=>f!==key)
      saveExcel()
      renderExcel()
      const focus = fieldList.querySelectorAll('input')[areaConfig().fields[type].indexOf(key)]
      focus?.focus()
    })
    label.append(input,element('span',columnLabel(key)))
    fieldList.append(label)
  }
  $('excel-count').textContent=`${fields.length} colunas · ${list.length} registros`
  renderColumnOrder($('export-order'), fields.map(key => [key,columnLabel(key)]), next => {
    workspace().order[type] = next
    saveExcel()
    renderExcel()
  }, {tag: 'div'})
  $('download').disabled = !fields.length || !list.length
  $('export-preview').replaceChildren(fields.length&&list.length?table(fields,list.slice(0,8),type,false):element('p','Selecione colunas e aplique um recorte com registros.','empty'))
}
const sources = [
  ['Transferegov.br · Discricionárias e Legais','https://api-publica.transferegov.gestao.gov.br/discricionarias/docs','Propostas consultadas pela API. Convênios, contratos de repasse, desembolsos, empenhos, aditivos, prorrogações e metas cruzados com os arquivos oficiais do Transferegov.','federalProposalUpdatedAt'],
  ['Transferegov.br · Fundo a Fundo','https://api-publica.transferegov.gestao.gov.br/fundoafundo/docs','Planos de ação autorizados, sem parcela de emenda identificada. O valor do plano representa a previsão de repasse.','federalFundUpdatedAt'],
  ['Portal da Transparência de São Paulo','https://www.transparencia.sp.gov.br/Convenios','Convênios e transferências para o Município e unidades identificadas. A base informa valor total e valor concedido.','stateUpdatedAt'],
  ['Sefaz-SP','https://portal.fazenda.sp.gov.br/acessoinformacao/Paginas/Transfer%C3%AAncias-Constitucionais-a-Municipios.aspx','Quotas-partes municipais desde 2020, com as bases de cálculo publicadas pela Sefaz-SP.'],
]
function renderSource() {
  const data=state.data
  const coverage=data?.coverage
  const files=Object.values(coverage?.federalFiles||{}).map(v=>Date.parse(v)).filter(Number.isFinite)
  const sourceDate=state.area==='federal'?(files.length?Math.max(...files):NaN):Date.parse(coverage?.stateUpdatedAt)
  const published=Date.parse(data?.generatedAt)
  $('portal-update').textContent=Number.isFinite(published)?'Última carga publicada em '+dateTime.format(published)+' · Atualização programada às 11h23, horário de São Paulo.':''
  $('hero-status').textContent=data?(state.area==='federal'?'Transferegov.br':'Portais oficiais de São Paulo'):state.error?'Base indisponível':'Consultando fontes'
  $('coverage-help').hidden=!data
  $('coverage-tooltip').textContent=areaConfig().help
  $('hero-date').textContent=Number.isFinite(sourceDate)?(state.area==='federal'?'Arquivos federais de ':'Convênios estaduais atualizados em ')+dateTime.format(sourceDate):state.error?'Tente novamente em instantes.':'Data da fonte indisponível'
  const counts=[
    rows('instruments','federal').filter(r=>r.source==='Transferegov.br').length,
    rows('instruments','federal').filter(r=>r.source==='Transferegov.br · Fundo a Fundo').length,
    rows('instruments','estadual').length,rows('payments','estadual').length,
  ]
  const labels=['instrumentos','planos de ação','convênios','registros de quotas-partes']
  $('sources').replaceChildren(...sources.map(([name,url,notes,stamp],i)=>{
    const card=element('article',undefined,'source-box')
    const info=i===0&&coverage?.federalProposalMode==='csv'?notes.replace('Propostas consultadas pela API.','Propostas lidas dos arquivos oficiais do Transferegov nesta carga.'):notes
    card.append(element('p',data?integer.format(counts[i])+' '+labels[i]:'Fonte oficial','coverage'),element('h2',name),element('p',info))
    const date=Date.parse(coverage?.[stamp])
    if (Number.isFinite(date)) card.append(element('p','Atualização na fonte em '+dateTime.format(date)+'.'))
    const a=link('Consultar fonte oficial',url)
    a.target='_blank'
    a.rel='noopener noreferrer'
    card.append(a)
    return card
  }))
}
function viewName() {
  const hash=location.hash.slice(1)
  if (hash==='fontes') return 'fontes'
  return hash==='excel'||hash.endsWith('-excel')?'excel':'consulta'
}
let lastRoute
function renderView() {
  const view=viewName()
  const hash=location.hash.slice(1)
  if (view!=='fontes') state.area=hash.startsWith('estadual')?'estadual':'federal'
  const route=state.area+'/'+view
  for (const node of document.querySelectorAll('.view')) node.hidden=node.id!==view
  $('area-menu').hidden=view==='fontes'
  $('area-menu').setAttribute('aria-label','Opções da aba '+areaConfig().name)
  for (const nav of document.querySelectorAll('[data-area], [data-view]')) {
    if (view==='fontes'?nav.dataset.view==='fontes':nav.dataset.area===state.area) nav.setAttribute('aria-current','page')
    else nav.removeAttribute('aria-current')
  }
  for (const [id,section,url] of [['consult-link','consulta',state.area],['excel-link','excel',state.area+'-excel']]) {
    const nav=$(id)
    nav.href='#'+url
    if (section===view) nav.setAttribute('aria-current','page')
    else nav.removeAttribute('aria-current')
  }
  $('configure-excel').href='#'+state.area+'-excel'
  $('area-heading').textContent=areaConfig().heading
  $('area-intro').textContent=areaConfig().description
  $('excel-area').textContent=areaConfig().name.toUpperCase()+' · EXPORTAÇÃO'
  if (view==='consulta') renderTable()
  if (view==='excel') {
    if (lastRoute===state.area+'/consulta') workspace().exportTable=workspace().table
    $('export-message').textContent=''
    renderExcel()
  }
  renderSource()
  if (lastRoute && route!==lastRoute) window.scrollTo(0,0)
  lastRoute=route
}
$('q').addEventListener('input',()=>{
  const config=workspace().grids[workspace().table]
  config.q=$('q').value
  config.page=0
  closePeers()
  renderResults()
})
$('filters-toggle').addEventListener('click',()=>{
  $('filters').hidden=!$('filters').hidden
  $('filters-toggle').setAttribute('aria-expanded',String(!$('filters').hidden))
  closePeers()
})
$('columns').addEventListener('toggle',()=>{if($('columns').open)closePeers($('columns'))})
document.addEventListener('click',event=>{if(!event.target.closest('.facet,.columns'))closePeers()})
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('detail').open)closePeers()})
for (const b of document.querySelectorAll('[data-table]')) b.addEventListener('click',()=>{workspace().table=b.dataset.table;renderTable()})
$('export-table').addEventListener('change',()=>{workspace().exportTable=$('export-table').value;renderExcel()})
$('export-scope').addEventListener('change',()=>{workspace().exportScope=$('export-scope').value;renderExcel()})
$('select-all').addEventListener('click',()=>{workspace().order[workspace().exportTable]=[...areaConfig().fields[workspace().exportTable]];saveExcel();renderExcel()})
$('deselect-all').addEventListener('click',()=>{workspace().order[workspace().exportTable]=[];saveExcel();renderExcel()})
$('download').addEventListener('click',()=>{
  const type=workspace().exportTable
  const fields=workspace().order[type]
  const list=exportRows()
  if (!fields.length || !list.length) return
  const selected=fields.map(k=>({key:k,label:columnLabel(k),type:amounts.has(k)?'money':dates.has(k)?'date':'text'}))
  const blob=makeWorkbook(selected,list,areaConfig().names[type])
  const url=URL.createObjectURL(blob)
  const a=link('',url)
  a.download=`recursos-recebidos-${state.area}-${norm(areaConfig().names[type]).replaceAll(' ','-')}.xlsx`
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(()=>URL.revokeObjectURL(url),1500)
  $('export-message').textContent=`Planilha gerada com ${list.length} registros e ${fields.length} colunas.`
})
const coverageHelp = $('coverage-help')
const coverageToggle = $('coverage-toggle')
const coverageTooltip = $('coverage-tooltip')
let coveragePinned = false
function showCoverage(visible) {
  coverageTooltip.hidden = !visible
  coverageToggle.setAttribute('aria-expanded',String(visible))
}
coverageHelp.addEventListener('pointerenter',event => { if (event.pointerType === 'mouse') showCoverage(true) })
coverageHelp.addEventListener('pointerleave',() => { if (!coveragePinned) showCoverage(false) })
coverageToggle.addEventListener('focus',() => { if (coverageToggle.matches(':focus-visible')) showCoverage(true) })
coverageToggle.addEventListener('blur',() => { if (!coveragePinned) showCoverage(false) })
coverageToggle.addEventListener('click',() => {
  coveragePinned = !coveragePinned
  showCoverage(coveragePinned)
})
function closeCoverage() {
  coveragePinned = false
  showCoverage(false)
}
document.addEventListener('click',event => { if (!coverageHelp.contains(event.target)) closeCoverage() })
document.addEventListener('keydown',event => { if (event.key === 'Escape') closeCoverage() })
window.addEventListener('hashchange',() => { closeCoverage(); renderView() })
renderView()
try {
  const response=await fetch('/api/dados')
  if (!response.ok) throw new Error('Falha na consulta')
  const data=await response.json()
  if (!Array.isArray(data.instruments) || !Array.isArray(data.payments) || !data.coverage) throw new Error('Formato inválido')
  const groups={federal:{instruments:[],payments:[]},estadual:{instruments:[],payments:[]}}
  for (const type of tableTypes) for (const row of data[type]) {
    const area=row.origin==='Estado'?'estadual':row.origin==='União'?'federal':null
    if (!area) continue
    row.search=norm(areas[area].fields[type].map(key=>`${row[key]??''} ${textValue(key,row[key])}`).join(' '))
    groups[area][type].push(row)
  }
  state.data={...data,groups}
} catch {
  state.error=true
}
renderView()
