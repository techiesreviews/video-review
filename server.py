from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import argparse, json, mimetypes, threading, re, uuid, os

ROOT=Path(__file__).resolve().parent
MANIFEST=ROOT/'data/manifest.json'
REVIEWS=ROOT/'data/comments.json'
LOCK=threading.Lock()
PORT=8879

def read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send_json(self, data, status=200):
        payload=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        route=self.path.split('?',1)[0]
        if route=='/api/videos':
            return self.send_json([{k:v for k,v in x.items() if k!='path'} for x in read(MANIFEST,[])])
        if route=='/api/comments':return self.send_json(read(REVIEWS,{'comments':[]}))
        if route.startswith('/media/'):
            key=route[7:];item=next((x for x in read(MANIFEST,[]) if x['id']==key),None)
            if not item:return self.send_error(404)
            path=Path(item['path'])
        elif route in ['/','/index.html','/app.js','/style.css']:
            path=ROOT/('index.html' if route=='/' else route[1:])
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
        self.send_response(status);self.send_header('Content-Type',mimetypes.guess_type(str(path))[0] or 'application/octet-stream');self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-cache')
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
        if self.path!='/api/comments':return self.send_error(404)
        if self.headers.get('Origin') not in [f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}']:return self.send_error(403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_error(415)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>65536:raise ValueError('Invalid request size')
            cmd=json.loads(self.rfile.read(size));action=cmd.get('action')
            with LOCK:
                data=read(REVIEWS,{'comments':[]});comments=data['comments']
                if action=='add':
                    video=next((x for x in read(MANIFEST,[]) if x['id']==cmd['videoId']),None)
                    if not video:raise ValueError('Unknown video version')
                    start=cmd['startMs'];end=cmd.get('endMs');text=cmd['text'].strip()
                    if isinstance(start,bool) or not isinstance(start,int) or start<0 or start>video['durationMs']:raise ValueError('Timestamp outside video')
                    if end is not None and (isinstance(end,bool) or not isinstance(end,int) or end<=start or end>video['durationMs']):raise ValueError('Range end must follow start')
                    if not text or len(text)>10000:raise ValueError('Please enter a comment (up to 10,000 characters)')
                    comments.append({'id':str(uuid.uuid4()),'videoId':video['id'],'startMs':start,'endMs':end,'text':text,'category':cmd.get('category','edit'),'status':'open','createdAt':cmd['createdAt']})
                elif action=='update':
                    c=next((x for x in comments if x['id']==cmd['id']),None)
                    if not c:raise ValueError('Comment not found')
                    if 'status' in cmd:
                        if cmd['status'] not in ['open','done']:raise ValueError('Invalid status')
                        c['status']=cmd['status']
                    if 'text' in cmd:
                        if not cmd['text'].strip() or len(cmd['text'])>10000:raise ValueError('Invalid comment')
                        c['text']=cmd['text'].strip()
                else:raise ValueError('Unsupported action')
                temp=REVIEWS.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8');os.replace(temp,REVIEWS)
            self.send_json(data)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:self.send_json({'error':str(e)},400)

def create_server(port=8879, data_dir=None):
    global MANIFEST, REVIEWS, PORT
    directory=Path(data_dir or os.environ.get('VIDEO_REVIEW_DATA_DIR', ROOT/'data')).expanduser().resolve()
    directory.mkdir(parents=True,exist_ok=True)
    MANIFEST=directory/'manifest.json'
    REVIEWS=directory/'comments.json'
    for path,initial in [(MANIFEST,[]),(REVIEWS,{'comments':[]})]:
        if not path.exists():path.write_text(json.dumps(initial,indent=2),encoding='utf-8')
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
