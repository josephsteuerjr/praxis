/* Keep Python inside the signed .app process so Cocoa/TCC see its identity. */
#include <CoreFoundation/CoreFoundation.h>
#include <stdbool.h>
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
#ifndef HELENE_NO_ALERT  /* test builds on headless runners only */
    CFStringRef text=CFStringCreateWithCString(NULL,message,kCFStringEncodingUTF8);
    CFUserNotificationDisplayAlert(0,kCFUserNotificationStopAlertLevel,NULL,NULL,NULL,
        CFSTR("Hélène не удалось открыть"),text,CFSTR("Закрыть"),NULL,NULL,NULL);
    if(text)CFRelease(text);
#endif
    return 1;
}
/* Read the OS mapping; never remove quarantine, search other installations or
   relaunch the app. Resolve optionally so older Security versions still start
   ordinary, untranslocated bundles. The signed Cocoa process stays the owner. */
static int original_path(const char *path, char *out, size_t size) {
    void *security=dlopen("/System/Library/Frameworks/Security.framework/Security",RTLD_LAZY|RTLD_LOCAL);
    if(!security)return 0;
    CFURLRef (*original)(CFURLRef,CFErrorRef*)=dlsym(security,"SecTranslocateCreateOriginalPathForURL");
    if(!original){dlclose(security);return 0;}
    CFURLRef url=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8*)path,strlen(path),false);
    CFErrorRef error=NULL;CFURLRef source=url?original(url,&error):NULL;
    int ok=source && CFURLGetFileSystemRepresentation(source,true,(UInt8*)out,size);
    if(source)CFRelease(source);if(url)CFRelease(url);if(error)CFRelease(error);
    dlclose(security);return ok;
}
static int same_file_bytes(const char *left,const char *right) {
    FILE *a=fopen(left,"rb"),*b=fopen(right,"rb");int equal=a && b;
    if(equal){unsigned char x[8192],y[8192];size_t n,m;
        do {n=fread(x,1,sizeof(x),a);m=fread(y,1,sizeof(y),b);
            if(n!=m || memcmp(x,y,n)!=0){equal=0;break;}
        } while(n);
        if(ferror(a)||ferror(b))equal=0;
    }
    if(a)fclose(a);if(b)fclose(b);return equal;
}
int main(int argc,char **argv){
    char executable[PATH_MAX],folder[PATH_MAX],root[PATH_MAX];uint32_t length=sizeof(executable);
    if(_NSGetExecutablePath(executable,&length)!=0 || !realpath(executable,folder))return fail("Не удалось определить папку приложения.");
    char original[PATH_MAX],canonical[PATH_MAX];
    if(original_path(folder,original,sizeof(original)) && realpath(original,canonical) && strcmp(folder,canonical)!=0){
        if(!same_file_bytes(folder,canonical))return fail("Файлы Hélène изменились после открытия. Закрой приложение и открой его снова из полной папки Helene.");
        memcpy(folder,canonical,strlen(canonical)+1);
    }
    char *slash=strrchr(folder,'/');if(!slash)return fail("Некорректный путь приложения.");*slash=0;
    char parent[PATH_MAX];if(snprintf(parent,sizeof(parent),"%s/../../..",folder)>=(int)sizeof(parent) || !realpath(parent,root))return fail("Папка Hélène недоступна.");
    char library[PATH_MAX],python[PATH_MAX],entry[PATH_MAX],runtime[PATH_MAX];
    snprintf(library,sizeof(library),"%s/runtime/lib/libpython3.14.dylib",root);
    snprintf(python,sizeof(python),"%s/runtime/bin/python3",root);
    snprintf(entry,sizeof(entry),"%s/app/" HELENE_ENTRY,root);
    snprintf(runtime,sizeof(runtime),"%s/runtime",root);
    if(access(entry,R_OK)!=0){
        /* App Translocation: macOS ran a read-only copy of the bundle alone. The original
           folder was resolved above when the OS could map it; this is the fallback. */
        if(strstr(folder,"/AppTranslocation/"))
            return fail("macOS открыл Hélène из временной копии без соседних папок runtime и app. Перемести всю папку Helene из «Загрузок» (например, в свою папку пользователя) и открой Helene.app оттуда.");
        return fail("Не найден код программы рядом с Hélène. Распакуй всю папку Helene вместе с runtime и app. Если они уже рядом, перемести полную папку из Загрузок и открой Start Helene.command.");
    }
    setenv("PYTHONHOME",runtime,1);unsetenv("PYTHONPATH");
    void *handle=dlopen(library,RTLD_NOW|RTLD_GLOBAL);if(!handle)return fail(dlerror());
    int (*run)(int,char **)=dlsym(handle,"Py_BytesMain");if(!run)return fail("В комплекте нет совместимого Python.");
    char *code="import os,runpy,sys;os.environ.pop('PYTHONHOME',None);entry=sys.argv.pop(1);sys.path.insert(0,os.path.dirname(entry));runpy.run_path(entry,run_name='__main__')";
    char **args=calloc((size_t)argc+8,sizeof(char*));if(!args)return fail("Недостаточно памяти.");
    int n=0;args[n++]=python;args[n++]="-s";args[n++]="-c";args[n++]=code;args[n++]=entry;args[n++]="--root";args[n++]=root;
    for(int i=1;i<argc;i++)if(strncmp(argv[i],"-psn_",5)!=0)args[n++]=argv[i];
    int result=run(n,args);free(args);return result;
}
