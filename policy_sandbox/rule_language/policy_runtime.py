from __future__ import annotations
import ast
from decimal import Decimal
class Unknown:
 def __bool__(self):raise ValueError('unknown_fact')
UNKNOWN=Unknown()
class PolicyRuntime:
 ALLOWED=(ast.Expression,ast.BoolOp,ast.UnaryOp,ast.Compare,ast.Name,ast.Load,ast.Constant,ast.And,ast.Or,ast.Not,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.List,ast.Tuple,ast.BinOp,ast.Add,ast.Sub,ast.Mult,ast.Div)
 def compile(self,expression):
  tree=ast.parse(expression,mode='eval')
  for node in ast.walk(tree):
   if not isinstance(node,self.ALLOWED):raise ValueError('unsafe_policy_expression')
  return compile(tree,'<policy>','eval'),sorted({node.id for node in ast.walk(tree) if isinstance(node,ast.Name)})
 def evaluate(self,expression,facts,units=None):
  code,names=self.compile(expression);missing=[name for name in names if name not in facts]
  if missing:return {'result':'unknown','missing':missing,'explanation':['缺少事实 '+name for name in missing]}
  try:value=bool(eval(code,{'__builtins__':{}},facts))
  except (TypeError,ValueError) as exc:return {'result':'invalid','missing':[],'explanation':[str(exc)]}
  return {'result':'eligible' if value else 'ineligible','missing':[],'explanation':['表达式计算结果为 '+str(value).lower()],'facts_used':names,'units':units or {}}
 def compare(self,old_expression,new_expression,people):
  changed=[]
  for person in people:
   old=self.evaluate(old_expression,person.get('facts',{}));new=self.evaluate(new_expression,person.get('facts',{}))
   if old['result']!=new['result']:changed.append({'id':person['id'],'from':old['result'],'to':new['result']})
  return changed
 def budget(self,expression,people,benefit,budget):
  eligible=[]
  for person in people:
   if self.evaluate(expression,person.get('facts',{}))['result']=='eligible':eligible.append(str(person['id']))
  total=Decimal(str(benefit))*len(eligible);cap=Decimal(str(budget));return {'eligible_ids':eligible,'required':str(total),'budget':str(cap),'within_budget':total<=cap,'shortfall':str(max(Decimal('0'),total-cap))}
 def validate_units(self,comparisons,units):
  errors=[]
  for left,right in comparisons:
   if left in units and right in units and units[left]!=units[right]:errors.append('unit_mismatch:'+left+':'+right)
  return errors
