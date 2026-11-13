from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import argparse, json, mimetypes, re, os, sys
from urllib.parse import urlsplit,parse_qs,unquote,quote
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parent/'scripts'/'video'))
import review_store as store
import overview_page
import rough_cut
import scene_store

ROOT=Path(__file__).resolve().parent
WEB=ROOT/'web'
DATA=ROOT/'data'
PORT=8879
# The overview page saved in a video's folder is opened by double click, so the browser calls the server from the
# origin "null". Only the two scene routes accept it.
FILE_PAGE_ORIGIN='null'
SCENE_FILE_TYPES=('.mkv','.mp4','.mov','.jpg')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def send_json(self, data, status=200, extra=None):
        payload=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store')
        for name,value in (extra or {}).items():self.send_header(name,value)
        self.end_headers();self.wfile.write(payload)
    def own_origin(self):return self.headers.get('Origin') in [f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}']
    def cors(self):
        """Headers that let the saved overview page (origin "null") read an answer."""
        return {'Access-Control-Allow-Origin':FILE_PAGE_ORIGIN} if self.headers.get('Origin')==FILE_PAGE_ORIGIN else {}
    def scene_video(self,key):
        """(folder, cue file) of a video registered with overview_page, or None."""
        try:return overview_page.lookup(DATA,key)
        except ValueError:return None
    def scene_path(self,rest):
        """For /scenes/<video>: send the overview page and return None. For /scenes/<video>/<file>: the take file or
        thumbnail to send, or None after a 404. Only files directly under the video's scenes/ folder, of a take or
        thumbnail type, and really inside it (not through .. or a link), are served."""
        key,_,relative=rest.partition('/')
        found=self.scene_video(key)
        if not found:return self.send_error(404)
        folder,cue=found
        if not relative:
            try:page=overview_page.build_html(folder,cue=cue,link_prefix=f'/scenes/{quote(key)}/',server='')
            except (scene_store.SceneFileError,OSError):return self.send_error(404)
            payload=page.encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)
            return None
        parts=unquote(relative).split('/')
        if parts[0]!='scenes' or len(parts)<2 or any(part in ('','.','..') or '\\' in part or '\0' in part for part in parts):return self.send_error(404)
        scenes=(folder/'scenes').resolve();path=(folder.joinpath(*parts)).resolve()
        if path.suffix.lower() not in SCENE_FILE_TYPES or scenes not in path.parents or not path.is_file():return self.send_error(404)
        return path
    def scene_list(self,query):
        key=parse_qs(query).get('video',[''])[0];found=self.scene_video(key)
        if not found:return self.send_json({'error':'Unknown video'},404)
        try:saved=scene_store.load(found[0])
        except scene_store.SceneFileError as error:return self.send_json({'error':str(error)},409)
        self.send_json({'video':key,'saved_at':saved.get('saved_at'),'scenes':saved['scenes']},extra=self.cors())
    def do_GET(self):
        route=self.path.split('?',1)[0]
        if route=='/api/scenes':return self.scene_list(urlsplit(self.path).query)
        if route=='/api/videos':
            return self.send_json([{**{k:v for k,v in x.items() if k!='path'},'fileVersion':store.file_version(x['path'])} for x in store.videos(DATA)])
        if route=='/api/comments':return self.send_json({'comments':store.comments(DATA)})
        if route=='/api/local':return self.send_json({'local':True})
        if route=='/api/agent':return self.send_json({'connected':store.agent_connected(DATA)})
        if route=='/api/library/search':return self.send_json(store.search_library(DATA,parse_qs(urlsplit(self.path).query).get('q',[''])[0]))
        if route=='/api/library/removed':return self.send_json([{k:v for k,v in item.items() if k!='path'} for item in store.read(DATA/'library-archive.json',[])])
        if route.startswith('/api/transcript/'):
            try:return self.send_json(store.get_transcript(DATA,route[len('/api/transcript/'):]))
            except ValueError as error:return self.send_json({'error':str(error)},404)
        if route=='/api/view-request':return self.send_json(store.read(DATA/'view-request.json',{}))
        if route.startswith('/scenes/'):
            path=self.scene_path(route[len('/scenes/'):])
            if path is None:return
        elif route.startswith('/media/'):
            key=route[7:];item=next((x for x in store.videos(DATA) if x['id']==key),None)
            if not item:return self.send_error(404)
            path=Path(item['path'])
        elif route in ['/','/index.html','/app.js','/backend.js','/review-core.js','/transcript-core.js','/transcript-panel.js','/edit-bar-core.js','/cut-core.js','/cut-playback.js','/join-fade.js','/waveform-core.js','/waveform-panel.js','/library-panel.js','/style.css']:
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
    def do_OPTIONS(self):
        # The browser asks first before the saved overview page may send a star from the origin "null".
        if self.path!='/api/scenes/star' or self.headers.get('Origin')!=FILE_PAGE_ORIGIN:return self.send_error(403)
        self.send_response(204)
        for name,value in {**self.cors(),'Access-Control-Allow-Methods':'POST','Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Private-Network':'true','Access-Control-Max-Age':'600','Content-Length':'0'}.items():self.send_header(name,value)
        self.end_headers()
    def star(self,cmd):
        """Lex's star for a take (or off, with take null): writes the scene file, then rebuilds the rough cut list and rewrites the saved page."""
        if not isinstance(cmd,dict) or 'take' not in cmd:raise ValueError('Invalid star request')
        found=self.scene_video(str(cmd.get('video','')))
        if not found:return self.send_json({'error':'Unknown video'},404,extra=self.cors())
        folder,cue=found;scene,take=cmd.get('scene'),cmd.get('take')
        if not isinstance(scene,str) or (take is not None and type(take) is not int):raise ValueError('Invalid star request')
        saved=scene_store.update(folder,lambda current:scene_store.set_star(current,scene,take))
        # The rough cut list and the page both follow the star on their own; one failing does not stop the other.
        problems=[]
        try:rough_cut.rebuild_rough_cut(folder,cue=cue)
        except (OSError,ValueError,scene_store.SceneFileError) as error:problems.append(f'the rough cut could not be rebuilt: {error}')
        try:overview_page.build(folder,cue=cue,server=f'http://127.0.0.1:{PORT}')
        except (OSError,scene_store.SceneFileError) as error:problems.append(f'the page could not be rewritten: {error}')
        if problems:return self.send_json({'error':'The star is saved, but '+' and '.join(problems)},500,extra=self.cors())
        self.send_json({'ok':True,'scenes':saved['scenes']},extra=self.cors())
    def do_POST(self):
        if self.path not in ['/api/comments','/api/view','/api/library','/api/scenes/star'] and not self.path.startswith('/api/transcript/'):return self.send_error(404)
        if not self.own_origin() and not (self.path=='/api/scenes/star' and self.headers.get('Origin')==FILE_PAGE_ORIGIN):return self.send_error(403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_error(415)
        try:
            size=int(self.headers.get('Content-Length','0'))
            limit=8*1024*1024 if self.path.startswith('/api/transcript/') else 65536
            if size<=0 or size>limit:raise ValueError('Invalid request size')
            cmd=json.loads(self.rfile.read(size))
            if self.path=='/api/scenes/star':return self.star(cmd)
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
        except scene_store.SceneFileError as e:self.send_json({'error':str(e)},409,extra=self.cors())
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:self.send_json({'error':str(e)},400,extra=self.cors())

def create_server(port=8879, data_dir=None):
    global DATA, PORT
    DATA=store.data_dir(data_dir)
    httpd=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    PORT=httpd.server_port
    overview_page.remember_port(DATA,PORT)
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
