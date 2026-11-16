from __future__ import annotations
from ..rule_language.policy_runtime import PolicyRuntime
import datetime as dt
import decimal
import math
import statistics
import ast
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

class DomainError(RuntimeError):
    pass

@dataclass(frozen=True)
class AnalysisResult:
    key: str
    accepted: bool
    value: decimal.Decimal
    reasons: tuple[str,...]
    facts: Mapping[str,Any]=field(default_factory=dict)

class PolicyEngineBase:
    algorithm='policy'
    def normalize(self,rows):
        result=[];seen=set()
        for position,row in enumerate(rows):
            item=dict(row);key=str(item.get('id') or position)
            if key in seen:continue
            seen.add(key);item['_key']=key;item['_position']=position;result.append(item)
        return result
    def rank(self,subjects,context):return sorted([self.evaluate(item,context) for item in self.normalize(subjects)],key=lambda result:(not result.accepted,-result.value,result.key))
    def allocate(self,subjects,context):
        remaining=decimal.Decimal(str(context.get('capacity',100)))-decimal.Decimal(str(context.get('reserved',0)));accepted=[];rejected=[]
        by_key={str(item.get('id')):item for item in self.normalize(subjects)}
        for result in self.rank(subjects,context):
            demand=decimal.Decimal(str(by_key[result.key].get('demand',1)))
            if result.accepted and demand<=remaining:accepted.append(result);remaining-=demand
            else:rejected.append(AnalysisResult(result.key,False,result.value,result.reasons+('allocation_exhausted',),result.facts))
        return accepted,rejected
    def summarize(self,results):
        values=[float(result.value) for result in results];reasons=Counter(reason for result in results for reason in result.reasons)
        return {'count':len(results),'accepted':sum(result.accepted for result in results),'rejected':sum(not result.accepted for result in results),'mean':statistics.fmean(values) if values else 0.0,'median':statistics.median(values) if values else 0.0,'reasons':dict(sorted(reasons.items()))}
    def compare(self,left,right):
        a={item.key:item for item in left};b={item.key:item for item in right};changes=[]
        for key in sorted(set(a)|set(b)):
            old=a.get(key);new=b.get(key)
            if old is None:changes.append({'key':key,'change':'added','accepted':new.accepted})
            elif new is None:changes.append({'key':key,'change':'removed','accepted':old.accepted})
            elif old.accepted!=new.accepted or old.value!=new.value:changes.append({'key':key,'change':'changed','from':str(old.value),'to':str(new.value),'accepted_from':old.accepted,'accepted_to':new.accepted})
        return {'changes':changes,'change_count':len(changes)}

class PolicyEngine(PolicyEngineBase):
    def __init__(self): self.runtime=PolicyRuntime()
    ALLOWED=(ast.Expression,ast.BoolOp,ast.UnaryOp,ast.Compare,ast.Name,ast.Load,ast.Constant,ast.And,ast.Or,ast.Not,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.List,ast.Tuple)
    def compile(self,expression):
        tree=ast.parse(expression,mode='eval')
        for node in ast.walk(tree):
            if not isinstance(node,self.ALLOWED):raise DomainError('unsafe_policy_expression')
        return compile(tree,'<policy>','eval')
    def evaluate(self,item,context):
        if context.get('units') or context.get('three_valued'):
            key=str(item.get('id','unknown')); facts=dict(item.get('facts',item)); outcome=self.runtime.evaluate(str(context.get('expression','age >= 18')),facts,context.get('units')); accepted=outcome['result']=='eligible'; reasons=() if accepted else (outcome['result'],); benefit=decimal.Decimal(str(context.get('benefit',1))) if accepted else decimal.Decimal('0'); return AnalysisResult(key,accepted,benefit,reasons,outcome)
        key=str(item.get('id','unknown'));expression=str(context.get('expression','age >= 18'));facts=dict(item.get('facts',item));reasons=[]
        try:eligible=bool(eval(self.compile(expression),{'__builtins__':{}},facts))
        except NameError as exc:reasons.append('missing_fact:'+str(exc).split("'")[1]);eligible=False
        except (SyntaxError,DomainError) as exc:reasons.append(str(exc));eligible=False
        benefit=decimal.Decimal(str(context.get('benefit',1))) if eligible else decimal.Decimal('0');return AnalysisResult(key,eligible,benefit,tuple(reasons),{'expression':expression,'facts_used':sorted(facts)})
