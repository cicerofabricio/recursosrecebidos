const host = 'recursosrecebidos.sigpc.com.br'
const issuer = 'https://token.actions.githubusercontent.com'
const audience = 'https://recursosrecebidos.sigpc.com.br/api/atualizacao'
const repository = 'cicerofabricio/recursosrecebidos'
const workflow = repository+'/.github/workflows/atualizar.yml@refs/heads/main'
const snapshotName = 'publico-v1'
const maxBytes = 5_000_000
const jsonType = 'application/json; charset=utf-8'
const securityHeaders = {
  'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'X-Content-Type-Options':'nosniff',
  'Referrer-Policy':'strict-origin-when-cross-origin',
  'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
  'Strict-Transport-Security':'max-age=31536000',
}
const json = (body,status=200,more={})=>Response.json(body,{status,headers:{...securityHeaders,'Cache-Control':'no-store',...more}})
const decoder = new TextDecoder()
const encoder = new TextEncoder()
const base64url = value => Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')),v=>v.charCodeAt(0))

async function issuerKeys() {
  const controller = new AbortController()
  const deadline = setTimeout(()=>controller.abort(),6000)
  try {
    const response=await fetch(issuer+'/.well-known/jwks',{redirect:'manual',signal:controller.signal})
    if (!response.ok) return null
    return await response.json()
  } finally {
    clearTimeout(deadline)
  }
}

async function validIdentity(request) {
  const token = request.headers.get('authorization')?.match(/^Bearer ([A-Za-z0-9._-]{100,8500})$/i)?.[1]
  if (!token || token.split('.').length!==3) return false
  try {
    const [header,claims,signature]=token.split('.')
    const h=JSON.parse(decoder.decode(base64url(header)))
    const c=JSON.parse(decoder.decode(base64url(claims)))
    const now=Math.floor(Date.now()/1000)
    if (h.alg!=='RS256' || !/^[A-Za-z0-9-]{15,120}$/.test(h.kid)
      || c.iss!==issuer || c.aud!==audience || c.repository!==repository || c.repository_id!=='1463460196'
      || c.repository_owner_id!=='196865265' || c.workflow_ref!==workflow || c.ref!=='refs/heads/main'
      || !['schedule','workflow_dispatch'].includes(c.event_name) || c.runner_environment!=='github-hosted'
      || !Number.isInteger(c.exp) || !Number.isInteger(c.nbf) || !Number.isInteger(c.iat)
      || c.exp<=now || c.nbf>now+30 || c.iat>now+30 || c.exp-c.iat>900) return false
    const jwks=await issuerKeys()
    const jwk=jwks?.keys?.find(k=>k.kid===h.kid&&k.kty==='RSA'&&k.alg==='RS256')
    if (!jwk) return false
    const key=await crypto.subtle.importKey('jwk',jwk,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['verify'])
    return await crypto.subtle.verify('RSASSA-PKCS1-v1_5',key,base64url(signature),encoder.encode(header+'.'+claims))
  } catch {
    return false
  }
}
function validSnapshot(data) {
  if (!data || typeof data!=='object') return false
  const stamp=Date.parse(data.generatedAt)
  if (!Number.isFinite(stamp) || stamp>Date.now()+60_000 || stamp<Date.now()-86_400_000) return false
  if (!data.coverage?.federalFiles || typeof data.coverage.federalFiles!=='object') return false
  const fileNames=['convenio','desembolso','emenda','empenho','meta_crono_fisico','proposta','prorroga_oficio','termo_aditivo']
  if (fileNames.some(name=>!Number.isFinite(Date.parse(data.coverage.federalFiles['siconv_'+name+'.zip'])))) return false
  if (!Array.isArray(data.instruments) || !Array.isArray(data.payments)) return false
  if (data.instruments.length<1 || data.instruments.length>2500 || data.payments.length<1 || data.payments.length>16000) return false
  if (data.schemaVersion !== undefined && data.schemaVersion !== 2) return false
  if (data.schemaVersion === 2 && (!['api','csv'].includes(data.coverage.federalProposalMode)
    || !Number.isFinite(Date.parse(data.coverage.federalFundUpdatedAt))
    || !Number.isFinite(Date.parse(data.coverage.stateUpdatedAt))
    || (data.coverage.federalProposalMode==='api' && !Number.isFinite(Date.parse(data.coverage.federalProposalUpdatedAt))))) return false
  const instruments=new Map()
  const payments=new Set()
  for (const row of data.instruments) {
    if (!row || instruments.has(row.id)) return false
    const federal=(/^tg-[0-9]{4,12}$/.test(row.id)&&row.source==='Transferegov.br')
      || (data.schemaVersion===2&&/^faf-[0-9]{1,12}$/.test(row.id)&&row.source==='Transferegov.br · Fundo a Fundo'&&row.paid===null)
    const officialMunicipalName=['PREFEITURA MUNICIPAL DE SAO BERNARDO DO CAMPO','MUNICIPIO DE SAO BERNARDO DO CAMPO','FUNDO MUNICIPAL DE SAUDE DE SAO BERNARDO DO CAMPO']
    const stateAgreement=data.schemaVersion===2&&/^sp-convenio-\d{6,16}$/.test(row.id)
      && row.source==='Portal da Transparência SP'&&row.amendment==='Não identificada'
      && [row.total,row.granted].every(value=>value===null||Number.isSafeInteger(value))
      && (/^\d{14}$/.test(row.cnpj)||(row.cnpj===''&&typeof row.recipient==='string'&&officialMunicipalName.includes(row.recipient.toUpperCase().normalize('NFD').replace(/\p{M}/gu,''))))
    if (row.origin==='União' ? !federal||!/^\d{14}$/.test(row.cnpj) : row.origin!=='Estado'||!stateAgreement) return false
    instruments.set(row.id,row)
  }
  for (const row of data.payments) {
    if (!row || payments.has(row.id)||!Number.isSafeInteger(row.paid)) return false
    if (row.origin==='União') {
      const instrument=instruments.get(row.instrumentId)
      if (!/^ob-[0-9]{2,27}$/.test(row.id)||row.source!=='Transferegov.br'||instrument?.source!=='Transferegov.br'||instrument.cnpj!==row.cnpj) return false
    } else if (row.origin!=='Estado'||!/^sp-tributo-\d{4}-\d{2}-[1-4]$/.test(row.id)||row.source!=='Sefaz-SP'||row.instrumentId||row.cnpj!=='46523239000147') return false
    payments.add(row.id)
  }
  return true
}
async function ingest(request,env,ip) {
  if (!(await env.SYNC_LIMIT.limit({key:ip})).success) return json({erro:'Limite de tentativas'},429,{'Retry-After':'60'})
  if (!await validIdentity(request)) return json({erro:'Acesso restrito à rotina de atualização'},401)
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type')||'')) return json({erro:'Formato inválido'},415)
  const declared=Number(request.headers.get('content-length'))
  if (!Number.isSafeInteger(declared)||declared<1||declared>maxBytes) return json({erro:'Tamanho inválido'},413)
  const text=await request.text()
  if (text.length>maxBytes) return json({erro:'Tamanho inválido'},413)
  let data
  try { data=JSON.parse(text) } catch { return json({erro:'JSON inválido'},400) }
  if (!validSnapshot(data)) return json({erro:'Carga inválida'},422)
  const {metadata}=await env.SNAPSHOTS.getWithMetadata(snapshotName,{type:'stream',cacheTtl:30})
  if (metadata?.schemaVersion===2 && data.schemaVersion!==2) return json({erro:'Formato anterior à carga ativa'},409)
  if (metadata?.last && Date.parse(data.generatedAt)<=Date.parse(metadata.last)) return json({ok:true,resultado:'Já atualizado'})
  if (metadata?.federal && Date.parse(data.coverage.federalFiles['siconv_convenio.zip'])<Date.parse(metadata.federal)) return json({erro:'Fonte federal anterior à carga ativa'},409)
  await env.SNAPSHOTS.put(snapshotName,text,{metadata:{last:data.generatedAt,federal:data.coverage.federalFiles['siconv_convenio.zip'],schemaVersion:data.schemaVersion||1}})
  console.log(JSON.stringify({action:'snapshot_published',instruments:data.instruments.length,payments:data.payments.length,stamp:data.generatedAt}))
  return json({ok:true,atualizadoEm:data.generatedAt})
}

export default {
  async fetch(request,env) {
    const url=new URL(request.url)
    if (url.hostname!==host && url.hostname!=='localhost' && url.hostname!=='127.0.0.1') return json({erro:'Página não encontrada'},404)
    const ip=request.headers.get('CF-Connecting-IP')||'unknown'
    if (url.pathname==='/api/atualizacao') {
      if (request.method!=='POST') return json({erro:'Método não aceito'},405,{Allow:'POST'})
      try {return await ingest(request,env,ip)} catch {
        console.error('snapshot_update_failed')
        return json({erro:'Falha na atualização'},503)
      }
    }
    const head=request.method==='HEAD'
    if (request.method!=='GET' && !head) return json({erro:'Método não aceito'},405,{Allow:'GET, HEAD'})
    if (url.pathname==='/api/dados') {
      if (!(await env.PUBLIC_LIMIT.limit({key:ip})).success) return json({erro:'Limite temporário de consultas'},429,{'Retry-After':'60'})
      try {
        const {value,metadata}=await env.SNAPSHOTS.getWithMetadata(snapshotName,{type:'stream',cacheTtl:300})
        if (!value) return json({erro:'Carga temporariamente indisponível'},503,{'Retry-After':'60'})
        const headers={...securityHeaders,'Content-Type':jsonType,'Cache-Control':'public, max-age=300'}
        if (metadata?.last) headers['Last-Modified']=new Date(metadata.last).toUTCString()
        return new Response(head?null:metadata?.gzip?value.pipeThrough(new DecompressionStream('gzip')):value,{headers})
      } catch {
        console.error('snapshot_read_failed')
        return json({erro:'Carga temporariamente indisponível'},503,{'Retry-After':'60'})
      }
    }
    const asset = assets[url.pathname]
    if (!asset || !Object.hasOwn(assets,url.pathname)) return json({erro:'Página não encontrada'},404)
    return new Response(head?null:asset.content,{headers:{...securityHeaders,'Content-Type':asset.type,'Cache-Control':'public, max-age=300'}})
  },
}
