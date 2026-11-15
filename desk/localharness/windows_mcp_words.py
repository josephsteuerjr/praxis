"""Owned MCP errors carry codes so model and interface choose independently."""
from . import ui_language


def text(root, code, **values):
    return ui_language.text('native.mcp.'+code, ui_language.for_root(root), values)


def configured(config, code, **values):
    language=ui_language.interface_language(config,ui_language.system_tags())
    return ui_language.text('native.mcp.'+code,language,values)


def render(root, spec):
    result=text(root,spec['code'],**spec.get('values',{}))
    if spec.get('cause'):result+='\n'+render(root,spec['cause'])
    return result+('\n'+spec['detail'] if spec.get('detail') else '')


def failure(root, code, *, detail='', cause=None, in_doubt=False, **values):
    spec={'code':code,'values':values,'detail':str(detail)}
    if cause:spec['cause']=cause
    result={'isError':True,'content':[{'type':'text','text':render(root,spec)}],
            'helene_error':spec}
    if in_doubt:result['in_doubt']=True
    return result


class Error(RuntimeError):
    def __init__(self,root,code,*,detail='',cause=None,**values):
        self.spec={'code':code,'values':values,'detail':str(detail)}
        if cause:self.spec['cause']=cause
        super().__init__(render(root,self.spec))
