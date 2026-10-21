"""Fictional household planning; no physical equipment or external service control."""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
import planner

class Invalid(ValueError): pass
class Conflict(Invalid): pass

JOBS = [
    dict(id='dryer', name='Optional dryer', watts=2200, duration_minutes=60, noisy=True, deferrable=True),
    dict(id='dishwasher', name='Optional dishwasher', watts=1400, duration_minutes=90, noisy=True, deferrable=True),
    dict(id='ebike', name='Optional e-bike charger', watts=300, duration_minutes=120, noisy=False, deferrable=True),
]
DEFAULT = {'quiet_after': '21:00', 'jobs': {j['id']: {'earliest': '18:00', 'latest_finish': '24:00', 'skip': False} for j in JOBS}}
WARNING = 'Fictional Alexa+ experience simulation. Synthetic loads and tariffs. Advisory calendar only; no device control or measured savings.'

def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'))
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()

def slot(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
        raise Invalid('Times must be HH:MM, from 18:00 to 24:00 on half-hours.')
    try: hour, minute = int(value[:2]), int(value[3:])
    except ValueError as exc: raise Invalid('Invalid time.') from exc
    if value != f'{hour:02d}:{minute:02d}' or minute not in (0, 30) or not 1080 <= hour*60+minute <= 1440:
        raise Invalid('Times must be from 18:00 to 24:00 on half-hours.')
    return (hour*60 + minute - 1080)//30

def clock(s): return f'{18+s//2:02d}:{(s%2)*30:02d}'

def merge_constraints(current, patch):
    if not isinstance(patch, dict) or set(patch)-{'quiet_after', 'jobs'}:
        raise Invalid('Constraints accept only quiet_after and jobs.')
    result = deepcopy(current)
    if 'quiet_after' in patch:
        slot(patch['quiet_after']); result['quiet_after'] = patch['quiet_after']
    updates = patch.get('jobs', {})
    if not isinstance(updates, dict) or set(updates)-set(DEFAULT['jobs']):
        raise Invalid('Only dryer, dishwasher, and ebike are supported. Critical equipment is never scheduled.')
    for job_id, changes in updates.items():
        if not isinstance(changes, dict) or set(changes)-{'earliest', 'latest_finish', 'skip', 'exact_start'}:
            raise Invalid('Each job accepts earliest, latest_finish, skip and optional exact_start only.')
        for key, value in changes.items():
            if key == 'skip':
                if type(value) is not bool: raise Invalid('skip must be true or false.')
            elif key=='exact_start' and value is None: pass
            else: slot(value)
            result['jobs'][job_id][key] = value
    return result

def calculate(constraints):
    constraints = merge_constraints(DEFAULT, constraints)
    data = dict(baseline_watts=[650,850,1100,1250,1100,850,650,550,450,400,350,350],
                carbon_g_per_kwh=[410,420,430,410,380,350,320,300,280,260,240,230],
                tariff_millicents_per_kwh=[32000,32000,35000,35000,30000,28000,22000,20000,18000,16000,15000,15000],
                slot_minutes=30, capacity_watts=6500, jobs=[])
    skipped, reasons = [], []
    for job in JOBS:
        c = constraints['jobs'][job['id']]
        if c['skip']: skipped.append(job['id']); continue
        earliest = slot(c['earliest'])
        finish = slot(c['latest_finish'])
        if job['noisy']: finish = min(finish, slot(constraints['quiet_after']))
        latest = finish-job['duration_minutes']//30
        if c.get('exact_start') is not None:
            exact=slot(c['exact_start'])
            if earliest<=exact<=latest: earliest=latest=exact
            else:
                reasons.append(f"{job['name']} cannot start exactly at {c['exact_start']} within its start, finish and quiet limits. Change the exact start or its limits.")
                continue
        if latest < earliest:
            reasons.append(f"{job['name']} needs {job['duration_minutes']} minutes between {c['earliest']} and {clock(finish)}. Relax its window or skip it.")
        else:
            data['jobs'].append(dict(id=job['id'], watts=job['watts'], duration=job['duration_minutes']//30,
                                     earliest=earliest, latest=latest, deferrable=True))
    common = dict(constraints=constraints, skipped=skipped, warning=WARNING,
                  skipped_job_energy_kwh=sum(j['watts']*j['duration_minutes']/60000 for j in JOBS if j['id'] in skipped))
    if reasons:
        return dict(common, status='infeasible', schedule=[], metrics=None, baseline_metrics=None,
                    savings=None, optimality_proven=True, explanation=' '.join(reasons))
    result = planner.solve(data, node_budget=50000)
    baseline = planner.evaluate(data, {j['id']: j['earliest'] for j in data['jobs']})
    schedule = []
    for job in JOBS:
        if result['starts'] is not None and job['id'] in result['starts']:
            s = result['starts'][job['id']]
            schedule.append(dict(job_id=job['id'], name=job['name'], start=clock(s),
                                 end=clock(s+job['duration_minutes']//30), watts=job['watts'], duration_minutes=job['duration_minutes']))
    schedule.sort(key=lambda j:(j['start'],j['job_id']))
    m = result['metrics']
    return dict(common, status='proposed' if m else 'infeasible', schedule=schedule, metrics=m,
                baseline_metrics=baseline, savings={'peak_watts':baseline['peak_watts']-m['peak_watts'],
                'cost_dollars':round(baseline['cost_dollars']-m['cost_dollars'],6)} if m else None,
                optimality_proven=result['optimality_proven'], solver_status=result['status'], solver_nodes=result['nodes'],
                input_sha256=result['input_sha256'],
                explanation=('Schedule preserves energy for the included jobs; peak is optimized first, then modeled carbon, then tariff. Skipped jobs are omitted, not treated as efficiency savings.' if m else 'No schedule found within constraints.'))

class Store:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, revision INTEGER, body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, body TEXT)')
            db.execute('INSERT OR IGNORE INTO state VALUES (?,?)', ('current',canonical({'revision':0,'constraints':DEFAULT,'latest_proposal':None})))
            state=json.loads(db.execute('SELECT value FROM state WHERE key=?',('current',)).fetchone()[0])
            if state.get('pending_request') and not state['pending_request'].get('id'):
                state['pending_request']['id']=digest(state['pending_request'])[:24]
                db.execute('UPDATE state SET value=? WHERE key=?',(canonical(state),'current'))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=5)
        try:
            with db: yield db
        finally: db.close()

    def household(self):
        with self.lock, self.connect() as db:
            state = json.loads(db.execute('SELECT value FROM state WHERE key=?',('current',)).fetchone()[0])
        return dict(state, name='Watt If fictional home', date='2026-10-01', start='18:00', end='24:00',
                    slot_minutes=30, jobs=deepcopy(JOBS), warning=WARNING)

    def preview(self, patch, current=None):
        state = current or self.household()
        return calculate(merge_constraints(state['constraints'], patch))

    def begin_request(self, message):
        """Retain an unresolved instruction across provider failure and restart."""
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT value FROM state WHERE key=?',('current',)).fetchone()[0])
            previous=state.get('pending_request')
            if previous:
                if message.strip().lower().rstrip('.!?') in ('try again','retry'):
                    message=previous['message']
                elif message.strip() != previous['message'].strip() and not message.strip().startswith(previous['message'].strip()):
                    message=previous['message']+' '+message
            if len(message)>2000:
                raise Invalid('Unresolved requests exceed the supported input size. Review and explicitly replace them using the manual controls.')
            state['pending_request']={'message':message,'received_at':now(),'status':'needs_plan'}
            state['pending_request']['id']=digest(state['pending_request'])[:24]
            db.execute('UPDATE state SET value=? WHERE key=?',(canonical(state),'current'))
            return dict(state, name='Watt If fictional home', date='2026-10-01', start='18:00', end='24:00',
                        slot_minutes=30, jobs=deepcopy(JOBS), warning=WARNING)

    def propose(self, patch, expected_revision=None, execution=None, resolve_request_id=None):
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise Invalid('expected_revision must be a nonnegative integer.')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT value FROM state WHERE key=?',('current',)).fetchone()[0])
            pending=state.get('pending_request')
            if pending and (execution or {}).get('request_id') != pending['id'] and resolve_request_id != pending['id']:
                raise Conflict('An unresolved request is outstanding. Review it and explicitly replace it, or retry the original request.')
            if expected_revision is not None and state['revision'] != expected_revision:
                raise Conflict('Household changed during planning. Read the latest state and try again.')
            proposal = self.preview(patch,state)
            revision = state['revision']+1
            proposal.update(revision=revision, created_at=now(), execution=execution)
            proposal['sha256'] = digest(proposal)
            proposal['id'] = proposal['sha256'][:24]
            db.execute('INSERT INTO proposals VALUES (?,?,?)',(proposal['id'],revision,canonical(proposal)))
            db.execute('UPDATE state SET value=? WHERE key=?',(canonical({'revision':revision,'constraints':proposal['constraints'],'latest_proposal':proposal}),'current'))
            return deepcopy(proposal)

    def approve(self, proposal_id, revision):
        if not isinstance(proposal_id,str) or type(revision) is not int: raise Invalid('Exact proposal_id and integer revision are required.')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT value FROM state WHERE key=?',('current',)).fetchone()[0])
            if state.get('pending_request'):
                raise Conflict('An unresolved request needs a new plan. Retry it or explicitly preview replacement manual constraints before approval.')
            p=state['latest_proposal']
            if not p or p['id'] != proposal_id or revision != state['revision']:
                raise Conflict('Proposal is stale. Review and approve the current revision.')
            if p['status'] != 'proposed': raise Invalid('An infeasible proposal cannot be approved.')
            if digest({k:v for k,v in p.items() if k not in ('sha256','id')}) != p['sha256']:
                raise Conflict('Proposal integrity check failed. Generate a new proposal.')
            aid=digest({'proposal_id':proposal_id,'revision':revision})[:24]
            existing=db.execute('SELECT body FROM approvals WHERE id=?',(aid,)).fetchone()
            if existing: return json.loads(existing[0])
            approval=dict(id=aid,proposal_id=proposal_id,revision=revision,approved_at=now(),sha256=p['sha256'],
                          advisory_only=True,export_url='/api/export?id='+aid)
            db.execute('INSERT INTO approvals VALUES (?,?)',(aid,canonical(approval)))
            return approval

    def export(self, approval_id):
        if not isinstance(approval_id,str) or len(approval_id)!=24 or any(c not in '0123456789abcdef' for c in approval_id):
            raise Invalid('Valid approval id required.')
        with self.lock,self.connect() as db:
            row=db.execute('SELECT body FROM approvals WHERE id=?',(approval_id,)).fetchone()
            if not row: raise Invalid('No approval found. Approve a proposal first.')
            a=json.loads(row[0])
            p=json.loads(db.execute('SELECT body FROM proposals WHERE id=?',(a['proposal_id'],)).fetchone()[0])
        if (digest({k:v for k,v in p.items() if k not in ('sha256','id')}) != p['sha256']
                or a['sha256'] != p['sha256'] or a['revision'] != p['revision']
                or a['proposal_id'] != p['id'] or p['status'] != 'proposed'):
            raise Conflict('Approved proposal integrity check failed. Export refused.')
        # Historical approved exports remain auditable; they never mutate the current plan.
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Watt If//Fictional Advisory Calendar//EN','CALSCALE:GREGORIAN']
        stamp=datetime.fromisoformat(a['approved_at']).strftime('%Y%m%dT%H%M%SZ')
        for j in p['schedule']:
            def dt(t): return (datetime(2026,10,1,18)+timedelta(minutes=slot(t)*30)).strftime('%Y%m%dT%H%M%S')
            lines += ['BEGIN:VEVENT','UID:'+a['id']+'-'+j['job_id']+'@watt-if.local','DTSTAMP:'+stamp,
                      'DTSTART:'+dt(j['start']),'DTEND:'+dt(j['end']),'SUMMARY:Advisory - '+j['name'],
                      'DESCRIPTION:Fictional Oct 1 demo. No device control. Review before use.','END:VEVENT']
        return '\r\n'.join(lines+['END:VCALENDAR',''])
