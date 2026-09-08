"""PeakShift: exact small-instance scheduling of explicitly deferrable loads.
Advisory simulation, not electrical control. Never defer safety-critical equipment.
All prices, carbon intensities and loads are operator inputs, not live measurements.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

class Invalid(ValueError): pass

def number(x:Any,name:str,lo:int,hi:int)->int:
    if type(x) is not int or not lo<=x<=hi: raise Invalid(f'{name} must be integer {lo}..{hi}')
    return x

def validate(data:dict)->None:
    if not isinstance(data,dict): raise Invalid('Input must be an object')
    base=data.get('baseline_watts')
    if not isinstance(base,list) or not 1<=len(base)<=24: raise Invalid('1..24 slots required')
    n=len(base)
    for x in base: number(x,'baseline watts',0,100000)
    for name in ('carbon_g_per_kwh','tariff_millicents_per_kwh'):
        a=data.get(name)
        if not isinstance(a,list) or len(a)!=n: raise Invalid(name+' must match slots')
        for x in a: number(x,name,0,10000000)
    number(data.get('slot_minutes'),'slot_minutes',1,1440)
    number(data.get('capacity_watts',100000),'capacity_watts',1,100000)
    jobs=data.get('jobs')
    if not isinstance(jobs,list) or len(jobs)>8: raise Invalid('At most eight jobs')
    seen=set()
    for j in jobs:
        if not isinstance(j,dict) or not isinstance(j.get('id'),str) or not j['id'] or j['id'] in seen:
            raise Invalid('Unique nonempty job IDs required')
        seen.add(j['id'])
        if j.get('deferrable') is not True: raise Invalid('Explicitly deferrable loads only')
        number(j.get('watts'),'watts',1,100000)
        d=number(j.get('duration'),'duration',1,n)
        e=number(j.get('earliest'),'earliest',0,n-d)
        number(j.get('latest'),'latest start',e,n-d)

def evaluate(data:dict,starts:dict)->dict:
    validate(data)
    if not isinstance(starts,dict) or set(starts)!={j['id'] for j in data['jobs']}:
        raise Invalid('Every job must have exactly one start')
    load=data['baseline_watts'][:]
    for j in data['jobs']:
        s=number(starts[j['id']],'start',j['earliest'],j['latest'])
        for t in range(s,s+j['duration']): load[t]+=j['watts']
    minutes=data['slot_minutes']
    carbon_n=sum(w*c for w,c in zip(load,data['carbon_g_per_kwh']))
    price_n=sum(w*c for w,c in zip(load,data['tariff_millicents_per_kwh']))
    return {'peak_watts':max(load),'load_watts':load,
            'energy_kwh':sum(load)*minutes/60000,
            'carbon_g':carbon_n*minutes/60000,
            'cost_dollars':price_n*minutes/60000/100000,
            'objective':[max(load),carbon_n,price_n],
            'within_capacity':max(load)<=data.get('capacity_watts',100000)}

def solve(data:dict,node_budget:int=500000)->dict:
    """Lexicographic objective: peak W, then carbon numerator, then price numerator.
    Enumerates discrete nonpreemptive starts with admissible lower bounds.
    A node-budget stop is explicitly NOT a proof of optimality or infeasibility.
    """
    validate(data);number(node_budget,'node_budget',1,5000000)
    base=data['baseline_watts'];n=len(base);cap=data.get('capacity_watts',100000)
    jobs=sorted(data['jobs'],key=lambda j:(j['latest']-j['earliest'],-j['watts'],j['id']))
    carbon=data['carbon_g_per_kwh'];price=data['tariff_millicents_per_kwh']
    load=base[:];current={};best=None;best_starts=None;nodes=0;stopped=False;pruned=0
    avg_bound=math.ceil((sum(base)+sum(j['watts']*j['duration'] for j in jobs))/n)
    minima=[]
    for j in jobs:
        carbon_costs=[];price_costs=[]
        for s in range(j['earliest'],j['latest']+1):
            carbon_costs.append(j['watts']*sum(carbon[s:s+j['duration']]))
            price_costs.append(j['watts']*sum(price[s:s+j['duration']]))
        minima.append((min(carbon_costs),min(price_costs)))
    def visit(i):
        nonlocal nodes,stopped,best,best_starts,pruned
        if nodes>=node_budget: stopped=True;return
        nodes+=1
        if max(load)>cap: pruned+=1;return
        lower=(max(max(load),avg_bound),
               sum(w*c for w,c in zip(load,carbon))+sum(m[0] for m in minima[i:]),
               sum(w*c for w,c in zip(load,price))+sum(m[1] for m in minima[i:]))
        if best is not None and lower>=best: pruned+=1;return
        if i==len(jobs): best=lower;best_starts=current.copy();return
        j=jobs[i]
        options=range(j['earliest'],j['latest']+1)
        # Least added peak first improves incumbents; it does not alter exactness.
        options=sorted(options,key=lambda s:(max(load[t]+j['watts'] for t in range(s,s+j['duration'])),
                        sum(carbon[s:s+j['duration']]),s))
        for s in options:
            for t in range(s,s+j['duration']):load[t]+=j['watts']
            current[j['id']]=s;visit(i+1)
            for t in range(s,s+j['duration']):load[t]-=j['watts']
            del current[j['id']]
            if stopped: return
    visit(0)
    result={'status':('optimal' if best is not None else 'infeasible') if not stopped else 'budget_exhausted',
            'optimality_proven':not stopped,'nodes':nodes,'pruned':pruned,'starts':best_starts,
            'input_sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),
            'metrics':evaluate(data,best_starts) if best_starts is not None else None,
            'warning':'Synthetic or operator-input simulation only. No device control or measured savings.'}
    return result

if __name__=='__main__':
    import argparse
    from pathlib import Path
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input');ap.add_argument('--output');ap.add_argument('--node-budget',type=int,default=500000)
    args=ap.parse_args()
    try:
        data=json.loads(Path(args.input).read_text());result=solve(data,args.node_budget)
        result['earliest_start_baseline']=evaluate(data,{j['id']:j['earliest'] for j in data['jobs']})
        encoded=json.dumps(result,indent=2)
        if args.output:Path(args.output).write_text(encoded+'\n')
        else:print(encoded)
    except (Invalid,OSError,json.JSONDecodeError) as exc:ap.error(str(exc))
