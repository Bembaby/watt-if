import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Server
from household import Store

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=Server(('127.0.0.1',0),Store(Path(self.temp.name)/'state.db'))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        h={'Content-Type':'application/json'};h.update(headers or {})
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
        r=c.getresponse();status=r.status;data=r.read();c.close();return status,data
    def test_real_http_plan_approve_export(self):
        status,data=self.request('/api/plan',{'constraints':{}});self.assertEqual(status,200);p=json.loads(data)['proposal']
        status,data=self.request('/api/approve',{'proposal_id':p['id'],'revision':p['revision']});self.assertEqual(status,200)
        a=json.loads(data)['approval'];status,data=self.request(a['export_url']);self.assertEqual(status,200)
        self.assertIn(b'BEGIN:VCALENDAR',data)
    def test_host_origin_and_static_allowlist(self):
        self.assertEqual(self.request('/api/household',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/plan',{'constraints':{}},{'Origin':'https://evil.example'})[0],403)
        for path in ('/household.py','/watt_if.sqlite3','/../HANDOFF_PRIVATE.md'):
            self.assertEqual(self.request(path)[0],404)
    def test_body_limit_and_exact_fields(self):
        self.assertEqual(self.request('/api/chat',{'message':'a'*17000})[0],400)
        self.assertEqual(self.request('/api/approve',{'proposal_id':'x','revision':True})[0],400)
        self.assertEqual(self.request('/api/plan',{'constraints':{},'approve':True})[0],400)
    def test_stale_approval_http_conflict(self):
        p=json.loads(self.request('/api/plan',{'constraints':{}})[1])['proposal']
        self.request('/api/plan',{'constraints':{'quiet_after':'18:00'}})
        self.assertEqual(self.request('/api/approve',{'proposal_id':p['id'],'revision':p['revision']})[0],409)
    def test_tunnel_status_is_explicit_even_when_model_is_offline(self):
        import agent
        old_host,old_location=agent.HOST,agent.EXECUTION_LOCATION
        try:
            agent.configure_runtime(11435,'runpod_ssh_tunnel')
            with patch('agent.model_details',side_effect=agent.RunError('Offline')):
                status,data=self.request('/api/status')
            result=json.loads(data)
            self.assertEqual(status,200)
            self.assertFalse(result['ready'])
            self.assertTrue(result['cloud_enabled'])
            self.assertEqual(result['execution_location'],'runpod_ssh_tunnel')
            self.assertEqual(result['host'],'http://127.0.0.1:11435')
            self.assertEqual(result['transport'],'ssh_loopback_forward')
        finally:agent.HOST,agent.EXECUTION_LOCATION=old_host,old_location
    def test_runtime_cannot_configure_arbitrary_network_hosts(self):
        from agent import configure_runtime
        for port in (True,'11435',80,65536,'https://example.com'):
            with self.assertRaises(ValueError):configure_runtime(port,'runpod_ssh_tunnel')
        with self.assertRaises(ValueError):configure_runtime(11435,'unreviewed_provider')

if __name__=='__main__':unittest.main()
