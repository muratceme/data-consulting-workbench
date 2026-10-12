import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url)),dist=path.join(root,'dist');
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.zip':'application/zip'};
// Discovery and downloads only. API credentials and native jobs run in the user's package.
const server=http.createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://localhost');
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);return res.end()}
  const decoded=decodeURIComponent(url.pathname),target=path.resolve(dist,'.'+(decoded==='/'?'/index.html':decoded));
  if(!target.startsWith(dist+path.sep)){res.writeHead(403);return res.end('Forbidden')}
  const contents=await fs.readFile(target);res.writeHead(200,{'Content-Type':types[path.extname(target)]||'application/octet-stream','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:contents);
 }catch{res.writeHead(404);res.end('Not found')}
});
let port=Number(process.env.PORT)||4322;
server.on('error',err=>{if(err.code==='EADDRINUSE'&&!process.env.PORT&&port<4422){port++;server.listen(port,'127.0.0.1')}else{console.error(err.message);process.exit(1)}});
server.on('listening',()=>console.log(`Datawork → http://localhost:${port}`));server.listen(port,'127.0.0.1');
