"""Loopback-only single-operator Watt If demo server."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit,parse_qs
from household import Store,Invalid,Conflict

ROOT=Path(__file__).resolve().parent
STATIC={'/':('index.html','text/html'),'/index.html':('index.html','text/html'),'/app.js':('app.js','application/javascript'),'/style.css':('style.css','text/css')}

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store):
        if address[0]!='127.0.0.1': raise ValueError('Only 127.0.0.1 binding is supported.')
        self.store=store;self.chat_lock=threading.Lock()
        super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,status,value,content_type='application/json',headers=None):
        body=json.dumps(value).encode() if content_type=='application/json' else value if isinstance(value,bytes) else value.encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers();self.wfile.write(body)

    def boundary(self):
        port=self.server.server_port
        allowed={f'127.0.0.1:{port}',f'localhost:{port}'}
        if self.headers.get('Host') not in allowed: raise PermissionError('Invalid Host.')
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+v for v in allowed}: raise PermissionError('Cross-origin access denied.')
        if self.headers.get('Sec-Fetch-Site')=='cross-site': raise PermissionError('Cross-site access denied.')

    def do_GET(self):
        try:
            self.boundary();u=urlsplit(self.path)
            if u.path=='/api/household': return self.send(200,{'household':self.server.store.household()})
            if u.path=='/api/status':
                from agent import model_details,RunError,MODEL
                try: details=model_details();ready=True;error=None
                except RunError as exc: details={};ready=False;error=str(exc)
                return self.send(200,{'provider':'ollama','model_id':MODEL,'host':'http://127.0.0.1:11434',
                                      'simulation':True,'advisory_only':True,'cloud_enabled':False,
                                      'ready':ready,'error':error,**details,
                                      'model_readiness':'Local inventory checked; this is not inference evidence.'})
            if u.path=='/api/export':
                q=parse_qs(u.query)
                if set(q)!={'id'} or len(q['id'])!=1: raise Invalid('One approved id is required.')
                return self.send(200,self.server.store.export(q['id'][0]),'text/calendar',{'Content-Disposition':'attachment; filename="watt-if-advisory.ics"'})
            if u.path in STATIC:
                name,mime=STATIC[u.path]
                return self.send(200,(ROOT/name).read_bytes(),mime)
            return self.send(404,{'error':'Not found.'})
        except PermissionError as exc: self.send(403,{'error':str(exc)})
        except Invalid as exc: self.send(400,{'error':str(exc)})
        except FileNotFoundError: self.send(404,{'error':'Static asset not found.'})

    def do_POST(self):
        try:
            self.boundary()
            if self.headers.get('Transfer-Encoding'): raise Invalid('Transfer encoding is unsupported.')
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json': raise Invalid('application/json is required.')
            raw_length=self.headers.get('Content-Length','')
            if not raw_length.isdigit() or not 1<=int(raw_length)<=16384: raise Invalid('Body must contain 1–16384 bytes.')
            self.connection.settimeout(5)
            body=json.loads(self.rfile.read(int(raw_length)))
            if not isinstance(body,dict): raise Invalid('JSON object required.')
            path=urlsplit(self.path).path
            if path=='/api/plan':
                if set(body)!={'constraints'}: raise Invalid('Expected constraints only.')
                return self.send(200,{'proposal':self.server.store.propose(body['constraints'])})
            if path=='/api/approve':
                if set(body)!={'proposal_id','revision'}: raise Invalid('Expected proposal_id and revision only.')
                return self.send(200,{'approval':self.server.store.approve(body['proposal_id'],body['revision'])})
            if path=='/api/chat':
                if set(body)!={'message'}: raise Invalid('Expected message only.')
                if not self.server.chat_lock.acquire(blocking=False): return self.send(409,{'error':'An agent run is already in progress.'})
                try:
                    from agent import run,RunError
                    try: result=run(body['message'],self.server.store)
                    except RunError as exc: return self.send(503,{'error':str(exc),'reply':'No proposal saved.','proposal':None,'trace':exc.trace})
                    return self.send(200,result)
                finally: self.server.chat_lock.release()
            return self.send(404,{'error':'Not found.'})
        except PermissionError as exc: self.send(403,{'error':str(exc)})
        except Conflict as exc: self.send(409,{'error':str(exc),'trace':getattr(exc,'trace',None)})
        except (Invalid,json.JSONDecodeError,UnicodeDecodeError) as exc: self.send(400,{'error':str(exc)})
        except TimeoutError: self.send(408,{'error':'Request body timed out.'})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8793)
    p.add_argument('--state',default=str(ROOT/'watt_if.sqlite3'))
    args=p.parse_args()
    server=Server(('127.0.0.1',args.port),Store(args.state))
    print(f'Watt If local simulation: http://127.0.0.1:{server.server_port}',flush=True)
    server.serve_forever()

if __name__=='__main__': main()
