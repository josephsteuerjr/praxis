"""Portable structural Python facts. No source text, comments or literal values."""
import ast
import io
from pathlib import Path
import tokenize

VERSION='python-ast-evidence-2'


def point(lines,line,byte):
    text=lines[max(0,line-1)] if 0<line<=len(lines) else ''
    return {'line':line,'char':len(text.encode('utf-8')[:byte].decode('utf-8','ignore')),'byte':byte}


def ranges(node,lines):
    start=point(lines,node.lineno,node.col_offset)
    end=point(lines,getattr(node,'end_lineno',node.lineno),getattr(node,'end_col_offset',node.col_offset))
    return {'start':start,'end':end}


def dotted(node):
    if isinstance(node,ast.Name):return [node.id]
    if isinstance(node,ast.Attribute):
        parent=dotted(node.value)
        return parent+[node.attr] if parent else None
    return None


def parse(text):
    import forge_intelligence
    tree=ast.parse(text);lines=[s.rstrip('\r') for s in text.split('\n')]
    legacy,_=forge_intelligence._python_symbols(Path('module.py'),Path('.'),parsed=tree)
    signatures={(r['name'],r['line']):r.get('signature','') for r in legacy}
    facts={'definitions':[],'calls':[],'references':[],'scopes':[], 'imports':[], 'text_ranges':[], 'dynamic_imports':[]}
    scopes=facts['scopes']
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope=0;self.names=[];self.conditional=0;self.callees=set()
            scopes.append({'parent':None,'kind':'module','qualname':'','bindings':{}})
        def bind(self,name,kind,**data):
            scopes[self.scope]['bindings'].setdefault(name,[]).append(dict(kind=kind,conditional=bool(self.conditional),**data))
        def fact(self,node,**values):
            return dict(ranges(node,lines),scope=self.scope,**values)
        def definition(self,node,name,kind):
            qualname='.'.join(self.names+[name])
            facts['definitions'].append(self.fact(node,name=name,qualname=qualname,kind=kind,signature=signatures.get((qualname,node.lineno),'')))
            return qualname
        def enter(self,node,kind,name,args=None):
            old=self.scope;self.scope=len(scopes)
            scopes.append({'parent':old,'kind':kind,'qualname':'.'.join(self.names+[name]),'bindings':{}})
            self.names.append(name)
            if args:
                for a in [*args.posonlyargs,*args.args,*args.kwonlyargs]:
                    self.bind(a.arg,'parameter')
                for a in [args.vararg,args.kwarg]:
                    if a:self.bind(a.arg,'parameter')
            if isinstance(node.body,list):
                for item in node.body:self.visit(item)
            else:self.visit(node.body)
            self.names.pop();self.scope=old
        def function(self,node):
            qual=self.definition(node,node.name,'async_function' if isinstance(node,ast.AsyncFunctionDef) else 'function')
            self.bind(node.name,'definition',qualname=qual)
            for item in [*node.decorator_list,*node.args.defaults,*[a for a in node.args.kw_defaults if a is not None]]:
                self.visit(item)
            self.enter(node,'function',node.name,node.args)
        visit_FunctionDef=function
        visit_AsyncFunctionDef=function
        def visit_ClassDef(self,node):
            qual=self.definition(node,node.name,'class');self.bind(node.name,'definition',qualname=qual)
            for item in [*node.decorator_list,*node.bases,*[k.value for k in node.keywords]]:self.visit(item)
            self.enter(node,'class',node.name)
        def visit_Lambda(self,node):
            for item in [*node.args.defaults,*[a for a in node.args.kw_defaults if a is not None]]:self.visit(item)
            self.enter(node,'function','<lambda>',node.args)
        def visit_Import(self,node):
            for alias in node.names:
                name=alias.asname or alias.name.split('.')[0]
                module=alias.name if alias.asname else name
                self.bind(name,'module',module=module,imported_module=alias.name,alias=bool(alias.asname))
                facts['imports'].append(self.fact(node,name=name,module=alias.name,imported='',level=0,alias=bool(alias.asname)))
        def visit_ImportFrom(self,node):
            for alias in node.names:
                name=alias.asname or alias.name
                self.bind(name,'symbol',module=node.module or '',imported=alias.name,level=node.level,alias=bool(alias.asname))
                facts['imports'].append(self.fact(node,name=name,module=node.module or '',imported=alias.name,level=node.level,alias=bool(alias.asname)))
        def visit_Name(self,node):
            if isinstance(node.ctx,(ast.Store,ast.Del)):self.bind(node.id,'write')
            elif isinstance(node.ctx,ast.Load) and id(node) not in self.callees:
                facts['references'].append(self.fact(node,name=node.id,chain=[node.id],kind='reference'))
        def visit_Attribute(self,node):
            if isinstance(node.ctx,ast.Load) and id(node) not in self.callees:
                facts['references'].append(self.fact(node,name=node.attr,chain=dotted(node),kind='reference'))
            self.visit(node.value)
        def visit_Call(self,node):
            self.callees.add(id(node.func))
            name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
            fact=self.fact(node.func,name=name,chain=dotted(node.func),kind='call',caller=scopes[self.scope]['qualname'])
            facts['calls'].append(fact)
            if name in ('__import__','import_module'):facts['dynamic_imports'].append(dict(fact))
            self.generic_visit(node)
        def assignment(self,node):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            for target in targets:
                for item in ast.walk(target):
                    if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Store):self.definition(item,item.id,'assignment')
            self.generic_visit(node)
        visit_Assign=assignment
        visit_AnnAssign=assignment
        visit_NamedExpr=assignment
        def visit_ExceptHandler(self,node):
            if node.name:self.bind(node.name,'write')
            self.generic_visit(node)
        def visit_MatchAs(self,node):
            if node.name:self.bind(node.name,'write')
            self.generic_visit(node)
        visit_MatchStar=visit_MatchAs
        def visit_MatchMapping(self,node):
            if node.rest:self.bind(node.rest,'write')
            self.generic_visit(node)
        def conditional_node(self,node):
            self.conditional+=1
            try:self.generic_visit(node)
            finally:self.conditional-=1
        visit_If=conditional_node
        visit_For=conditional_node
        visit_AsyncFor=conditional_node
        visit_While=conditional_node
        visit_Try=conditional_node
        visit_TryStar=conditional_node
        visit_Match=conditional_node
        def visit_Global(self,node):
            for name in node.names:self.bind(name,'global_or_nonlocal')
        visit_Nonlocal=visit_Global
    Visitor().visit(tree)
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.COMMENT,tokenize.STRING):
            # Positions only: never cache the token's text.
            facts['text_ranges'].append({'start':[tok.start[0],tok.start[1]],'end':[tok.end[0],tok.end[1]]})
    return facts


def import_module(binding,file_module,is_package):
    module=binding.get('module','');level=binding.get('level',0)
    if not level:return module
    package=file_module.split('.') if is_package else file_module.split('.')[:-1]
    if level>len(package):return ''
    parent=package[:len(package)-(level-1)]
    return '.'.join(parent+([module] if module else []))


def binding_for(facts,scope,name):
    current=scope;from_function=False
    while current is not None:
        item=facts['scopes'][current]
        if not (from_function and item['kind']=='class'):
            rows=item['bindings'].get(name,[])
            if rows:
                if len(rows)!=1 or rows[0].get('conditional'):return None,True
                value=rows[0]
                if value['kind'] in ('module','symbol','definition'):return value,False
                return None,True
            if '*' in item['bindings']:return None,True
        from_function=from_function or item['kind']=='function'
        current=item['parent']
    return None,False


def classify(fact,facts,file_module,is_package,target_name,target_module):
    chain=fact.get('chain');short=target_name.rsplit('.',1)[-1]
    row=dict(fact);row.pop('scope',None);row.pop('chain',None)
    row['qualname']='.'.join(chain) if chain else fact['name']
    binding,shadowed=binding_for(facts,fact['scope'],chain[0]) if chain else (None,False)
    row.update(method='python_ast',ambiguous=True,matches_target=None)
    if chain and len(chain)==1:
        if binding and binding['kind']=='symbol':
            module=import_module(binding,file_module,is_package);name=binding['imported']
            row.update(evidence='import_bound_alias' if binding['alias'] else 'import_bound',
                ambiguous=not bool(module),binding={'module':module,'name':name,'local':chain[0]},
                matches_target=(module==target_module and name==target_name) if target_module else name==target_name)
        elif binding and binding['kind']=='definition' and binding['qualname']==target_name:
            row.update(evidence='same_module_name',ambiguous=False,matches_target=file_module==target_module if target_module else None,binding={'module':file_module,'name':binding['qualname']})
        else:
            row.update(evidence='name_match_other_scope',shadowed=shadowed)
    elif chain and binding and binding['kind']=='module' and (
            len(chain)==2 or '.'.join([binding['module'],*chain[1:-1]])==binding['imported_module']):
        module='.'.join([binding['module'],*chain[1:-1]])
        row.update(evidence='module_attribute',ambiguous=False,receiver='.'.join(chain[:-1]),
            binding={'module':module,'local':chain[0]},matches_target=(module==target_module and chain[-1]==target_name) if target_module else chain[-1]==short)
    else:
        row.update(evidence='attribute_receiver_unknown',receiver='.'.join(chain[:-1]) if chain else '<expression>',shadowed=shadowed)
    return row
