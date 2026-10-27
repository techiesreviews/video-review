from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import argparse, json, mimetypes, re, os, sys
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parent))
import review_store as store

ROOT=Path(__file__).resolve().parent
WEB=ROOT/'web'
DATA=ROOT/'data'
PORT=8879

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send_json(self, data, status=200):
        payload=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        route=self.path.split('?',1)[0]
        if route=='/api/videos':
            return self.send_json([{**{k:v for k,v in x.items() if k!='path'},'fileVersion':store.file_version(x['path'])} for x in store.videos(DATA)])
        if route=='/api/comments':return self.send_json({'comments':store.comments(DATA)})
        if route=='/api/local':return self.send_json({'local':True})
        if route=='/api/library/search':return self.send_json(store.search_library(DATA,parse_qs(urlsplit(self.path).query).get('q',[''])[0]))
        if route=='/api/library/removed':return self.send_json([{k:v for k,v in item.items() if k!='path'} for item in store.read(DATA/'library-archive.json',[])])
        if route.startswith('/api/transcript/'):
            try:return self.send_json(store.get_transcript(DATA,route[len('/api/transcript/'):]))
            except ValueError as error:return self.send_json({'error':str(error)},404)
        if route=='/api/view-request':return self.send_json(store.read(DATA/'view-request.json',{}))
        if route.startswith('/media/'):
            key=route[7:];item=next((x for x in store.videos(DATA) if x['id']==key),None)
            if not item:return self.send_error(404)
            path=Path(item['path'])
        elif route in ['/','/index.html','/app.js','/backend.js','/review-core.js','/transcript-core.js','/transcript-panel.js','/cut-core.js','/library-panel.js','/style.css']:
            path=WEB/('index.html' if route=='/' else route[1:])
        else:return self.send_error(404)
        if not path.is_file():return self.send_error(404)
        size=path.stat().st_size;start=0;end=size-1;status=200
        requested=self.headers.get('Range')
        if requested:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
            if not match:return self.send_error(416)
            lo,hi=match.groups()
            if lo:start=int(lo);end=min(int(hi),size-1) if hi else size-1
            elif hi:start=max(0,size-int(hi))
            if start> end or start>=size:return self.send_error(416)
            status=206
        self.send_response(status);self.send_header('Content-Type','text/javascript; charset=utf-8' if path.suffix=='.js' else mimetypes.guess_type(str(path))[0] or 'application/octet-stream');self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-cache')
        if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with path.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(1024*1024,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_POST(self):
        if self.path not in ['/api/comments','/api/view','/api/library'] and not self.path.startswith('/api/transcript/'):return self.send_error(404)
        if self.headers.get('Origin') not in [f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}']:return self.send_error(403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_error(415)
        try:
            size=int(self.headers.get('Content-Length','0'))
            limit=8*1024*1024 if self.path.startswith('/api/transcript/') else 65536
            if size<=0 or size>limit:raise ValueError('Invalid request size')
            cmd=json.loads(self.rfile.read(size))
            if self.path=='/api/library':
                if cmd.get('action')=='remove':store.archive_video(DATA,cmd['videoId'])
                elif cmd.get('action')=='restore':store.restore_video(DATA,cmd['videoId'])
                elif cmd.get('action')=='add':
                    item=store.add_local_video(DATA,cmd.get('path',''),cmd.get('title',''))
                    return self.send_json({key:value for key,value in item.items() if key!='path'})
                else:raise ValueError('Unknown library action')
                return self.send_json({'ok':True})
            if self.path.startswith('/api/transcript/'):
                return self.send_json(store.save_transcript(DATA,self.path[len('/api/transcript/'):],cmd))
            if self.path=='/api/view':store.set_view(DATA,cmd);return self.send_json({'ok':True})
            # The browser always speaks as the reviewer; only the MCP server writes as the AI.
            if cmd.get('action') in ['add','reply']:cmd['author']='you'
            data,_=store.apply(DATA,cmd)
            self.send_json(data)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:self.send_json({'error':str(e)},400)

def create_server(port=8879, data_dir=None):
    global DATA, PORT
    DATA=store.data_dir(data_dir)
    httpd=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    PORT=httpd.server_port
    return httpd

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Local video review server')
    parser.add_argument('--port',type=int,default=int(os.environ.get('VIDEO_REVIEW_PORT','8879')))
    parser.add_argument('--data-dir',default=None)
    args=parser.parse_args()
    httpd=create_server(args.port,args.data_dir)
    print(f'Video Review: http://127.0.0.1:{PORT}',flush=True)
    try:httpd.serve_forever()
    except KeyboardInterrupt:pass
    finally:httpd.server_close()
