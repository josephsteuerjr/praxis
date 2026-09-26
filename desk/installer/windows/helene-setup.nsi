; Установщик Hélène для Windows — нормальная программа (1.1.0, 26.09).
;
; Установщик кладёт файлы сам: папка (для меня — %LocalAppData%\Programs\Helene, для
; всех — Program Files\Helene), ярлыки в «Пуске» и на Рабочем столе, запись в
; «Параметры → Приложения» с uninstall.exe. Мастер (helene-setup.exe) после этого
; только настраивает: имя агента, конституция, модель, режим, служба — и пишет
; helene.json и data/. Поверх стоящей Hélène — обновление: старая копия мастера
; гасит окно и службу (--stop), файлы подменяются, helene.json и data/ не трогаются,
; мастер сливает настройки (--configure) и открывает Hélène.
;
;   makensis /INPUTCHARSET UTF8 /DVERSION=1.1.0 /DPAYLOAD=<папка поставки> /DOUTFILE=<итог> /DICON=<ico> helene-setup.nsi
;
; Тихо: /S [/D=<папка>] [/AllUsers | /CurrentUser]; снятие тихо: uninstall.exe /S — data/ и
; helene.json остаются.

Unicode true
!define PRODUCT "Helene"
!define PRODUCT_UI "Hélène"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT}"

!ifndef VERSION
  !error "VERSION не задана: makensis /DVERSION=<версия> …"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD не задан: папка поставки (installer/build/Helene)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Helene-${VERSION}-setup.exe"
!endif

; Для меня / для всех: страница выбора, права по выбору (UAC — только «для всех»).
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
!include "FileFunc.nsh"
!include "LogicLib.nsh"

Name "${PRODUCT_UI} ${VERSION}"
OutFile "${OUTFILE}"
BrandingText "${PRODUCT_UI} ${VERSION}"
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!ifdef ICON
  !define MUI_ICON "${ICON}"
  !define MUI_UNICON "${ICON}"
!endif
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "${PRODUCT_UI} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Личный агент на этом компьютере.$\r$\n$\r$\nУстановщик положит программу, создаст ярлыки и запись в «Приложениях». Имя агента, конституцию и модель спросит мастер настройки после установки; поверх уже стоящей ${PRODUCT_UI} это обновление — память, конституция и настройки останутся."
!define MULTIUSER_INSTALLMODEPAGE_TEXT_TOP "Для кого поставить ${PRODUCT_UI}?"
!define MULTIUSER_INSTALLMODEPAGE_TEXT_ALLUSERS "Для всех пользователей этого компьютера (в Program Files; Windows спросит права администратора)"
!define MULTIUSER_INSTALLMODEPAGE_TEXT_CURRENTUSER "Только для меня (в моей папке программ, без прав администратора)"
!define MUI_FINISHPAGE_RUN "$INSTDIR\helene-setup.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--configure"
!define MUI_FINISHPAGE_RUN_TEXT "Открыть мастер настройки ${PRODUCT_UI}"
!define MUI_FINISHPAGE_TEXT "Файлы на месте. Дальше — мастер настройки: имя агента, конституция, модель. Поверх прежней установки он просто сольёт настройки и откроет ${PRODUCT_UI}."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MULTIUSER_PAGE_INSTALLMODE
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1049 "ProductName" "${PRODUCT_UI}"
VIAddVersionKey /LANG=1049 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileDescription" "Установщик ${PRODUCT_UI}"
VIAddVersionKey /LANG=1049 "LegalCopyright" "${PRODUCT_UI}"

Function .onInit
  !insertmacro MULTIUSER_INIT
FunctionEnd

Function un.onInit
  !insertmacro MULTIUSER_UNINIT
FunctionEnd

Section "-install"
  ; Поверх стоящей: старая копия мастера снимает службу и гасит окно — иначе файлы
  ; заняты, а служба под LocalSystem пережила бы подмену своего exe.
  IfFileExists "$INSTDIR\helene-setup.exe" 0 fresh
    SetDetailsPrint textonly
    DetailPrint "Останавливаю прежнюю ${PRODUCT_UI}…"
    SetDetailsPrint none
    ExecWait '"$INSTDIR\helene-setup.exe" --stop --quiet' $0
    ${If} $0 != 0
      MessageBox MB_OK|MB_ICONSTOP "Не удалось остановить прежнюю ${PRODUCT_UI}: закрой её окно (полностью, включая значок у часов) и повтори установку. Подробности — $INSTDIR\stop.log"
      Abort
    ${EndIf}
fresh:
  SetDetailsPrint textonly
  DetailPrint "Копирую файлы программы…"
  SetDetailsPrint none
  SetOutPath "$INSTDIR"
  ; helene.json (настройки, ключи) и data/ (память агента) поверх не перезаписываются
  ; никогда; заготовка helene.json — только там, где своей ещё нет.
  File /r /x "helene.json" /x "data" "${PAYLOAD}\*.*"
  IfFileExists "$INSTDIR\helene.json" +2 0
    File "/oname=helene.json" "${PAYLOAD}\helene.json"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Для всех: программа в Program Files, а память, настройки и код агента должны
  ; писаться из-под обычной учётной записи. Права на папку — пользователям; при
  ; установленной службе она сама сужает app\ и runtime\ до чтения при каждом старте.
  ${If} $MultiUser.InstallMode == "AllUsers"
    SetDetailsPrint textonly
    DetailPrint "Права на папку — пользователям этого компьютера…"
    SetDetailsPrint none
    nsExec::ExecToLog 'icacls "$INSTDIR" /grant *S-1-5-32-545:(OI)(CI)M /Q'
    Pop $0
  ${EndIf}

  SetDetailsPrint textonly
  DetailPrint "Ярлыки и запись в «Приложениях»…"
  SetDetailsPrint none
  CreateShortcut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\helene.exe" "" "$INSTDIR\helene.ico"
  CreateShortcut "$DESKTOP\${PRODUCT}.lnk" "$INSTDIR\helene.exe" "" "$INSTDIR\helene.ico"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayName" "${PRODUCT_UI}"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr SHCTX "${UNINST_KEY}" "Publisher" "${PRODUCT_UI}"
  WriteRegStr SHCTX "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr SHCTX "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\helene.ico"
  WriteRegStr SHCTX "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr SHCTX "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD SHCTX "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD SHCTX "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD SHCTX "${UNINST_KEY}" "EstimatedSize" "$0"
  SetDetailsPrint textonly
  DetailPrint "Готово."
SectionEnd

Section "Uninstall"
  ; Данные — только по явному «да»; тихое снятие их оставляет.
  StrCpy $1 ""
  IfSilent +3 0
  MessageBox MB_YESNO|MB_ICONQUESTION "Удалить и данные агента — папку data (память, дневник, конституция) и helene.json с ключами?$\r$\n«Нет» оставит их в папке." IDNO +2
    StrCpy $1 "--purge"
  ; Служба, песочница (AppContainer), процессы, запись оболочки — снимает сам мастер.
  IfFileExists "$INSTDIR\helene-setup.exe" 0 +2
    ExecWait '"$INSTDIR\helene-setup.exe" --uninstall --quiet $1' $0
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\tree"
  RMDir /r "$INSTDIR\server"
  RMDir /r "$INSTDIR\licenses"
  Delete "$INSTDIR\*.exe"
  Delete "$INSTDIR\*.md"
  Delete "$INSTDIR\*.ico"
  Delete "$INSTDIR\*.ps1"
  Delete "$INSTDIR\*.log"
  Delete "$INSTDIR\NOTICE"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\helene-build.json"
  Delete "$INSTDIR\helene-relay"
  StrCmp $1 "--purge" 0 +4
    Delete "$INSTDIR\helene.json"
    Delete "$INSTDIR\helene.json.*"
    RMDir /r "$INSTDIR"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  Delete "$DESKTOP\${PRODUCT}.lnk"
  DeleteRegKey SHCTX "${UNINST_KEY}"
SectionEnd
