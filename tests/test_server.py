import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class ReviewServerTests:
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.data=Path(cls.temp.name)
        cls.media=cls.data/'example.mp4'
        cls.media.write_bytes(bytes(range(256)))
        cls.httpd=server.create_server(0,cls.data)
        cls.origin=f'http://127.0.0.1:{cls.httpd.server_port}'
        (cls.data/'manifest.json').write_text(json.dumps([{'id':'example-v1','title':'Example','label':'v1','path':str(cls.media),'fps':30,'durationMs':60000,'fingerprint':'example','keepRanges':[[0,1800]]}]))
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True)
        cls.thread.start()

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
