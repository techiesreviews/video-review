import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'/'video'))
import server
import overview_page
import scene_store

CUE='### 1 · INTRO\nPicture this.\n### 2 · WHAT IT COSTS\nTwo out of three.\n'
CLEAN={'ends_early':False,'long_pause':False,'length_off':False}

def fresh_scene_store():
    """Scene 1 has two takes (the newest is best), scene 2 none."""
    store=scene_store.new_store(scene_store.parse_cue_file(CUE))
    scene_store.add_take(store,'01-intro',1,duration=30.0,sound=CLEAN)
    scene_store.add_take(store,'01-intro',2,duration=40.0,sound=CLEAN)
    return store

class ReviewServerTests:
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.data=Path(cls.temp.name)
        cls.media=cls.data/'example.mp4'
        cls.media.write_bytes(bytes(range(256)))
        cls.httpd=server.create_server(0,cls.data)
        cls.raw=cls.data/'Weekly update'
        (cls.raw/'scenes'/'01-intro').mkdir(parents=True)
        for name in ('take-01.mkv','take-02.mkv'):(cls.raw/'scenes'/'01-intro'/name).write_bytes(bytes(range(256)))
        (cls.raw/'scenes'/'01-intro'/'take-02.jpg').write_bytes(b'jpeg')
        (cls.raw/'prompter.md').write_text(CUE,encoding='utf-8')
        cls.key=overview_page.register(cls.data,cls.raw)
        cls.reset_scenes()
        cls.origin=f'http://127.0.0.1:{cls.httpd.server_port}'
        (cls.data/'manifest.json').write_text(json.dumps([{'id':'example-v1','title':'Example','label':'v1','path':str(cls.media),'fps':30,'durationMs':60000,'fingerprint':'example','keepRanges':[[0,1800]]}]))
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def reset_scenes(cls):
        scene_store.save(cls.raw,fresh_scene_store())
        (cls.raw/overview_page.FILE_NAME).unlink(missing_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self,path,body=None,headers=None):
        req=Request(self.origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
        return urlopen(req,timeout=3)

    def test_byte_range_preserves_exact_data(self):
        with self.request('/media/example-v1',headers={'Range':'bytes=12-31'}) as r:
            assert r.status==206
            assert r.headers['Content-Range']=='bytes 12-31/256'
            assert r.read()==bytes(range(12,32))

    def test_manifest_does_not_expose_local_path(self):
        with self.request('/api/videos') as r:
            item=json.load(r)[0]
            assert 'path' not in item

    def test_transcript_endpoint_preserves_word_times_and_rejects_wrong_cut(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        document={'words':[{'id':'W1','text':'Finish','startMs':100,'endMs':500}],
                  'revision':'test','cuts':[{'id':'J1','atMs':700,'risk':True}]}
        with self.request('/api/transcript/example-v1',document,headers) as response:
            assert json.load(response)['words'][0]['startMs']==100
        with self.request('/api/transcript/example-v1') as response:
            saved=json.load(response);assert saved['words'][0]['id']=='W1'
        try:self.request('/api/transcript/example-v1',{'words':[{'id':'W1','text':'Wrong cut','startMs':0,'endMs':900000}]},headers)
        except HTTPError as error:assert error.code==400
        else:raise AssertionError('A transcript from a longer cut was accepted')
        with self.request('/api/transcript/example-v1') as response:assert json.load(response)==saved

    def test_agent_status_is_true_only_while_the_agent_is_alive(self):
        seen=self.data/'agent-seen.json'
        seen.unlink(missing_ok=True)
        with self.request('/api/agent') as r:assert json.load(r)=={'connected':False}
        server.store.touch_agent(self.data)
        with self.request('/api/agent') as r:assert json.load(r)=={'connected':True}
        seen.write_text(json.dumps({'pid':1,'at':'2020-01-01T00:00:00+00:00'}))
        with self.request('/api/agent') as r:assert json.load(r)=={'connected':False}
        seen.write_text('{"at":"not a time"}')
        with self.request('/api/agent') as r:assert json.load(r)=={'connected':False}
        seen.unlink(missing_ok=True)

    def test_file_version_changes_when_the_file_is_replaced(self):
        with self.request('/api/videos') as r:before=json.load(r)[0]['fileVersion']
        self.media.write_bytes(bytes(range(128)))
        try:
            with self.request('/api/videos') as r:assert json.load(r)[0]['fileVersion'] not in (None,before)
        finally:self.media.write_bytes(bytes(range(256)))

    def test_comment_range_is_persisted_in_milliseconds(self):
        note={'action':'add','videoId':'example-v1','startMs':20123,'endMs':20567,'text':'Remove repeat','category':'speech','createdAt':'2026-01-01T00:00:00Z'}
        with self.request('/api/comments',note,{'Content-Type':'application/json','Origin':self.origin}) as r:
            comments=json.load(r)['comments']
        saved=comments[-1]
        assert (saved['startMs'],saved['endMs'])==(20123,20567)
        persisted=json.loads((self.data/'comments.json').read_text())['comments']
        assert saved in persisted

    def test_comment_can_be_deleted(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        note={'action':'add','videoId':'example-v1','startMs':1000,'endMs':None,'text':'Delete me','createdAt':'2026-01-01T00:00:00Z'}
        with self.request('/api/comments',note,headers) as r:
            saved=json.load(r)['comments'][-1]
        with self.request('/api/comments',{'action':'delete','id':saved['id']},headers) as r:
            assert all(c['id']!=saved['id'] for c in json.load(r)['comments'])
        persisted=json.loads((self.data/'comments.json').read_text())['comments']
        assert all(c['id']!=saved['id'] for c in persisted)

    def test_browser_replies_are_always_from_the_reviewer(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        note={'action':'add','videoId':'example-v1','startMs':3000,'endMs':None,'text':'Thread','author':'ai','createdAt':'2026-01-01T00:00:00Z'}
        with self.request('/api/comments',note,headers) as r:
            saved=json.load(r)['comments'][-1]
        assert saved['author']=='you'
        with self.request('/api/comments',{'action':'reply','id':saved['id'],'text':'Keep the breath','author':'ai'},headers) as r:
            replies=json.load(r)['comments'][-1]['replies']
        assert [(x['author'],x['text']) for x in replies]==[('you','Keep the breath')]

    def test_page_reports_what_the_reviewer_is_watching(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        with self.request('/api/view',{'videoId':'example-v1','playheadMs':1234.4},headers) as r:
            assert json.load(r)['ok']
        view=json.loads((self.data/'view.json').read_text())
        assert (view['videoId'],view['playheadMs'])==('example-v1',1234)
        with self.request('/api/view-request') as r:
            assert json.load(r)=={}

    def test_browser_saves_a_change_request_and_refuses_a_bad_one(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        note={'action':'add','videoId':'example-v1','startMs':1000,'endMs':2000,'text':'Tighten this','editRequest':{'type':'change','quote':'two words','firstWordId':'W1','lastWordId':'W2','wordIds':['W1','W2']}}
        with self.request('/api/comments',note,headers) as r:saved=json.load(r)['comments'][-1]
        assert saved['editRequest']['type']=='change' and saved['editRequest']['wordIds']==['W1','W2']
        assert (saved['editRequest']['startFrame'],saved['editRequest']['endFrame'])==(30,60)
        try:self.request('/api/comments',{**note,'editRequest':{'type':'change','wordIds':'W1'}},headers)
        except HTTPError as error:assert error.code==400
        else:raise AssertionError('A malformed change request was accepted')

    def test_invalid_range_is_rejected(self):
        note={'action':'add','videoId':'example-v1','startMs':2000,'endMs':1000,'text':'Example','createdAt':'now'}
        try:self.request('/api/comments',note,{'Content-Type':'application/json','Origin':self.origin})
        except HTTPError as error:assert error.code==400
        else:raise AssertionError('Invalid range was accepted')

    def test_cross_origin_write_is_rejected(self):
        try:self.request('/api/comments',{'action':'add'},{'Content-Type':'application/json','Origin':'https://example.org'})
        except HTTPError as error:assert error.code==403
        else:raise AssertionError('Cross-origin write was accepted')

    def test_unconfigured_file_is_not_served(self):
        try:self.request('/media/missing')
        except HTTPError as error:assert error.code==404
        else:raise AssertionError('Unconfigured media was served')

    def star(self,body,origin=None):
        headers={'Content-Type':'application/json','Origin':origin or self.origin}
        return self.request('/api/scenes/star',body,headers)

    def saved_scene(self,scene='01-intro'):return scene_store.find_scene(scene_store.load(self.raw),scene)

    def test_scene_list_is_served_for_a_registered_video(self):
        self.reset_scenes()
        with self.request('/api/scenes?video='+self.key) as r:
            data=json.load(r)
        assert [s['id'] for s in data['scenes']]==['01-intro','02-what-it-costs']
        assert [t['number'] for t in data['scenes'][0]['takes']]==[1,2]
        assert 'Access-Control-Allow-Origin' not in r.headers

    def test_unknown_or_missing_video_has_no_scene_list(self):
        for query in ('?video=nope-12345678','?video=','','?video=../x'):
            try:self.request('/api/scenes'+query)
            except HTTPError as error:assert error.code==404
            else:raise AssertionError('A scene list was served for '+query)

    def test_the_saved_page_may_ask_for_the_scene_list(self):
        req=Request(self.origin+'/api/scenes?video='+self.key,headers={'Origin':'null'})
        with urlopen(req,timeout=3) as r:assert r.headers['Access-Control-Allow-Origin']=='null'
        req=Request(self.origin+'/api/scenes?video='+self.key,headers={'Origin':'https://example.org'})
        with urlopen(req,timeout=3) as r:assert 'Access-Control-Allow-Origin' not in r.headers

    def test_a_star_click_writes_the_scene_file_and_rewrites_the_page(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1}) as r:
            answer=json.load(r)
        assert answer['ok'] and answer['scenes'][0]['star']==1
        saved=self.saved_scene()
        assert (saved['star'],saved['best'])==(1,1)
        page=(self.raw/overview_page.FILE_NAME).read_text(encoding='utf-8')
        assert 'style="flex:30" title="Scene 1, take 1"' in page
        assert 'Picture this.' in page
        assert f'data-server="http://127.0.0.1:{self.httpd.server_port}"' in page

    def test_a_star_can_be_taken_off_again(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1}):pass
        with self.star({'video':self.key,'scene':'01-intro','take':None}) as r:assert json.load(r)['ok']
        saved=self.saved_scene()
        assert (saved['star'],saved['best'])==(None,2)

    def test_a_bad_star_request_changes_nothing(self):
        self.reset_scenes()
        before=(self.raw/'scenes.json').read_bytes()
        for body,code in [({'video':self.key,'scene':'01-intro','take':9},400),
                          ({'video':self.key,'scene':'09-nothing','take':1},400),
                          ({'video':self.key,'scene':'01-intro','take':'1'},400),
                          ({'video':self.key,'scene':'01-intro','take':True},400),
                          ({'video':self.key,'scene':7,'take':1},400),
                          ({'video':self.key,'take':1},400),
                          ({'video':'nope-12345678','scene':'01-intro','take':1},404)]:
            try:self.star(body)
            except HTTPError as error:assert error.code==code,(body,error.code)
            else:raise AssertionError(f'A bad star request was accepted: {body}')
        assert (self.raw/'scenes.json').read_bytes()==before
        assert not (self.raw/overview_page.FILE_NAME).exists()

    def test_a_star_needs_json_and_a_known_origin(self):
        body={'video':self.key,'scene':'01-intro','take':1}
        try:self.star(body,'https://example.org')
        except HTTPError as error:assert error.code==403
        else:raise AssertionError('A star from another site was accepted')
        try:self.request('/api/scenes/star',body,{'Content-Type':'text/plain','Origin':self.origin})
        except HTTPError as error:assert error.code==415
        else:raise AssertionError('A star without JSON was accepted')

    def test_the_saved_page_opened_by_double_click_may_send_a_star(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1},'null') as r:
            assert r.headers['Access-Control-Allow-Origin']=='null'
        assert self.saved_scene()['star']==1
        # ... but the same null origin may not write comments or anything else
        try:self.request('/api/comments',{'action':'add'},{'Content-Type':'application/json','Origin':'null'})
        except HTTPError as error:assert error.code==403
        else:raise AssertionError('A null origin could write a comment')

    def test_the_star_route_answers_the_preflight_only_for_the_saved_page(self):
        def preflight(origin):
            req=Request(self.origin+'/api/scenes/star',method='OPTIONS',headers={
                'Origin':origin,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
            return urlopen(req,timeout=3)
        with preflight('null') as r:
            assert r.status==204
            assert r.headers['Access-Control-Allow-Origin']=='null'
            assert 'POST' in r.headers['Access-Control-Allow-Methods']
            assert 'Content-Type' in r.headers['Access-Control-Allow-Headers']
            assert r.headers['Access-Control-Allow-Private-Network']=='true'
        try:preflight('https://example.org')
        except HTTPError as error:assert error.code==403
        else:raise AssertionError('A preflight from another site was answered')

    def test_a_star_that_could_not_rewrite_the_page_says_so_but_is_kept(self):
        self.reset_scenes()
        real=server.overview_page.build
        def broken(*args,**kwargs):raise OSError('disk full')
        server.overview_page.build=broken
        try:
            try:self.star({'video':self.key,'scene':'01-intro','take':1})
            except HTTPError as error:
                assert error.code==500
                assert 'saved' in json.load(error)['error']
            else:raise AssertionError('A failed page rewrite was reported as fine')
        finally:server.overview_page.build=real
        assert self.saved_scene()['star']==1

    def test_a_scene_file_that_cannot_be_read_is_reported_not_replaced(self):
        self.reset_scenes()
        (self.raw/'scenes.json').write_text('{nope',encoding='utf-8')
        try:
            try:self.star({'video':self.key,'scene':'01-intro','take':1})
            except HTTPError as error:assert error.code==409
            else:raise AssertionError('A broken scene file was overwritten')
            assert (self.raw/'scenes.json').read_text(encoding='utf-8')=='{nope'
        finally:self.reset_scenes()

    def test_the_server_serves_the_same_page_with_links_that_work(self):
        self.reset_scenes()
        with self.request('/scenes/'+self.key) as r:
            page=r.read().decode('utf-8')
            assert r.headers['Content-Type']=='text/html; charset=utf-8'
        assert 'Picture this.' in page and 'Not recorded yet' in page
        assert f'data-play="/scenes/{self.key}/scenes/01-intro/take-02.mkv"' in page
        assert f'<img src="/scenes/{self.key}/scenes/01-intro/take-02.jpg"' in page
        assert 'data-server=""' in page
        with self.request(f'/scenes/{self.key}/scenes/01-intro/take-02.jpg') as r:assert r.read()==b'jpeg'
        assert not (self.raw/overview_page.FILE_NAME).exists()

    def test_the_served_page_follows_a_star_change(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1}):pass
        with self.request('/scenes/'+self.key) as r:page=r.read().decode('utf-8')
        assert 'title="Scene 1, take 1"' in page

    def test_take_files_are_served_with_ranges(self):
        with self.request(f'/scenes/{self.key}/scenes/01-intro/take-01.mkv',headers={'Range':'bytes=12-31'}) as r:
            assert r.status==206
            assert r.headers['Content-Range']=='bytes 12-31/256'
            assert r.read()==bytes(range(12,32))

    def test_only_take_files_and_thumbnails_under_scenes_are_served(self):
        (self.raw/'scenes'/'01-intro'/'notes.txt').write_text('private',encoding='utf-8')
        for path in ('scenes.json','prompter.md','overview.html','scenes/01-intro/notes.txt','scenes/01-intro',
                     'scenes/../scenes.json','scenes/%2e%2e/scenes.json','scenes/01-intro/%2e%2e/%2e%2e/prompter.md',
                     'scenes\\01-intro\\take-01.mkv','scenes/01-intro/take-09.mkv','scenes/missing/take-01.mkv'):
            try:self.request(f'/scenes/{self.key}/{path}')
            except HTTPError as error:assert error.code in (404,400),(path,error.code)
            else:raise AssertionError('Served '+path)
        try:self.request('/scenes/nope-12345678')
        except HTTPError as error:assert error.code==404
        else:raise AssertionError('Served the page of an unknown video')

    def test_a_link_out_of_the_scenes_folder_is_not_followed(self):
        outside=self.data/'outside.mkv';outside.write_bytes(b'secret')
        link=self.raw/'scenes'/'01-intro'/'take-03.mkv'
        try:link.symlink_to(outside)
        except (OSError,NotImplementedError):return  # no symbolic links here (Windows without the right)
        try:self.request(f'/scenes/{self.key}/scenes/01-intro/take-03.mkv')
        except HTTPError as error:assert error.code==404
        else:raise AssertionError('Followed a link out of the scenes folder')
        finally:link.unlink()

if __name__=='__main__':
    ReviewServerTests.setUpClass()
    try:
        suite=ReviewServerTests()
        checks=[name for name in dir(suite) if name.startswith('test_')]
        for name in checks:
            getattr(suite,name)()
            print('PASS',name)
        print(f'{len(checks)} checks passed.')
    finally:ReviewServerTests.tearDownClass()
