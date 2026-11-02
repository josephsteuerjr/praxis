"""Propose exact native-platform exemptions from frozen source decorators only.

No module imports, arbitrary eval, observed-failure promotion or blanket skips.
Unknown/component/audit guards remain observations for separate review.
"""
from pathlib import Path
import argparse,ast,hashlib,json,operator,os,sys

class Unknown(Exception):pass
def statements(nodes,names):
    for node in nodes:
        if isinstance(node,ast.Return):return True,value(node.value,names)
        if isinstance(node,ast.If):
            branch=node.body if value(node.test,names) else node.orelse
            returned,result=statements(branch,names)
            if returned:return True,result
        elif isinstance(node,ast.Pass) or isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant):continue
        else:raise Unknown('non-static reason function statement')
    return False,None
def value(node,names):
    if isinstance(node,ast.Constant):return node.value
    if isinstance(node,ast.Name):
        if node.id not in names:raise Unknown(node.id)
        return names[node.id]
    if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name):
        if (node.value.id,node.attr)==('os','name'):return os.name
        if (node.value.id,node.attr)==('sys','platform'):return sys.platform
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not):return not value(node.operand,names)
    if isinstance(node,ast.BoolOp):
        unknown=False
        for item in node.values:
            try:result=bool(value(item,names))
            except Unknown:unknown=True;continue
            if isinstance(node.op,ast.And) and not result:return False
            if isinstance(node.op,ast.Or) and result:return True
        if unknown:raise Unknown('conditional platform expression')
        return isinstance(node.op,ast.And)
    if isinstance(node,ast.Compare):
        left=value(node.left,names)
        for op,right_node in zip(node.ops,node.comparators):
            right=value(right_node,names)
            if isinstance(op,ast.Eq):match=left==right
            elif isinstance(op,ast.NotEq):match=left!=right
            elif isinstance(op,ast.In):match=left in right
            elif isinstance(op,ast.NotIn):match=left not in right
            elif isinstance(op,ast.Is):match=left is right
            elif isinstance(op,ast.IsNot):match=left is not right
            else:raise Unknown('comparison')
            if not match:return False
            left=right
        return True
    if isinstance(node,(ast.Tuple,ast.List)):return [value(item,names) for item in node.elts]
    if isinstance(node,ast.IfExp):return value(node.body if value(node.test,names) else node.orelse,names)
    if isinstance(node,ast.Call) and not node.keywords:
        if isinstance(node.func,ast.Name):
            if node.func.id=='bool' and len(node.args)==1:return bool(value(node.args[0],names))
            function=names.get('__functions__',{}).get(node.func.id)
            if function is not None and not node.args and not function.args.args:
                returned,result=statements(function.body,names)
                if returned:return result
                raise Unknown('no static reason returned')
        if isinstance(node.func,ast.Name) and node.func.id=='hasattr' and len(node.args)==2:
            if isinstance(node.args[0],ast.Name) and node.args[0].id=='os':
                name=value(node.args[1],names)
                if isinstance(name,str) and not name.startswith('_'):return hasattr(os,name)
        if isinstance(node.func,ast.Attribute) and node.func.attr in ('startswith','endswith') and len(node.args)==1:
            base=value(node.func.value,names);arg=value(node.args[0],names)
            if isinstance(base,str) and isinstance(arg,str):return getattr(base,node.func.attr)(arg)
        if isinstance(node.func,ast.Attribute) and node.func.attr in ('exists','is_dir','is_file') and not node.args:
            path=node.func.value
            if (isinstance(path,ast.Call) and isinstance(path.func,ast.Name) and path.func.id=='Path'
                    and len(path.args)==1 and not path.keywords):
                name=value(path.args[0],names)
                if isinstance(name,str) and (name=='/proc' or name.startswith('/proc/')) and os.name=='nt':return False
    raise Unknown(ast.unparse(node))

def guard(node,names):
    for decorator in node.decorator_list:
        if not isinstance(decorator,ast.Call) or not isinstance(decorator.func,ast.Attribute):continue
        kind=decorator.func.attr
        if kind not in ('skipIf','skipUnless') or len(decorator.args)<2:continue
        expression=ast.unparse(decorator)
        try:
            condition=bool(value(decorator.args[0],names));reason=value(decorator.args[1],names)
            if not isinstance(reason,str) or not reason:raise Unknown('nonliteral reason')
        except Unknown:return {'unknown_guard':expression}
        if condition if kind=='skipIf' else not condition:return {'reason':reason,'guard':expression}
    return {}

def inspect(name,raw,lane):
    module=ast.parse(raw.decode('utf-8-sig'))
    names={'__functions__':{item.name:item for item in module.body if isinstance(item,ast.FunctionDef)}}
    for item in module.body:
        targets=item.targets if isinstance(item,ast.Assign) else [item.target] if isinstance(item,ast.AnnAssign) else []
        for target in targets:
            if isinstance(target,ast.Name):
                try:names[target.id]=value(item.value,names)
                except (Unknown,TypeError):names.pop(target.id,None)
    classes={item.name:item for item in module.body if isinstance(item,ast.ClassDef)}
    def methods(cls,seen=()):
        if cls.name in seen:return {}
        result={}
        for base in cls.bases:
            if isinstance(base,ast.Name) and base.id in classes:result.update(methods(classes[base.id],(*seen,cls.name)))
        for item in cls.body:
            if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) and item.name.startswith('test'):
                result[item.name]=item
        return result
    rows=[];unknown=[];sha=hashlib.sha256(raw).hexdigest()
    for cls in classes.values():
        class_guard=guard(cls,names)
        for method,node in methods(cls).items():
            decision=class_guard if class_guard else guard(node,names)
            if not decision:continue
            case=Path(name).stem+'.'+cls.name+'.'+method
            record={'source':name,'source_sha256':sha,'case':case,'key':lane+'::'+case,**decision}
            (unknown if 'unknown_guard' in decision else rows).append(record)
    return rows,unknown

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    assert sys.platform=='win32','Native Windows capability observation only'
    source=args.source.resolve();sys.path.insert(0,str(source/'desk/installer'))
    from portable_sources import compose_core
    core,_=compose_core(source);rows=[];unknown=[]
    candidates=[(name,entry.data,'core') for name,entry in core.items() if Path(name).name.startswith('test') and name.endswith('.py')]
    candidates.extend((p.relative_to(source).as_posix(),p.read_bytes(),p.relative_to(source).as_posix())
        for p in (source/'desk/tests').rglob('*.py') if p.name.startswith(('t_','test')))
    for name,raw,lane in candidates:
        try:found,unclassified=inspect(name,raw,lane)
        except (SyntaxError,UnicodeError):continue # Canonical import lane owns syntax failures.
        rows.extend(found);unknown.extend(unclassified)
    proposed={}
    for row in rows:
        if row['key'] in proposed:assert proposed[row['key']]==row['reason']
        proposed[row['key']]=row['reason']
    result={'platform':sys.platform,'proposal_only':True,'independent_review_required':True,
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'python_version':sys.version,'python_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        'policy':dict(sorted(proposed.items())),'evidence':rows,'unknown_guards':unknown,
        'runtime_component_failure_or_audit_absence_not_auto_permitted':True}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'platform_cases':len(proposed),'unknown_guards':len(unknown),'proposal_only':True}))

if __name__=='__main__':main()
