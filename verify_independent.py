"""Independent finite-domain oracle and HTTP checks; no inference or cloud calls."""
from __future__ import annotations
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import http.client
import itertools
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import threading
from unittest.mock import patch

PRODUCT = Path(__file__).resolve().parent
sys.path.insert(0, str(PRODUCT))
import household
import app

BASE = [650,850,1100,1250,1100,850,650,550,450,400,350,350]
CARBON = [410,420,430,410,380,350,320,300,280,260,240,230]
PRICE = [32000,32000,35000,35000,30000,28000,22000,20000,18000,16000,15000,15000]
SPECS = [('dryer',2200,2,True),('dishwasher',1400,3,True),('ebike',300,4,False)]
TIMES = [f'{18+s//2:02}:{s%2*30:02}' for s in range(13)]

def hashes():
    return {n: hashlib.sha256((PRODUCT/n).read_bytes()).hexdigest() for n in ('household.py','planner.py','app.py','agent.py','app.js')}

def base_constraints():
    return {'quiet_after':'21:00','jobs':{j:{'earliest':'18:00','latest_finish':'24:00','skip':False} for j,_,_,_ in SPECS}}

def minute(text):
    h,m = map(int,text.split(':'))
    return h*60+m

def oracle(c):
    included = [j for j in SPECS if not c['jobs'][j[0]]['skip']]
    options=[]
    for name,watts,duration,noisy in included:
        earliest=minute(c['jobs'][name]['earliest'])
        finish=minute(c['jobs'][name]['latest_finish'])
        if noisy: finish=min(finish,minute(c['quiet_after']))
        options.append([i for i in range(12) if 1080+i*30>=earliest and 1080+(i+duration)*30<=finish])
    best=None
    enumerated=0
    for assignment in itertools.product(*options):
        load=[BASE[t]+sum(w for start,(_,w,d,_) in zip(assignment,included) if start<=t<start+d) for t in range(12)]
        enumerated+=1
        if any(w>6500 for w in load): continue
        obj=(max(load),sum(w*g for w,g in zip(load,CARBON)),sum(w*p for w,p in zip(load,PRICE)))
        if best is None or obj<best: best=obj
    return best,enumerated,included

def main():
    before=hashes()
    cases=[]
    # All quiet-time / omitted-work combinations, including no chores.
    for quiet in TIMES:
        for mask in range(8):
            c=base_constraints();c['quiet_after']=quiet
            for i,(j,*_) in enumerate(SPECS): c['jobs'][j]['skip']=bool(mask&(1<<i))
            cases.append(c)
    # Every half-hour start/finish pair per actual appliance at six quiet cutoffs.
    for name,*_ in SPECS:
        for quiet in ('18:00','18:30','19:30','21:00','23:30','24:00'):
            for earliest,finish in itertools.product(TIMES,repeat=2):
                c=base_constraints();c['quiet_after']=quiet
                c['jobs'][name].update(earliest=earliest,latest_finish=finish)
                cases.append(c)
    rng=random.Random(20260908)
    for _ in range(400):
        c=base_constraints();c['quiet_after']=rng.choice(TIMES)
        for j,*_ in SPECS:
            c['jobs'][j].update(earliest=rng.choice(TIMES),latest_finish=rng.choice(TIMES),skip=rng.randrange(5)==0)
        cases.append(c)
    failures=[];feasible=0;assignments=0
    for ix,c in enumerate(cases):
        best,visited,included=oracle(c);assignments+=visited
        got=household.calculate(c)
        errors=[]
        if got['constraints']!=c: errors.append('constraints changed')
        skipped_energy=sum(w*d*.5/1000 for j,w,d,_ in SPECS if c['jobs'][j]['skip'])
        if abs(got['skipped_job_energy_kwh']-skipped_energy)>1e-9: errors.append('omitted energy differs')
        if best is None:
            if got['status']!='infeasible' or got['schedule'] or got['metrics'] is not None: errors.append('infeasible case claimed feasible')
        else:
            feasible+=1
            if got['status']!='proposed': errors.append('feasible case rejected')
            else:
                loads=BASE[:]
                if {x['job_id'] for x in got['schedule']}!={j for j,*_ in included}: errors.append('included job mismatch')
                for entry in got['schedule']:
                    name,w,d,noisy=next(x for x in SPECS if x[0]==entry['job_id'])
                    start=minute(entry['start']);end=minute(entry['end']);settings=c['jobs'][name]
                    if end-start!=30*d or entry['duration_minutes']!=30*d: errors.append('duration mismatch')
                    if start<minute(settings['earliest']) or end>minute(settings['latest_finish']): errors.append('window violated')
                    if noisy and end>minute(c['quiet_after']): errors.append('quiet cutoff violated')
                    for t in range(12):
                        if start<=1080+t*30<end: loads[t]+=w
                expected_obj=(max(loads),sum(w*g for w,g in zip(loads,CARBON)),sum(w*p for w,p in zip(loads,PRICE)))
                if expected_obj!=best or tuple(got['metrics']['objective'])!=best: errors.append('non-optimal objective or mismatched metrics')
                expected_energy=(sum(BASE)+sum(w*d for _,w,d,_ in included))*.5/1000
                if abs(got['metrics']['energy_kwh']-expected_energy)>1e-9 or abs(got['baseline_metrics']['energy_kwh']-expected_energy)>1e-9: errors.append('included energy changed')
                if abs(got['metrics']['cost_dollars']-expected_obj[2]*.5/1000/100000)>1e-9: errors.append('tariff units mismatch')
                if not got['optimality_proven']: errors.append('unproved small exact case')
        if errors: failures.append({'case':ix,'constraints':c,'errors':errors})
    checks=[]
    def check(label,condition): checks.append({'check':label,'pass':bool(condition)})
    with tempfile.TemporaryDirectory(prefix='watt-if-independent-') as temp:
        dbpath=Path(temp)/'test.sqlite3'; store=household.Store(dbpath)
        p=store.propose({});a=store.approve(p['id'],p['revision'])
        check('approval idempotent',a==store.approve(p['id'],p['revision']))
        ics=store.export(a['id'])
        check('calendar includes all and only included chores',ics.count('BEGIN:VEVENT')==3)
        check('calendar CRLF complete',ics.endswith('END:VCALENDAR\r\n') and '\n' not in ics.replace('\r\n',''))
        check('calendar explicitly advisory', 'No device control' in ics and 'Advisory' in ics)
        old=store.propose({'quiet_after':'18:00'})
        try: store.approve(p['id'],p['revision']);stale=False
        except household.Conflict:stale=True
        check('older feasible approval rejected after infeasible new revision',stale)
        check('historical approved calendar remains unchanged',store.export(a['id'])==ics)
        midnight=store.propose({'quiet_after':'24:00','jobs':{'ebike':{'earliest':'22:00','latest_finish':'24:00'}}})
        ma=store.approve(midnight['id'],midnight['revision'])
        check('24:00 converted to next date', 'DTEND:20261002T000000' in store.export(ma['id']))
        # Corrupt the persisted export source, not the current-state integrity copy.
        with closing(sqlite3.connect(dbpath)) as db:
            content=json.loads(db.execute('SELECT body FROM proposals WHERE id=?',(p['id'],)).fetchone()[0])
            content['schedule'][0]['start']='18:00'
            content['schedule'][0]['name']='Changed after approval'
            db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(content),p['id']))
            db.commit()
        try: store.export(a['id']);protected=False
        except household.Invalid:protected=True
        check('export rejects altered persisted approved proposal',protected)
        # Separate Store instances have separate Python locks: SQLite must guard revisions.
        peer=household.Store(dbpath);initial_revision=store.household()['revision'];barrier=threading.Barrier(2);outcomes=[]
        def concurrent_proposal(target,quiet):
            barrier.wait(timeout=5)
            try:
                value=target.propose({'quiet_after':quiet},expected_revision=initial_revision)
                outcomes.append(('saved',value['revision']))
            except household.Conflict: outcomes.append(('conflict',None))
        racers=[threading.Thread(target=concurrent_proposal,args=(target,quiet)) for target,quiet in ((store,'22:00'),(peer,'23:00'))]
        for racer in racers:racer.start()
        for racer in racers:racer.join(timeout=5)
        check('two stores cannot both commit the same expected revision',sorted(x[0] for x in outcomes)==['conflict','saved'] and store.household()['revision']==initial_revision+1)
        server=app.Server(('127.0.0.1',0),store)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        port=server.server_port
        def req(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse();result=response.status,response.read(),dict(response.getheaders());connection.close();return result
        try:
            status,_,headers=req('GET','/api/household');check('normal loopback GET',status==200)
            check('no permissive CORS',not any(k.lower()=='access-control-allow-origin' for k in headers))
            for label,headers in [('wrong Host',{'Host':'attacker.example'}),('wrong Origin',{'Origin':'https://attacker.example'}),('opaque Origin',{'Origin':'null'}),('cross-site fetch',{'Sec-Fetch-Site':'cross-site'})]:
                check('GET denies '+label,req('GET','/api/household',headers=headers)[0]==403)
                h={'Content-Type':'application/json',**headers}
                check('POST denies '+label,req('POST','/api/plan','{"constraints":{}}',h)[0]==403)
            check('same-origin localhost alias accepted',req('GET','/api/household',headers={'Origin':f'http://localhost:{port}'})[0]==200)
            check('HTML form POST rejected',req('POST','/api/plan','constraints={}',{'Content-Type':'application/x-www-form-urlencoded'})[0]==400)
            check('malformed JSON rejected',req('POST','/api/plan','{',{'Content-Type':'application/json'})[0]==400)
            check('JSON array rejected',req('POST','/api/plan','[]',{'Content-Type':'application/json'})[0]==400)
            check('oversized body rejected',req('POST','/api/plan',' '*16385,{'Content-Type':'application/json'})[0]==400)
            check('unknown endpoint rejected',req('GET','/api/not-real')[0]==404)
            # A blocked local test stub stands in for inference; no model or inventory call.
            entered=threading.Event();release=threading.Event();first_response=[]
            def blocked_chat(message,target):
                revision=target.household()['revision'];entered.set()
                if not release.wait(timeout=5):raise RuntimeError('Independent test latch timed out')
                return {'proposal':target.propose({},expected_revision=revision),'trace':{'evidence_type':'independent-concurrency-stub'}}
            with patch('agent.run',side_effect=blocked_chat):
                first=threading.Thread(target=lambda:first_response.append(req('POST','/api/chat','{"message":"test"}',{'Content-Type':'application/json'})))
                first.start()
                try:
                    check('test chat reached serialized run',entered.wait(timeout=5))
                    check('second simultaneous chat rejected',req('POST','/api/chat','{"message":"test"}',{'Content-Type':'application/json'})[0]==409)
                    manual_status,manual_raw,_=req('POST','/api/plan','{"constraints":{"quiet_after":"20:30"}}',{'Content-Type':'application/json'})
                    check('manual plan remains usable during chat',manual_status==200)
                    manual_revision=json.loads(manual_raw)['proposal']['revision']
                finally:release.set();first.join(timeout=5)
                check('stale chat cannot overwrite manual plan',len(first_response)==1 and first_response[0][0]==409 and store.household()['revision']==manual_revision and store.household()['constraints']['quiet_after']=='20:30')
        finally:server.shutdown();server.server_close();worker.join(timeout=5)
    result={'run_at_utc':datetime.now(timezone.utc).isoformat(),'inference_performed':False,'external_services_used':False,'source_before':before,'source_after':hashes(),
            'oracle':{'cases':len(cases),'feasible_cases':feasible,'enumerated_assignments':assignments,'failures':failures},'checks':checks,
            'ics_time_semantics':'Floating calendar times intentionally interpreted in importing calendar local zone; fictional Oct 1, no fixed New Jersey instant asserted.'}
    target=PRODUCT/'verification-output'/'independent.json';target.parent.mkdir(exist_ok=True);target.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'oracle':{k:v for k,v in result['oracle'].items() if k!='failures'},'oracle_failure_count':len(failures),'checks_passed':sum(c['pass'] for c in checks),'checks_total':len(checks),'failed_checks':[c for c in checks if not c['pass']],'source_changed':before!=result['source_after'],'report':str(target)},indent=2))
    return 1 if failures or any(not c['pass'] for c in checks) else 0

if __name__=='__main__':raise SystemExit(main())
