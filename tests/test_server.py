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

    def test_comment_range_is_persisted_in_milliseconds(self):
        note={'action':'add','videoId':'example-v1','startMs':20123,'endMs':20567,'text':'Remove repeat','category':'speech','createdAt':'2026-01-01T00:00:00Z'}
        with self.request('/api/comments',note,{'Content-Type':'application/json','Origin':self.origin}) as r:
            comments=json.load(r)['comments']
        saved=comments[-1]
        assert (saved['startMs'],saved['endMs'])==(20123,20567)
        persisted=json.loads((self.data/'comments.json').read_text())['comments']
        assert saved in persisted

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
