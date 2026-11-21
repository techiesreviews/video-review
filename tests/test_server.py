import http.client
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
from unittest import mock
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'/'video'))
import server
import overview_page
import rough_cut
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

    def test_the_page_s_edits_only_save_is_refused_once_the_words_changed(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        words=[{'id':'W1','text':'Finish','startMs':100,'endMs':500}]
        with self.request('/api/transcript/example-v1',{'words':words,'revision':'r1'},headers):pass
        cut={'id':'c','startMs':100,'endMs':500,'wordIds':['W1'],'quote':'Finish'}
        with self.request('/api/transcript/example-v1',{'words':words,'revision':'r1','draftEdits':[cut],'editsOnly':True},headers) as response:
            assert json.load(response)['draftEdits'][0]['id']=='c'
        newer=[{'id':'W1','text':'Finish','startMs':120,'endMs':500}]
        with self.request('/api/transcript/example-v1',{'words':newer,'revision':'r1'},headers):pass
        try:self.request('/api/transcript/example-v1',{'words':words,'revision':'r1','draftEdits':[cut],'editsOnly':True},headers)
        except HTTPError as error:assert error.code==400 and 'transcript changed' in json.load(error)['error']
        else:raise AssertionError('An edits-only save wrote old words over new ones')
        with self.request('/api/transcript/example-v1') as response:assert json.load(response)['words'][0]['startMs']==120

    def test_a_body_that_is_not_an_object_gets_a_plain_400(self):
        headers={'Content-Type':'application/json','Origin':self.origin}
        for path in ('/api/transcript/example-v1','/api/comments','/api/library','/api/view','/api/scenes/star'):
            for body in (None,[]):
                origin={'Origin':server.FILE_PAGE_ORIGIN} if path=='/api/scenes/star' else {}
                req=Request(self.origin+path,data=json.dumps(body).encode(),headers={**headers,**origin})
                try:urlopen(req,timeout=3)
                except HTTPError as error:assert error.code==400 and 'JSON object' in json.load(error)['error'],(path,body)
                else:raise AssertionError(f'{path} took {body!r}')

    def test_a_full_transcript_read_back_by_the_page_takes_an_edits_only_save(self):
        # Original words with an empty label, joins and a source mapping, as the page reads them and sends them back.
        headers={'Content-Type':'application/json','Origin':self.origin}
        words=[{'id':'W1','text':'Finish','startMs':100,'endMs':500}]
        full={'words':words,'revision':None,'cuts':[{'id':'J1','atMs':700}],'keepRanges':[[0,1800]],'fps':30,
              'original':{'durationMs':65000,'label':'','words':words}}
        with self.request('/api/transcript/example-v1',full,headers):pass
        with self.request('/api/transcript/example-v1') as response:page=json.load(response)
        page['label']='Word-timed transcript';page['revision']='imported';page['original']['label']='Original recording'
        cut={'id':'c','startMs':100,'endMs':500,'wordIds':['W1'],'quote':'Finish'}
        with self.request('/api/transcript/example-v1',{**page,'draftEdits':[cut],'editsOnly':True},headers) as response:
            assert json.load(response)['draftEdits'][0]['id']=='c'

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

    def test_every_file_the_page_imports_is_served_with_its_type(self):
        # A module the server refuses stops the whole page, so each local import of a page module must be served:
        # the cut preferences file too, as JSON, which a browser only takes as a module with a JSON type.
        web=Path(server.__file__).resolve().parent/'web'
        imported={name for path in web.glob('*.js') for name in re.findall(r'''from\s*['"]\./([^'"]+)['"]''',path.read_text(encoding='utf-8'))}
        assert 'cut-preferences.json' in imported,imported
        for name in sorted(imported):
            with self.request('/'+name) as r:
                kind=r.headers['Content-Type'].split(';')[0]
                assert kind==('application/json' if name.endswith('.json') else 'text/javascript'),(name,kind)
                assert r.read()==(web/name).read_bytes(),name

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
        req=Request(self.origin+'/api/scenes?video='+self.key+'&code='+self.code(),headers={'Origin':'null'})
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
        assert f'data-code="{self.code()}"' in page

    def code(self):return overview_page.page_code(self.data,self.key)

    def saved_rough_cut(self):return json.loads((self.raw/rough_cut.FILE_NAME).read_text(encoding='utf-8'))

    def test_a_star_click_rebuilds_the_rough_cut_on_its_own(self):
        self.reset_scenes()
        (self.raw/rough_cut.FILE_NAME).unlink(missing_ok=True)
        with self.star({'video':self.key,'scene':'01-intro','take':1}) as r:assert json.load(r)['ok']
        scenes=self.saved_rough_cut()['scenes']
        assert [(s['scene'],s['take'],s['recorded']) for s in scenes]==[('01-intro',1,True),('02-what-it-costs',None,False)]
        assert scenes[0]['file']=='scenes/01-intro/take-01.mkv' and scenes[0]['duration']==30.0
        with self.star({'video':self.key,'scene':'01-intro','take':2}) as r:assert json.load(r)['ok']
        assert self.saved_rough_cut()['scenes'][0]['take']==2
        with self.star({'video':self.key,'scene':'01-intro','take':None}) as r:assert json.load(r)['ok']
        assert self.saved_rough_cut()['scenes'][0]['take']==2

    def test_the_rough_cut_and_the_page_strip_agree_after_a_star_click(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1}):pass
        page=(self.raw/overview_page.FILE_NAME).read_text(encoding='utf-8')
        first=self.saved_rough_cut()['scenes'][0]
        assert f'title="Scene {first["number"]}, take {first["take"]}"' in page
        assert 'title="Scene 2: not recorded yet"' in page and self.saved_rough_cut()['scenes'][1]['recorded'] is False

    def test_a_bad_star_request_leaves_the_rough_cut_alone(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1}):pass
        before=(self.raw/rough_cut.FILE_NAME).read_bytes()
        try:self.star({'video':self.key,'scene':'01-intro','take':9})
        except HTTPError as error:assert error.code==400
        else:raise AssertionError('A star for a take that is not there was accepted')
        assert (self.raw/rough_cut.FILE_NAME).read_bytes()==before

    def test_a_star_whose_rough_cut_could_not_be_rebuilt_says_so_but_is_kept_and_the_page_is_still_written(self):
        self.reset_scenes()
        real=server.rough_cut.rebuild_rough_cut
        def broken(*args,**kwargs):raise OSError('disk full')
        server.rough_cut.rebuild_rough_cut=broken
        try:
            try:self.star({'video':self.key,'scene':'01-intro','take':1})
            except HTTPError as error:
                assert error.code==500
                message=json.load(error)['error']
                assert 'saved' in message and 'rough cut' in message
            else:raise AssertionError('A failed rough cut rebuild was reported as fine')
        finally:server.rough_cut.rebuild_rough_cut=real
        assert self.saved_scene()['star']==1
        assert 'style="flex:30" title="Scene 1, take 1"' in (self.raw/overview_page.FILE_NAME).read_text(encoding='utf-8')

    def test_a_request_that_leaves_out_the_take_does_not_take_the_star_off(self):
        self.reset_scenes()
        with self.star({'video':self.key,'scene':'01-intro','take':1}):pass
        before=(self.raw/'scenes.json').read_bytes();page=(self.raw/overview_page.FILE_NAME).read_bytes()
        try:self.star({'video':self.key,'scene':'01-intro'})
        except HTTPError as error:assert error.code==400
        else:raise AssertionError('A star request without a take was accepted')
        assert (self.raw/'scenes.json').read_bytes()==before and (self.raw/overview_page.FILE_NAME).read_bytes()==page
        assert self.saved_scene()['star']==1

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
                          ({'video':self.key,'scene':'01-intro'},400),
                          ([],400),(1,400),('1',400),
                          ({'video':'nope-12345678','scene':'01-intro','take':1},404)]:
            try:self.star(body)
            except HTTPError as error:assert error.code==code,(body,error.code)
            else:raise AssertionError(f'A bad star request was accepted: {body}')
        assert (self.raw/'scenes.json').read_bytes()==before
        assert not (self.raw/overview_page.FILE_NAME).exists()

    def test_every_star_answer_can_be_read_by_the_saved_page(self):
        # The page opened by double click only shows what the server said when the answer carries this header.
        for body,code in [({'video':'nope-12345678','scene':'01-intro','take':1},403),
                          ({'video':self.key,'scene':'01-intro','take':9,'code':self.code()},400),
                          ({'video':self.key,'scene':'01-intro','take':1},403),
                          ([],400)]:
            try:self.star(body,'null')
            except HTTPError as error:
                assert error.code==code,(body,error.code)
                assert error.headers['Access-Control-Allow-Origin']=='null',body
            else:raise AssertionError(f'A bad star request was accepted: {body}')

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
        with self.star({'video':self.key,'scene':'01-intro','take':1,'code':self.code()},'null') as r:
            assert r.headers['Access-Control-Allow-Origin']=='null'
        assert self.saved_scene()['star']==1
        # ... but the same null origin may not write comments or anything else
        try:self.request('/api/comments',{'action':'add'},{'Content-Type':'application/json','Origin':'null'})
        except HTTPError as error:assert error.code==403
        else:raise AssertionError('A null origin could write a comment')

    def test_another_website_without_the_page_code_cannot_press_stars(self):
        # S2: any website can send the origin "null" from a sandboxed frame. Only the saved page knows its code.
        self.reset_scenes()
        before=(self.raw/'scenes.json').read_bytes()
        for extra in ({},{'code':''},{'code':'guess'},{'code':123},'one letter short'):
            if extra=='one letter short':extra={'code':self.code()[:-1]}
            body={'video':self.key,'scene':'01-intro','take':1,**extra}
            try:self.star(body,'null')
            except HTTPError as error:
                assert error.code==403,(extra,error.code)
                assert error.headers['Access-Control-Allow-Origin']=='null'
                assert 'out of date' in json.load(error)['error']
            else:raise AssertionError(f'A star from the origin "null" without the right code was accepted: {extra}')
        assert (self.raw/'scenes.json').read_bytes()==before

    def test_another_website_without_the_page_code_cannot_read_the_take_list(self):
        for query in ('','&code=','&code=guess'):
            req=Request(self.origin+'/api/scenes?video='+self.key+query,headers={'Origin':'null'})
            try:urlopen(req,timeout=3)
            except HTTPError as error:assert error.code==403,(query,error.code)
            else:raise AssertionError('The take list was sent to the origin "null" without the code: '+query)

    def test_starting_the_server_gives_a_page_saved_before_page_codes_its_code(self):
        registry_path=self.data/overview_page.REGISTRY_NAME
        registry=json.loads(registry_path.read_text(encoding='utf-8'))
        old_code=registry[self.key].pop('code')
        registry_path.write_text(json.dumps(registry),encoding='utf-8')
        page=self.raw/overview_page.FILE_NAME
        page.write_text('old page without a code',encoding='utf-8')
        try:
            server.start_background_jobs().join(30)
            code=self.code()
            assert code and code!=old_code
            assert f'data-code="{code}"' in page.read_text(encoding='utf-8')
        finally:self.reset_scenes()

    def test_starting_the_server_on_another_port_points_the_saved_page_at_it(self):
        # R1-1: port 8879 was taken, so this copy started on the next one, while the page saved earlier still asks 8879.
        page=self.raw/overview_page.FILE_NAME
        old=self.httpd.server_port+1
        try:
            overview_page.remember_port(self.data,old)
            overview_page.publish(self.raw,data_dir=self.data)
            assert f'data-server="http://127.0.0.1:{old}"' in page.read_text(encoding='utf-8')
            overview_page.remember_port(self.data,self.httpd.server_port)  # what create_server does on the new port
            server.start_background_jobs().join(30)
            text=page.read_text(encoding='utf-8')
            assert f'<body data-server="{self.origin}"' in text,'the saved page still asks the old port'
            assert f'data-code="{self.code()}"' in text
        finally:
            overview_page.remember_port(self.data,self.httpd.server_port);self.reset_scenes()

    def test_the_port_job_still_runs_when_the_page_code_job_fails(self):
        page=self.raw/overview_page.FILE_NAME
        try:
            overview_page.remember_port(self.data,self.httpd.server_port+1)
            overview_page.publish(self.raw,data_dir=self.data)
            # Another copy of the app wrote its port after this server started: the pages still follow this server.
            with mock.patch.object(server.overview_page,'give_old_pages_a_code',side_effect=OSError('busy')):
                with mock.patch('sys.stderr') as stderr:server.start_background_jobs().join(30)
            assert 'next take' in ''.join(str(call.args[0]) for call in stderr.write.call_args_list)
            assert f'<body data-server="{self.origin}"' in page.read_text(encoding='utf-8'),'the saved page still asks the old port'
        finally:
            overview_page.remember_port(self.data,self.httpd.server_port);self.reset_scenes()

    def test_from_the_origin_null_an_unknown_video_looks_like_a_wrong_code(self):
        # Otherwise a website could test guessed folder names: a readable 404 would mean "no such video".
        for video in (self.key,'nope-12345678'):
            try:self.star({'video':video,'scene':'01-intro','take':1,'code':'guess'},'null')
            except HTTPError as error:
                assert error.code==403,(video,error.code)
                assert 'out of date' in json.load(error)['error']
            else:raise AssertionError('accepted')

    def test_a_broken_registry_refuses_the_saved_page_with_an_answer(self):
        registry_path=self.data/overview_page.REGISTRY_NAME
        before=registry_path.read_bytes()
        try:
            registry_path.write_text('{nope',encoding='utf-8')
            req=Request(self.origin+'/api/scenes?video='+self.key+'&code=x',headers={'Origin':'null'})
            try:urlopen(req,timeout=3)
            except HTTPError as error:
                assert error.code==403
                assert 'out of date' in json.load(error)['error']
            else:raise AssertionError('answered 200')
        finally:registry_path.write_bytes(before)

    def test_a_busy_or_broken_registry_does_not_crash_the_start_up_page_job(self):
        registry_path=self.data/overview_page.REGISTRY_NAME
        before=registry_path.read_bytes()
        try:
            registry_path.write_text('{nope',encoding='utf-8')
            errors=[]
            with mock.patch('sys.stderr') as stderr:
                server.start_background_jobs().join(10)
                errors.append(''.join(str(call.args[0]) for call in stderr.write.call_args_list))
            assert 'next take' in errors[0],errors
        finally:registry_path.write_bytes(before)
        with mock.patch.object(server.overview_page.file_store,'LOCK_WAIT_SECONDS',0.2):
            with server.overview_page.file_store.locked(server.overview_page.file_store.lock_for(registry_path)):
                with mock.patch('sys.stderr') as stderr:server.start_background_jobs().join(10)
                assert 'next take' in ''.join(str(call.args[0]) for call in stderr.write.call_args_list)
        assert registry_path.read_bytes()==before

    def raw_request(self,host,path='/api/videos',method='GET',headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
        try:
            connection.putrequest(method,path,skip_host=True)
            if host is not None:connection.putheader('Host',host)
            for name,value in (headers or {}).items():connection.putheader(name,value)
            connection.endheaders()
            response=connection.getresponse()
            return response.status,response.read()
        finally:connection.close()

    def test_only_local_host_names_are_answered(self):
        # S1 (DNS rebinding): a website can point its own name at 127.0.0.1; the browser then sends that name as Host.
        port=self.httpd.server_port
        for host in (f'127.0.0.1:{port}',f'localhost:{port}',f'LOCALHOST:{port}'):
            assert self.raw_request(host)[0]==200,host
        for host in ('evil.example',f'evil.example:{port}',f'127.0.0.1.evil.example:{port}',f'localhost.evil.example:{port}',
                     f'192.168.1.5:{port}',f'127.0.0.1:{port+1}','127.0.0.1','localhost',f'127.0.0.1:{port}@evil.example','',None):
            for path in ('/api/videos','/api/comments','/media/example-v1','/',f'/scenes/{self.key}'):
                status,body=self.raw_request(host,path)
                assert status==403,(host,path,status)
                assert b'example.mp4' not in body and b'Example' not in body
        status,_=self.raw_request(f'evil.example:{port}','/api/view','POST',{'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json','Content-Length':'2'})
        assert status==403
        status,_=self.raw_request(f'evil.example:{port}','/api/scenes/star','OPTIONS',{'Origin':'null'})
        assert status==403

    def test_a_save_that_keeps_failing_gets_an_answer_instead_of_a_dropped_connection(self):
        # B3: a save that Windows kept refusing raised an error the server did not catch.
        headers={'Content-Type':'application/json','Origin':self.origin}
        with mock.patch('os.replace',side_effect=PermissionError(13,'The process cannot access the file')):
            try:self.request('/api/view',{'videoId':'example-v1','playheadMs':10},headers)
            except HTTPError as error:
                assert error.code==500,error.code
                assert 'could not be saved' in json.load(error)['error']
            else:raise AssertionError('A failed save was reported as saved')

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

    def test_two_stars_at_once_both_land_and_the_page_matches_the_scene_file(self):
        self.reset_scenes()
        results=[]
        def click(take):
            try:
                with self.star({'video':self.key,'scene':'01-intro','take':take}) as r:results.append(r.status)
            except HTTPError as error:results.append(error.code)
        for round_ in range(5):
            threads=[threading.Thread(target=click,args=(take,)) for take in (1,2,1,2,1,2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
        assert results==[200]*30,results
        star=self.saved_scene()['star']
        page=(self.raw/overview_page.FILE_NAME).read_text(encoding='utf-8')
        assert f'title="Scene 1, take {star}"' in page
        assert sorted(p.name for p in self.raw.iterdir() if p.name.endswith('.tmp'))==[]

    def test_the_server_writes_the_port_it_really_uses_for_the_saved_pages(self):
        assert overview_page.server_address(self.data)==f'http://127.0.0.1:{self.httpd.server_port}'

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

    def test_a_star_where_both_the_rough_cut_and_the_page_fail_says_both_in_one_answer(self):
        self.reset_scenes()
        real_cut,real_page=server.rough_cut.rebuild_rough_cut,server.overview_page.build
        def broken(*args,**kwargs):raise OSError('disk full')
        server.rough_cut.rebuild_rough_cut=broken;server.overview_page.build=broken
        try:
            try:self.star({'video':self.key,'scene':'01-intro','take':1})
            except HTTPError as error:
                assert error.code==500
                message=json.load(error)['error']
                assert 'saved' in message and 'rough cut could not be rebuilt' in message and 'page could not be rewritten' in message,message
            else:raise AssertionError('A failed rebuild was reported as fine')
        finally:server.rough_cut.rebuild_rough_cut,server.overview_page.build=real_cut,real_page
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

    def test_the_saved_page_can_read_why_the_scene_list_was_refused(self):
        # A broken or missing scene file must reach the saved page (origin "null") as a readable answer, or the page
        # wrongly says the server is not running.
        def ask():
            req=Request(self.origin+'/api/scenes?video='+self.key+'&code='+self.code(),headers={'Origin':'null'})
            try:urlopen(req,timeout=3)
            except HTTPError as error:
                assert error.headers['Access-Control-Allow-Origin']=='null',error.code
                said=json.load(error)['error']
                assert isinstance(said,str) and said
                return error.code
            raise AssertionError('answered 200')
        try:
            (self.raw/'scenes.json').write_text('{nope',encoding='utf-8')
            assert ask()==409
            (self.raw/'scenes.json').unlink()
            assert ask()==404
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

    def test_a_video_folder_named_in_another_script_or_with_a_space_is_served(self):
        for name in ('Видео','My video 3'):
            folder=self.data/name
            (folder/'scenes'/'01-intro').mkdir(parents=True)
            (folder/'scenes'/'01-intro'/'take-02.jpg').write_bytes(b'jpeg')
            (folder/'prompter.md').write_text(CUE,encoding='utf-8')
            scene_store.save(folder,fresh_scene_store())
            key=overview_page.register(self.data,folder)
            with self.request('/scenes/'+quote(key)) as r:
                assert r.status==200,(name,r.status)
                page=r.read().decode('utf-8')
            assert 'Picture this.' in page,name
            assert f'<img src="/scenes/{quote(key)}/scenes/01-intro/take-02.jpg"' in page,name
            with self.request('/scenes/'+quote(key)+'/scenes/01-intro/take-02.jpg') as r:assert r.read()==b'jpeg',name

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
