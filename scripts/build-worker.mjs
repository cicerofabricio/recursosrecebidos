import { readFile, writeFile } from 'node:fs/promises'
const files = [
  ['/','index.html','text/html; charset=utf-8'],
  ['/app.css','app.css','text/css; charset=utf-8'],
  ['/app.js','app.js','text/javascript; charset=utf-8'],
  ['/xlsx.js','xlsx.js','text/javascript; charset=utf-8'],
  ['/marca.svg','marca.svg','image/svg+xml'],
]
const assets = {}
for (const [path,file,type] of files) assets[path] = {type,content:await readFile(new URL('../public/'+file,import.meta.url),'utf8')}
const worker = await readFile(new URL('../worker.mjs',import.meta.url),'utf8')
await writeFile(new URL('../worker.deploy.mjs',import.meta.url),'const assets = '+JSON.stringify(assets)+'\n'+worker)
