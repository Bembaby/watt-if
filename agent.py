"""Real bounded Strands/Ollama conversation. No fallback and no approval tool."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from household import Invalid, Conflict, canonical, digest, slot

ROOT=Path(__file__).resolve().parent
HOST='http://127.0.0.1:11434'
EXECUTION_LOCATION='local'
MODEL='qwen2.5:14b'
LIMITS={'turns':5,'output_tokens':5000,'total_tokens':18000}
SOURCE_FILES=('planner.py','household.py','agent.py','app.py','requirements.txt')
def sources(): return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}
LOADED_SOURCES=sources()

def configure_runtime(port=11434, location='local'):
    """Startup-only configuration. Remote execution requires an operator-owned SSH tunnel."""
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Ollama port must be an integer between 1024 and 65535.')
    if location not in ('local','runpod_ssh_tunnel'):
        raise ValueError('Model location must be local or runpod_ssh_tunnel.')
    global HOST,EXECUTION_LOCATION
    HOST=f'http://127.0.0.1:{port}'
    EXECUTION_LOCATION=location

def runtime_details():
    return {'host':HOST,'execution_location':EXECUTION_LOCATION,
            'cloud_enabled':EXECUTION_LOCATION=='runpod_ssh_tunnel',
            'transport':'ssh_loopback_forward' if EXECUTION_LOCATION=='runpod_ssh_tunnel' else 'loopback',
            'location_source':'operator_configuration'}

class RunError(RuntimeError):
    def __init__(self,message,trace=None):
        super().__init__(message)
        self.trace=trace

def tool_transcript(messages):
    """Expose only tool requests/results, never model reasoning or system prompts."""
    transcript=[]
    for message in messages:
        for block in message.get('content',[]):
            if 'toolUse' in block:
                value=block['toolUse']
                transcript.append({'type':'tool_use','tool':value.get('name'),'tool_use_id':value.get('toolUseId'),
                                   'args':value.get('input')})
            elif 'toolResult' in block:
                value=block['toolResult']
                content=[{'text':str(c['text'])[:2500]} for c in value.get('content',[]) if 'text' in c]
                transcript.append({'type':'tool_result','tool_use_id':value.get('toolUseId'),
                                   'status':value.get('status'),'content':content})
    return transcript[-30:]

def proposal_reply(proposal):
    """Render factual schedule text from validated data, not free-form model prose."""
    if proposal['status']=='infeasible':
        return 'No feasible advisory schedule. '+proposal['explanation']+' Review the constraints before trying again.'
    jobs='; '.join(f"{job['name']}: {job['start']}–{job['end']}" for job in proposal['schedule'])
    result='Proposed fictional evening schedule: '+(jobs or 'all optional jobs skipped')+'. '
    if proposal['skipped']:
        result+='Skipped: '+', '.join(proposal['skipped'])+'. Skipped jobs are excluded from both comparison schedules. '
    return result+'Review the exact times, then approve the advisory calendar.'

class Change(BaseModel):
    """One user-requested change, grounded in a verbatim clause of this turn."""
    model_config=ConfigDict(extra='forbid')
    job_id: Literal['dryer','dishwasher','ebike','household']
    field: Literal['earliest','latest_finish','skip','quiet_after']
    value: str = Field(description='HH:MM for times, or true/false text for skip.')
    evidence: str = Field(description='Exact complete user sentence or semicolon clause naming this job and one relevant time/intent; never crop words or shared job names.')

class IndexedChange(BaseModel):
    """Model-selected intent and source ID; quote text is supplied by the server."""
    model_config=ConfigDict(extra='forbid')
    job_id: Literal['dryer','dishwasher','ebike','household']
    field: Literal['earliest','latest_finish','skip','quiet_after']
    value: str = Field(description='HH:MM for times, or true/false text for skip.')
    clause_id: int = Field(strict=True,ge=1,description='The numbered source clause from the current input that explicitly supports this change.')

JOB_ALIASES={'dryer':r'\bdryer\b','dishwasher':r'\bdishwasher\b','ebike':r'\b(?:e[- ]?bike|bike charger)\b'}
ACTION_WORDS=r'\b(skip|omit|exclude|cancel|restore|include|resume|unskip|run|use|schedule)\b'

def request_clauses(message):
    """A deliberately narrow sentence/clause boundary, not general English parsing."""
    boundary=r'[!?;]+|\.(?=\s|$)|\s+but\s+|\s+and\s+(?=(?:keep|have|finish|start|skip|omit|cancel|restore|include|resume|make)\b)'
    return [part.strip(' ,.!?;') for part in re.split(boundary,message,flags=re.I) if part.strip(' ,.!?;')]

def resolve_indexed_changes(changes,message):
    """Resolve only server-provided source IDs; existing quote validation stays unchanged."""
    if not isinstance(changes,list) or len(changes)>10:raise Invalid('At most ten indexed changes.')
    clauses=request_clauses(message)
    resolved=[]
    for raw in changes:
        change=IndexedChange.model_validate(raw).model_dump() if isinstance(raw,dict) else raw.model_dump()
        clause_id=change.pop('clause_id')
        if clause_id>len(clauses):raise Invalid('clause_id must reference a numbered source clause from this request.')
        resolved.append(dict(change,evidence=clauses[clause_id-1]))
    return resolved

def evidence_times(quote):
    """Validate a model-cited clock expression; does not interpret the user request."""
    times=set()
    if re.search(r'\bmidnight\b',quote,re.I): times.add('24:00')
    pattern=r'(?<![\w:])(\d{1,2})(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)?(?![\w:])'
    for match in re.finditer(pattern,quote,re.I):
        h=int(match[1]);minute=int(match[2] or 0);suffix=(match[3] or '').lower().replace('.','')
        if suffix:
            if not 1<=h<=12: continue
            h=h%12+(12 if suffix=='pm' else 0)
            if h==0: h=24
        elif 1<=h<=12: h=24 if h==12 else h+12
        value=f'{h:02d}:{minute:02d}'
        try: slot(value)
        except Invalid: continue
        times.add(value)
    return times

def grounded_patch(changes, message):
    """Reject unsupported model edits; only cited, explicit changes reach the solver."""
    if not isinstance(changes,list) or len(changes)>10: raise Invalid('At most ten explicit changes.')
    # A cropped quote must never erase negation elsewhere in the request.
    positive_window=re.sub(r'\b(?:not before|no earlier than)\b',' ',message,flags=re.I)
    if re.search(r"\b(not|never|without|except|unless|cannot|no|if|when|whenever|provided)\b|n['’]t\b",positive_window,re.I):
        raise Invalid('Negated or conditional scheduling requests need clarification. Use direct positive instructions; explicit not-before windows are supported.')
    clauses=request_clauses(message)
    patch={};seen=set();cited_times=set();group_obligations=[];coverage={}
    for raw in changes:
        change=Change.model_validate(raw).model_dump() if isinstance(raw,dict) else raw.model_dump()
        job,field,value,quote=(change[k] for k in ('job_id','field','value','evidence'))
        if not quote or quote not in message: raise Invalid('Evidence must be an exact quote from the current user message.')
        clause=quote.strip(' ,.!?;')
        if clause not in clauses: raise Invalid('Evidence must quote the complete instruction clause, preserving all job names and intent words.')
        clause_index=clauses.index(clause)
        coverage.setdefault(clause_index,set()).add(job)
        if (job,field) in seen: raise Invalid('Only one change per job and field; ask about conflicting requests.')
        seen.add((job,field));lower=quote.lower()
        if field=='quiet_after':
            if (job!='household' or not re.search(r'\b(quiet|silent|noise|noisy)\b',lower)
                    or not re.search(r'\b(after|from|starting|by)\b',lower) or re.search(r'\b(until|before)\b',lower)):
                raise Invalid('quiet_after needs household evidence explicitly requesting quiet/noise cutoff.')
        else:
            if job not in JOB_ALIASES or not re.search(JOB_ALIASES[job],lower):
                raise Invalid('Quote must explicitly name this supported job.')
            group_obligations.append((field,{j for j,pattern in JOB_ALIASES.items() if re.search(pattern,lower)}))
        if field=='skip':
            if value not in ('true','false'): raise Invalid('skip value must be true or false text.')
            omit=bool(re.search(r"\b(skip|omit|exclude|cancel)\b|\b(?:do not|don't|don’t)\s+(?:run|use|schedule)\b",lower))
            restore=bool(re.search(r'\b(restore|include|resume|unskip)\b',lower))
            negated_omit=bool(re.search(r"\b(?:do not|don't|don’t|never)\s+(?:skip|omit|exclude|cancel)\b",lower))
            if (value=='true' and (not omit or restore or negated_omit)) or (value=='false' and (not restore or omit)):
                raise Invalid('Skipping requires an explicit omit request; restoring requires include/restore/resume.')
            patch.setdefault('jobs',{}).setdefault(job,{})[field]=value=='true'
            continue
        slot(value)
        if evidence_times(quote)!={value}:
            raise Invalid('Quote must contain one unambiguous time matching the requested value. Do not invent times.')
        cited_times.add(value)
        start=bool(re.search(r'\b(start(?:ing)?|not before|no earlier than|earliest)\b',lower))
        finish=bool(re.search(r'\b(finish(?:ed)?|ready|complete(?:d)?|done|deadline)\b',lower))
        if field=='earliest' and (not start or finish or re.search(r'\bby\b',lower) or ('before' in lower and not re.search(r'not before|no earlier than',lower))):
            raise Invalid('earliest changes require an explicit start/not-before instruction, never a finish/ready deadline.')
        if field=='latest_finish' and (not re.search(r'\b(by|before|no later than)\b',lower) or (start and not finish)):
            raise Invalid('latest_finish requires an explicit finish/ready/by deadline, not a start instruction.')
        if field=='quiet_after': patch[field]=value
        else: patch.setdefault('jobs',{}).setdefault(job,{})[field]=value
    if evidence_times(message)-cited_times:
        raise Invalid('A clock time in this request is unaddressed. Include every requested time with precise evidence, or ask for clarification.')
    for field,jobs in group_obligations:
        if any((job,field) not in seen for job in jobs):
            raise Invalid('A shared instruction names multiple jobs; include the same field change for each named job.')
    for index,clause in enumerate(clauses):
        requested_jobs={j for j,pattern in JOB_ALIASES.items() if re.search(pattern,clause,re.I)}
        generic_plan=bool(re.fullmatch(r'(?:please )?(?:plan|schedule|optimize)(?: my| the)?(?: evening)?',clause,re.I))
        if not generic_plan and (evidence_times(clause) or re.search(ACTION_WORDS,clause,re.I)):
            if re.search(r'\b(quiet|silent|noise|noisy)\b',clause,re.I): requested_jobs.add('household')
            if not requested_jobs:
                raise Invalid('This instruction names no supported chore. Only dryer, dishwasher, ebike and quiet cutoffs are supported; clarify every requested action.')
            if requested_jobs-coverage.get(index,set()):
                raise Invalid('Every requested job in every instruction clause needs its own grounded change. Do not omit a repeated deadline or skip request.')
            # This is a bounded command vocabulary, not general English parsing.
            # Reject unknown companions rather than silently dropping e.g. a fridge.
            remaining=clause.lower()
            for pattern in JOB_ALIASES.values(): remaining=re.sub(pattern,' ',remaining)
            words=set(re.findall(r'[a-z]+',remaining))
            allowed=set('please the my our both optional and keep have make start starting earliest not before no earlier than finish finished finishing ready complete completed done deadline by at after from quiet silent noise noisy cutoff skip omit exclude cancel restore include resume unskip run use schedule pm am p m a midnight tonight'.split())
            if words-allowed:
                raise Invalid('Part of this instruction is outside the supported chore commands. Split it into direct dryer, dishwasher, ebike or quiet-cutoff instructions, or review the manual controls.')
    if not changes and re.search(r'\b(skip|omit|cancel|restore|include|resume)\b',message,re.I):
        raise Invalid('Explicit skip/restore instruction needs a grounded change; do not silently ignore it.')
    return patch
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None

def model_details():
    opener=build_opener(ProxyHandler({}),NoRedirect())
    try:
        with opener.open(Request(HOST+'/api/show',data=json.dumps({'model':MODEL}).encode(),headers={'Content-Type':'application/json'}),timeout=5) as r:
            details=json.load(r)
        with opener.open(HOST+'/api/tags',timeout=5) as r: inventory=json.load(r)['models']
        match=next((m for m in inventory if m.get('name')==MODEL),None)
        if details.get('remote_host') or details.get('remote_model') or 'tools' not in details.get('capabilities',[]) or not match or not match.get('digest'):
            raise ValueError('Installed tool-capable model required; Ollama cloud descriptors are rejected.')
        return {'model_id':MODEL,'model_digest':match['digest'],**runtime_details()}
    except Exception as exc:
        raise RunError(f'Configured Ollama {MODEL} is unavailable. No fallback ran.') from exc

SYSTEM='''You are Watt If, a fictional Alexa+ household scheduling experience.
Use read_household to inspect the household when needed. Current saved constraints are authoritative.
propose_evening automatically reads the same household snapshot as a recorded dependency if needed.
Only optional dryer, dishwasher, and ebike are supported; never schedule other or critical equipment.
Never claim actual Alexa integration, device control, live rates, measured savings, or approval.
Input is JSON with the actual request and numbered source_clauses. Use only this current input.
For a scheduling request call propose_evening with a list of typed changes citing clause_id.
The server resolves the exact original quote; NEVER copy, paraphrase, or provide evidence text.
Example user: 'Have the ebike ready by 10 pm.'
Tool: {"changes":[{"job_id":"ebike","field":"latest_finish","value":"22:00","clause_id":1}]}
Example user: 'Start the dishwasher at 7 pm. Keep quiet after 9 pm.'
Tool: {"changes":[{"job_id":"dishwasher","field":"earliest","value":"19:00","clause_id":1},{"job_id":"household","field":"quiet_after","value":"21:00","clause_id":2}]}
Example user: 'Skip the dryer.'
Tool: {"changes":[{"job_id":"dryer","field":"skip","value":"true","clause_id":1}]}
Example user: 'Restore the dryer.'
Tool: {"changes":[{"job_id":"dryer","field":"skip","value":"false","clause_id":1}]}
These are format examples, never user requests. Use only the CURRENT user's times and words.
Each change cites the numbered source clause supporting it. A shared deadline uses the SAME clause_id for both jobs.
quiet_after ALWAYS uses job_id household, never dryer/dishwasher/ebike.
If a tool returns a validation error, inspect it and correct your changes ONCE within the turn budget.
After a second validation failure ask for clarification. Never report a rejected change as scheduled.
Ready BY, finished BY, done BY are latest_finish. Never turn those into an earliest start.
Only change earliest when the user explicitly says START, NOT BEFORE, or EARLIEST.
Never invent a start time to fit a finish deadline. The solver chooses starts.
Never omit a requested job or instruction clause. Negated/conditional instructions require clarification; explicit not-before windows are supported.
If two jobs share a deadline, make two change objects using the same source clause_id.
For quiet cutoff use job_id household, field quiet_after, and the quiet/noise clause_id.
For skipping use field skip, value "true", and the skip/omit/cancel clause_id. Restoring uses "false".
All times are HH:MM half-hours between 18:00 and 24:00. latest_finish is finish time, not start time.
quiet_after applies only to noisy dryer and dishwasher. Quiet time never skips a job automatically.
Preserve every unmentioned existing constraint. skip true omits that optional job; false restores it.
For 'plan my evening' or 'optimize' use changes=[]. Resetting all defaults uses the separate manual UI reset.
If time, supported job, or intent is unclear, ask a concise clarifying question and do not propose.
If constraints conflict, return the actual infeasible tool result. Do not silently relax constraints,
change jobs, shift dates, change wattage or invent user permission. Propose_evening does not approve.
Explain only actual tool output. Peak is optimized first; cost need not decrease. Included jobs retain
their modeled energy. Skipped jobs are omitted work, never efficiency savings. User must review then
press Approve in the UI to obtain an advisory calendar. No model instruction can approve.
'''

def run(message, store, timeout_seconds=120, model=None):
    if not isinstance(message,str) or not 1<=len(message.strip())<=2000: raise Invalid('Message must contain 1–2000 characters.')
    if sources()!=LOADED_SOURCES: raise RunError('Source changed. Restart the app before inference.')
    initial=store.begin_request(message)
    message=initial['pending_request']['message']
    source_clauses=[{'clause_id':i+1,'text':clause} for i,clause in enumerate(request_clauses(message))]
    snapshot=sources()
    if model is None:
        details=model_details()
        from strands.models.ollama import OllamaModel
        model=OllamaModel(host=HOST,model_id=MODEL,temperature=0,max_tokens=1400,options={'num_ctx':8192},
                          ollama_client_args={'timeout':90.0,'trust_env':False,'follow_redirects':False})
        evidence='real-model-inference'
    else: details={'model_id':'injected-test-fixture','execution_location':'test_fixture'}; evidence='sdk-fixture'
    from strands import Agent,tool
    from strands.tools.executors import SequentialToolExecutor
    events=[]; prepared={}; reads=0; attempts=0

    def record_household_read(initiated_by):
        nonlocal reads
        reads+=1
        if reads>2: raise Invalid('Household read quota reached.')
        result={k:v for k,v in initial.items() if k!='latest_proposal'}
        result['default_constraints']={'quiet_after':'21:00','jobs':{j['id']:{'earliest':'18:00','latest_finish':'24:00','skip':False} for j in initial['jobs']}}
        events.append({'tool':'read_household','args':{},'result':result,'elapsed_ms':0,'initiated_by':initiated_by})
        return result

    @tool
    def read_household() -> dict:
        """Read the actual fictional household and its current saved constraints."""
        return record_household_read('model_tool')

    @tool
    def propose_evening(changes: list[IndexedChange]) -> dict:
        """Preview changed household constraints; cannot approve or operate devices.

        Args:
            changes: Explicit changes with job_id, field, value, and numbered clause_id from the input. The server supplies exact quote text. Empty list preserves current constraints. A finish/ready/by time sets latest_finish, never earliest.
        """
        nonlocal attempts
        attempts+=1
        started=time.monotonic()
        if not reads: record_household_read('proposal_dependency')
        if attempts>3: raise Invalid('Proposal quota reached.')
        if prepared: return {'status':'already_prepared','proposal':prepared['preview']}
        change_values=[c if isinstance(c,dict) else c.model_dump() for c in changes]
        resolved=[]
        try:
            resolved=resolve_indexed_changes(change_values,message)
            constraints=grounded_patch(resolved,message)
            for change in resolved:
                if change['field']=='earliest':
                    exact=bool(re.search(r'\bstart(?:ing)?\b.*\bat\b',change['evidence'],re.I))
                    constraints['jobs'][change['job_id']]['exact_start']=change['value'] if exact else None
            preview=store.preview(constraints,initial)
            for change in resolved:
                job,field=change['job_id'],change['field']
                if job != 'household' and initial['constraints']['jobs'][job]['skip'] and field != 'skip':
                    if constraints.get('jobs',{}).get(job,{}).get('skip') is not False:
                        raise Invalid('This chore is currently left out. Explicitly restore/include it before changing its time.')
                if field == 'earliest' and re.search(r'\bstart(?:ing)?\b.*\bat\b',change['evidence'],re.I) and preview['status']=='proposed':
                    scheduled=next((j for j in preview['schedule'] if j['job_id']==job),None)
                    if scheduled and scheduled['start'] != change['value']:
                        raise Invalid('This would not start at the exact requested time. Use an explicit not-before window or adjust the manual controls and review the actual timetable.')
        except (Invalid,ValueError) as exc:
            events.append({'tool':'propose_evening','args':{'changes':change_values},'resolved_changes':resolved,'result':{'error':str(exc)},'elapsed_ms':round((time.monotonic()-started)*1000)})
            raise
        prepared.update(constraints=constraints,preview=preview)
        result={'status':preview['status'],'constraints':preview['constraints'],'schedule':preview['schedule'],
                'metrics':preview['metrics'],'baseline_metrics':preview['baseline_metrics'],'explanation':preview['explanation'],
                'approval_required':True,'advisory_only':True}
        events.append({'tool':'propose_evening','args':{'changes':change_values},'resolved_changes':resolved,'validated_patch':constraints,'result':result,'elapsed_ms':round((time.monotonic()-started)*1000)})
        return result

    agent=Agent(model=model,system_prompt=SYSTEM,tools=[read_household,propose_evening],callback_handler=None,
                tool_executor=SequentialToolExecutor(),retry_strategy=None)
    started_at=datetime.now(timezone.utc).isoformat(); timer=time.monotonic()
    async def invoke():
        request=json.dumps({'request':message,'source_clauses':source_clauses})
        return await asyncio.wait_for(agent.invoke_async(request,limits=LIMITS),timeout=timeout_seconds)
    result=None;failure=None
    try: result=asyncio.run(invoke())
    except Exception as exc: failure=exc
    trace=dict(provider='ollama' if evidence=='real-model-inference' else 'fixture',evidence_type=evidence,**details,
               sdk_version=version('strands-agents'),model_thinking=False,started_at=started_at,completed_at=datetime.now(timezone.utc).isoformat(),
               elapsed_seconds=round(time.monotonic()-timer,3),limits=LIMITS,stop_reason=getattr(result,'stop_reason',None),
               tool_events=events,sdk_tool_events=tool_transcript(agent.messages),
               read_count=reads,proposal_attempt_count=attempts,validated_preview=bool(prepared),
               source_clauses=source_clauses,grounding_method='server_resolved_clause_ids',
               usage=dict(result.metrics.accumulated_usage) if result else None,source_sha256=snapshot,
               household_revision=initial['revision'],request_id=initial['pending_request']['id'],message_sha256=digest(message),approval_performed=False)
    if failure:
        trace.update(status='failed',error_type=type(failure).__name__)
        raise RunError('Strands failed or timed out. No proposal was saved; no fallback ran.',trace) from failure
    if result.stop_reason not in ('end_turn','stop_sequence') or not reads or (attempts and not prepared):
        trace['status']='failed'
        raise RunError('Strands did not finish a valid household interaction. No proposal saved.',trace)
    if sources()!=snapshot:
        trace['status']='source_changed'
        raise RunError('Source changed during inference. No proposal saved; restart.',trace)
    trace['status']='completed'
    reply_source='validated_proposal_template' if prepared else 'model_clarification'
    trace['reply_source']=reply_source
    trace['model_response_sha256']=digest(str(result))
    try: proposal=store.propose(prepared['constraints'],expected_revision=initial['revision'],execution=trace) if prepared else None
    except Conflict as exc:
        trace['status']='stale_state';exc.trace=trace
        raise
    return {'reply':proposal_reply(proposal) if proposal else str(result),'reply_source':reply_source,'proposal':proposal,'trace':trace,
            'pending_request':store.household().get('pending_request')}

if __name__=='__main__':
    import argparse
    from household import Store
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('message');p.add_argument('--state',required=True);p.add_argument('--receipt')
    p.add_argument('--ollama-port',type=int,default=11434)
    p.add_argument('--model-location',choices=['local','runpod_ssh_tunnel'],default='local')
    args=p.parse_args()
    configure_runtime(args.ollama_port,args.model_location)
    try: output=run(args.message,Store(args.state));code=0
    except (Invalid,RunError) as exc:
        output={'error':str(exc),'proposal':None,'trace':getattr(exc,'trace',None)};code=1
    encoded=json.dumps(output,indent=2)
    if args.receipt:Path(args.receipt).write_text(encoded+'\n',encoding='utf-8')
    print(encoded)
    raise SystemExit(code)
