; Установщик Praxis (окно к своему серверу) для Windows — 1.1.0, 26.09.
;
; У варианта Praxis мастера нет: поставка — exe окна, статика, заготовка helene.json,
; документ подключения. Раньше «распакуйте архив в любую папку» — по слову Егора
; это не программа. Теперь обычный установщик: для меня (%LocalAppData%\Programs\Praxis)
; или для всех (Program Files\Praxis, права администратора), ярлыки в «Пуске» и на
; Рабочем столе, запись в «Параметры → Приложения» с удалением оттуда. Поверх
; стоящего — обновление: helene.json (адрес сервера и ключ канала) не перезаписывается.
;
;   makensis /INPUTCHARSET UTF8 /DVERSION=1.1.0 /DPAYLOAD=<папка поставки> /DOUTFILE=<итог> /DICON=<ico> praxis-setup.nsi
;
; Тихо: /S [/D=<папка>] [/AllUsers | /CurrentUser]; удаление тихо: uninstall.exe /S —
; helene.json остаётся.

Unicode true
!define PRODUCT "Praxis"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT}"

!ifndef VERSION
  !error "VERSION не задана: makensis /DVERSION=<версия> …"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD не задан: папка поставки (installer/build/Praxis)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Praxis-${VERSION}-setup.exe"
!endif

!define MULTIUSER_EXECUTIONLEVEL Highest
!define MULTIUSER_MUI
!define MULTIUSER_INSTALLMODE_COMMANDLINE
!define MULTIUSER_USE_PROGRAMFILES64
!define MULTIUSER_INSTALLMODE_INSTDIR "${PRODUCT}"
!define MULTIUSER_INSTALLMODE_INSTDIR_REGISTRY_KEY "${UNINST_KEY}"
!define MULTIUSER_INSTALLMODE_INSTDIR_REGISTRY_VALUENAME "InstallLocation"
!define MULTIUSER_INSTALLMODE_DEFAULT_CURRENTUSER
!include "MultiUser.nsh"
!include "MUI2.nsh"
!include "LogicLib.nsh"

Name "${PRODUCT} ${VERSION}"
OutFile "${OUTFILE}"
BrandingText "${PRODUCT} ${VERSION}"
SetCompressor /SOLID lzma

!ifdef ICON
  !define MUI_ICON "${ICON}"
  !define MUI_UNICON "${ICON}"
!endif
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "${PRODUCT} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Окно к агенту на своём сервере.$\r$\n$\r$\nУстановщик положит программу, создаст ярлыки и запись в «Приложениях». Адрес сервера и ключ канала спрашиваются при первом запуске; при обновлении поверх они сохраняются."
!define MULTIUSER_INSTALLMODEPAGE_TEXT_TOP "Для кого поставить ${PRODUCT}?"
!define MULTIUSER_INSTALLMODEPAGE_TEXT_ALLUSERS "Для всех пользователей этого компьютера (в Program Files; Windows спросит права администратора)"
!define MULTIUSER_INSTALLMODEPAGE_TEXT_CURRENTUSER "Только для меня (в моей папке программ, без прав администратора)"
!define MUI_FINISHPAGE_RUN "$INSTDIR\praxis.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Открыть ${PRODUCT}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MULTIUSER_PAGE_INSTALLMODE
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1049 "ProductName" "${PRODUCT}"
VIAddVersionKey /LANG=1049 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileDescription" "Установщик ${PRODUCT}"
VIAddVersionKey /LANG=1049 "LegalCopyright" "${PRODUCT}"

Function .onInit
  !insertmacro MULTIUSER_INIT
FunctionEnd

Function un.onInit
  !insertmacro MULTIUSER_UNINIT
FunctionEnd

Section "-install"
  SetDetailsPrint textonly
  DetailPrint "Закрываю открытое окно ${PRODUCT}, если оно есть…"
  SetDetailsPrint none
  nsExec::ExecToLog 'taskkill /IM praxis.exe /F'
  Pop $0
  Sleep 800
  SetDetailsPrint textonly
  DetailPrint "Копирую файлы программы…"
  SetDetailsPrint none
  SetOutPath "$INSTDIR"
  ; Настройки владельца (адрес сервера, ключ канала) — не перезаписывать: заготовка
  ; из поставки кладётся только там, где своего helene.json ещё нет.
  File /r /x "helene.json" "${PAYLOAD}\*.*"
  IfFileExists "$INSTDIR\helene.json" +2 0
    File "/oname=helene.json" "${PAYLOAD}\helene.json"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Для всех: окно пишет helene.json и helene.log рядом с собой — права на папку
  ; пользователям этого компьютера.
  ${If} $MultiUser.InstallMode == "AllUsers"
    nsExec::ExecToLog 'icacls "$INSTDIR" /grant *S-1-5-32-545:(OI)(CI)M /Q'
    Pop $0
  ${EndIf}

  SetDetailsPrint textonly
  DetailPrint "Ярлыки и запись в «Приложениях»…"
  SetDetailsPrint none
  CreateShortcut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\praxis.exe" "" "$INSTDIR\praxis.ico"
  CreateShortcut "$DESKTOP\${PRODUCT}.lnk" "$INSTDIR\praxis.exe" "" "$INSTDIR\praxis.ico"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr SHCTX "${UNINST_KEY}" "Publisher" "${PRODUCT}"
  WriteRegStr SHCTX "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\praxis.ico"
  WriteRegStr SHCTX "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr SHCTX "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD SHCTX "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD SHCTX "${UNINST_KEY}" "NoRepair" 1
  ; Размер — из поставки при сборке (/DSIZE_KB), а не обходом папки: обход считал и data/
  ; агента (десятки тысяч файлов) и держал экран «копирование» минуту после копирования.
  !ifdef SIZE_KB
    WriteRegDWORD SHCTX "${UNINST_KEY}" "EstimatedSize" ${SIZE_KB}
  !endif
  SetDetailsPrint textonly
  DetailPrint "Готово."
SectionEnd

Section "Uninstall"
  nsExec::ExecToLog 'taskkill /IM praxis.exe /F'
  Pop $0
  Sleep 800
  ; Настройки — только по явному «да»; тихое снятие их оставляет.
  StrCpy $1 "keep"
  IfSilent +3 0
  MessageBox MB_YESNO|MB_ICONQUESTION "Удалить и настройки — helene.json с адресом сервера и ключом канала?$\r$\n«Нет» оставит файл в папке." IDNO +2
    StrCpy $1 "purge"
  Delete "$INSTDIR\praxis.exe"
  Delete "$INSTDIR\praxis.ico"
  Delete "$INSTDIR\helene-build.json"
  Delete "$INSTDIR\helene.log"
  Delete "$INSTDIR\NOTICE"
  Delete "$INSTDIR\*.md"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\licenses"
  StrCmp $1 "purge" 0 +3
    Delete "$INSTDIR\helene.json"
    RMDir /r "$INSTDIR"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  Delete "$DESKTOP\${PRODUCT}.lnk"
  DeleteRegKey SHCTX "${UNINST_KEY}"
SectionEnd
