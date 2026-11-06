/* Keep Python inside the signed .app process so Cocoa/TCC see its identity. */
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifndef HELENE_ENTRY
#define HELENE_ENTRY "python_app.py"
#endif
static int fail(const char *message) {
    fprintf(stderr,"Helene: %s\n",message);
    CFStringRef text=CFStringCreateWithCString(NULL,message,kCFStringEncodingUTF8);
    CFUserNotificationDisplayAlert(0,kCFUserNotificationStopAlertLevel,NULL,NULL,NULL,
        CFSTR("Hélène не удалось открыть"),text,CFSTR("Закрыть"),NULL,NULL,NULL);
    if(text)CFRelease(text);return 1;
}
int main(int argc,char **argv){
    char executable[PATH_MAX],folder[PATH_MAX],root[PATH_MAX];uint32_t length=sizeof(executable);
    if(_NSGetExecutablePath(executable,&length)!=0 || !realpath(executable,folder))return fail("Не удалось определить папку приложения.");
    char *slash=strrchr(folder,'/');if(!slash)return fail("Некорректный путь приложения.");*slash=0;
    char parent[PATH_MAX];if(snprintf(parent,sizeof(parent),"%s/../../..",folder)>=(int)sizeof(parent) || !realpath(parent,root))return fail("Папка Hélène недоступна.");
    char library[PATH_MAX],python[PATH_MAX],entry[PATH_MAX],runtime[PATH_MAX];
    snprintf(library,sizeof(library),"%s/runtime/lib/libpython3.14.dylib",root);
    snprintf(python,sizeof(python),"%s/runtime/bin/python3",root);
    snprintf(entry,sizeof(entry),"%s/app/" HELENE_ENTRY,root);
    snprintf(runtime,sizeof(runtime),"%s/runtime",root);
    if(access(entry,R_OK)!=0)return fail("Не найден код программы. Распакуй всю папку Helene вместе с runtime и app.");
    setenv("PYTHONHOME",runtime,1);unsetenv("PYTHONPATH");
    void *handle=dlopen(library,RTLD_NOW|RTLD_GLOBAL);if(!handle)return fail(dlerror());
    int (*run)(int,char **)=dlsym(handle,"Py_BytesMain");if(!run)return fail("В комплекте нет совместимого Python.");
    char *code="import os,runpy,sys;os.environ.pop('PYTHONHOME',None);entry=sys.argv.pop(1);sys.path.insert(0,os.path.dirname(entry));runpy.run_path(entry,run_name='__main__')";
    char **args=calloc((size_t)argc+8,sizeof(char*));if(!args)return fail("Недостаточно памяти.");
    int n=0;args[n++]=python;args[n++]="-s";args[n++]="-c";args[n++]=code;args[n++]=entry;args[n++]="--root";args[n++]=root;
    for(int i=1;i<argc;i++)if(strncmp(argv[i],"-psn_",5)!=0)args[n++]=argv[i];
    int result=run(n,args);free(args);return result;
}
