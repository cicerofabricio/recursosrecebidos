// Gera uma planilha Open XML com texto literal, valores numéricos e datas reais.
const enc = new TextEncoder()
const xml = value => String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g,'')
  .replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')
const spreadsheet = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
const relationship = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
const packageRels = 'http://schemas.openxmlformats.org/package/2006/relationships'
const mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
const declaration = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
const crcTable = Uint32Array.from({length:256},(_,i)=>{
  for (let n=0;n<8;n++) i=(i>>>1)^((i&1)?0xedb88320:0)
  return i
})
function crc32(bytes) {
  let n=0xffffffff
  for (const b of bytes) n=crcTable[(n^b)&255]^(n>>>8)
  return (n^0xffffffff)>>>0
}
function zip(files) {
  let offset=0
  let catalogSize=0
  const locals=[], catalog=[]
  for (const [filename,content] of files) {
    const name=enc.encode(filename)
    const data=enc.encode(declaration+content)
    const crc=crc32(data)
    const local=new Uint8Array(30+name.length)
    const a=new DataView(local.buffer)
    a.setUint32(0,0x04034b50,true)
    a.setUint16(4,20,true)
    a.setUint16(12,33,true)
    a.setUint32(14,crc,true)
    a.setUint32(18,data.length,true)
    a.setUint32(22,data.length,true)
    a.setUint16(26,name.length,true)
    local.set(name,30)
    locals.push(local,data)
    const central=new Uint8Array(46+name.length)
    const b=new DataView(central.buffer)
    b.setUint32(0,0x02014b50,true)
    b.setUint16(4,20,true)
    b.setUint16(6,20,true)
    b.setUint16(14,33,true)
    b.setUint32(16,crc,true)
    b.setUint32(20,data.length,true)
    b.setUint32(24,data.length,true)
    b.setUint16(28,name.length,true)
    b.setUint32(42,offset,true)
    central.set(name,46)
    catalog.push(central)
    catalogSize+=central.length
    offset+=local.length+data.length
  }
  const footer=new Uint8Array(22)
  const end=new DataView(footer.buffer)
  end.setUint32(0,0x06054b50,true)
  end.setUint16(8,files.length,true)
  end.setUint16(10,files.length,true)
  end.setUint32(12,catalogSize,true)
  end.setUint32(16,offset,true)
  return new Blob([...locals,...catalog,footer],{type:mime})
}
function column(index) {
  let address=''
  for (let n=index+1;n>0;n=Math.floor((n-1)/26)) address=String.fromCharCode(65+(n-1)%26)+address
  return address
}
function cell(reference,value,type) {
  if (value===null || value===undefined || value==='') return ''
  if (type==='money' && Number.isFinite(value)) return `<c r="${reference}" s="2"><v>${(value/100).toFixed(2)}</v></c>`
  if (type==='date' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const day = (Date.parse(value)-Date.UTC(1899,11,30))/86400000
    if (Number.isFinite(day) && day>60) return `<c r="${reference}" s="3"><v>${day}</v></c>`
  }
  if (typeof value==='number' && Number.isFinite(value)) return `<c r="${reference}"><v>${value}</v></c>`
  const style=type==='header'?' s="1"':''
  return `<c r="${reference}"${style} t="inlineStr"><is><t xml:space="preserve">${xml(value).slice(0,32000)}</t></is></c>`
}
const styles = `<styleSheet xmlns="${spreadsheet}">
<numFmts count="2"><numFmt numFmtId="164" formatCode="&quot;R$&quot; #,##0.00"/><numFmt numFmtId="165" formatCode="dd/mm/yyyy"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF23584A"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`
export function makeWorkbook(fields,rows,sheetName) {
  const colNames=fields.map((_,i)=>column(i))
  const last=`${colNames.at(-1)}${rows.length+1}`
  const columns=fields.map((f,i)=>`<col min="${i+1}" max="${i+1}" width="${f.key==='object'?72:Math.max(19,Math.min(44,f.label.length+3))}" customWidth="1"/>`).join('')
  const header=fields.map((f,i)=>cell(colNames[i]+'1',f.label,'header')).join('')
  const body=rows.map((row,n)=>{
    const i=n+2
    return `<row r="${i}">${fields.map((f,j)=>cell(colNames[j]+i,row[f.key],f.type)).join('')}</row>`
  }).join('')
  return zip([
    ['[Content_Types].xml',`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="${mime.replace(/\.sheet$/,'.sheet.main')}+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`],
    ['_rels/.rels',`<Relationships xmlns="${packageRels}"><Relationship Id="rId1" Type="${relationship}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ['xl/workbook.xml',`<workbook xmlns="${spreadsheet}" xmlns:r="${relationship}"><sheets><sheet name="${xml(sheetName)}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
    ['xl/_rels/workbook.xml.rels',`<Relationships xmlns="${packageRels}"><Relationship Id="rId1" Type="${relationship}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="${relationship}/styles" Target="styles.xml"/></Relationships>`],
    ['xl/styles.xml',styles],
    ['xl/worksheets/sheet1.xml',`<worksheet xmlns="${spreadsheet}"><dimension ref="A1:${last}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${columns}</cols><sheetData><row r="1">${header}</row>${body}</sheetData><autoFilter ref="A1:${last}"/></worksheet>`],
  ])
}
