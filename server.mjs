import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
const root=path.dirname(fileURLToPath(import.meta.url)),dist=path.join(root,'dist');
const python=process.env.PYTHON||'python';
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.zip':'application/zip'};
const json=(res,status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value))};
let active=0;
async function demo(req,res){
 if(req.headers.origin&&!/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(req.headers.origin))return json(res,403,{error:'Yalnızca localhost istekleri desteklenir.'});
 if(active>=3)return json(res,429,{error:'Motor meşgul; tekrar deneyin.'});
 active++;let temp;
 try{
  let chunks=[],size=0;for await(const chunk of req){size+=chunk.length;if(size>2.1*1024*1024){json(res,413,{error:'En fazla 2 MB CSV.'});return;}chunks.push(chunk)}const body=Buffer.concat(chunks).toString('utf8');
  let input;try{input=JSON.parse(body)}catch{return json(res,400,{error:'Geçersiz JSON.'})}
  if(!['profile','report','quality','dictionary','dedupe'].includes(input.engine))return json(res,400,{error:'Desteklenmeyen motor.'});
  if(input.csv!==undefined&&typeof input.csv!=='string')return json(res,400,{error:'CSV metin olmalıdır.'});
  temp=await fs.mkdtemp(path.join(os.tmpdir(),'datawork-'));
  await fs.writeFile(path.join(temp,'input.csv'),input.csv??'id,region,amount,email\n1,Marmara,1200,ayse@example.test\n2,Ege,850,ali@example.test\n3,Marmara,640,\n4,Ege,-20,test@example.test\n');
  await fs.writeFile(path.join(temp,'config.json'),JSON.stringify({engine:input.engine,...(input.engine==='quality'?{rules:{id:{required:true,unique:true},amount:{type:'number',min:0}}}:{})}));
  await new Promise((resolve,reject)=>{const child=spawn(python,[path.join(root,'library/runner.py'),'--input','input.csv','--config','config.json'],{cwd:temp,windowsHide:true});let errors='';const timer=setTimeout(()=>{child.kill();reject(Error('İşlem süresi aşıldı.'))},20000);child.stderr.on('data',c=>errors+=c);child.on('error',e=>{clearTimeout(timer);reject(e)});child.on('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error(errors.trim()||'Motor hatası'))})});
  const result=JSON.parse(await fs.readFile(path.join(temp,'output/result.json'),'utf8'));json(res,200,result);
 }catch(e){json(res,400,{error:e.message})}finally{if(temp)await fs.rm(temp,{recursive:true,force:true});active--}
}
const server=http.createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/api/demo'&&req.method==='POST')return await demo(req,res);
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);return res.end()}
  const decoded=decodeURIComponent(url.pathname),target=path.resolve(dist,'.'+(decoded==='/'?'/index.html':decoded));
  if(!target.startsWith(dist+path.sep)){res.writeHead(403);return res.end('Forbidden')}
  const contents=await fs.readFile(target);res.writeHead(200,{'Content-Type':types[path.extname(target)]||'application/octet-stream','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:contents);
 }catch{res.writeHead(404);res.end('Not found')}
});
let port=Number(process.env.PORT)||4322;
server.on('error',err=>{if(err.code==='EADDRINUSE'&&!process.env.PORT&&port<4422){port++;server.listen(port,'127.0.0.1')}else{console.error(err.message);process.exit(1)}});
server.on('listening',()=>console.log(`Datawork → http://localhost:${port}`));server.listen(port,'127.0.0.1');
