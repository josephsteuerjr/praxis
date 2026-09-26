; Установщик Praxis (окно к своему серверу) для Windows — 1.1.0, 26.09.
;
; У варианта Praxis мастера нет: поставка — exe окна, статика, заготовка helene.json,
; документ подключения. Раньше «распакуйте архив в любую папку» — по слову Егора
; это не программа. Теперь обычный установщик: папка (по умолчанию
; %LocalAppData%\Programs\Praxis), ярлыки в «Пуске» и на Рабочем столе, запись в
; «Параметры → Приложения» с удалением оттуда. Поверх стоящей — обновление:
; helene.json (адрес сервера и ключ канала) не перезаписывается.
;
;   makensis /DVERSION=1.1.0 /DPAYLOAD=<папка поставки> /DOUTFILE=<итог> /DICON=<ico> praxis-setup.nsi
;
; Тихо: /S (и /D=<папка>); удаление тихо: uninstall.exe /S — helene.json остаётся.

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !error "VERSION не задана: makensis /DVERSION=<версия> …"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD не задан: папка поставки (installer/build/Praxis)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Praxis-${VERSION}-setup.exe"
!endif
!define PRODUCT "Praxis"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT}"

Name "${PRODUCT} ${VERSION}"
OutFile "${OUTFILE}"
BrandingText "${PRODUCT} ${VERSION}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
InstallDir "$LOCALAPPDATA\Programs\${PRODUCT}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"

!ifdef ICON
  !define MUI_ICON "${ICON}"
  !define MUI_UNICON "${ICON}"
!endif
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "${PRODUCT} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Окно к агенту на своём сервере.$\r$\n$\r$\nУстановщик положит программу, создаст ярлыки и запись в «Приложениях». Адрес сервера и ключ канала спрашиваются при первом запуске; при обновлении поверх они сохраняются."
!define MUI_FINISHPAGE_RUN "$INSTDIR\praxis.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Открыть ${PRODUCT}"
!insertmacro MUI_PAGE_WELCOME
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

  SetDetailsPrint textonly
  DetailPrint "Ярлыки и запись в «Приложениях»…"
  SetDetailsPrint none
  CreateShortcut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\praxis.exe" "" "$INSTDIR\praxis.ico"
  CreateShortcut "$DESKTOP\${PRODUCT}.lnk" "$INSTDIR\praxis.exe" "" "$INSTDIR\praxis.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PRODUCT}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\praxis.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"
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
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
