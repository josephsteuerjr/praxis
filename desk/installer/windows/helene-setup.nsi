; Установщик Hélène для Windows — один файл вместо архива с двумя exe (1.1.0, 26.09).
;
; Что он делает: распаковывает поставку во временную папку и открывает тот же мастер
; установки (helene-setup.exe), что раньше запускали из распакованного архива. Мастер
; спрашивает имя агента, конституцию, модель, режим и службу, копирует файлы в
; %LocalAppData%\Programs\Helene, пишет запись в «Приложения» Windows (удаление —
; там же) и ярлыки. Поверх уже стоящей Hélène тот же мастер идёт как обновление.
;
; Собирается из installer/build_dist.py (шаг «setup exe») тем NSIS, который лежит у
; Tauri (%LocalAppData%\tauri\NSIS) или в PATH:
;
;   makensis /DVERSION=1.1.0 /DPAYLOAD=<папка поставки> /DOUTFILE=<итог> /DICON=<ico> helene-setup.nsi
;
; Тихий запуск (/S): мастер зовётся с --update — обновление поверх стоящей установки
; без вопросов; без установки он сам откроет обычный визард.

Unicode true
!include "MUI2.nsh"

!ifndef VERSION
  !error "VERSION не задана: makensis /DVERSION=<версия> …"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD не задан: папка поставки (installer/build/Helene)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Helene-${VERSION}-setup.exe"
!endif

Name "Hélène ${VERSION}"
OutFile "${OUTFILE}"
BrandingText "Hélène ${VERSION}"
Caption "Установка Hélène"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ShowInstDetails nevershow
; Папка распаковки — своя на версию во временной папке владельца; мастер копирует
; отсюда в место установки, а хвост ниже убирает её за собой.
InstallDir "$TEMP\Helene-${VERSION}-setup"

!ifdef ICON
  !define MUI_ICON "${ICON}"
!endif
!define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "Мастер открыт"
!define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT "Дальше — в окне мастера установки."
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1049 "ProductName" "Hélène"
VIAddVersionKey /LANG=1049 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileDescription" "Установщик Hélène"
VIAddVersionKey /LANG=1049 "LegalCopyright" "Hélène"

Section "-unpack"
  SetDetailsPrint textonly
  DetailPrint "Распаковываю Hélène ${VERSION}…"
  SetDetailsPrint none
  RMDir /r "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}\*.*"
  SetDetailsPrint textonly
  DetailPrint "Открываю мастер установки…"
  HideWindow
  IfSilent silent
  ExecWait '"$INSTDIR\helene-setup.exe"'
  Goto done
silent:
  ExecWait '"$INSTDIR\helene-setup.exe" --update'
done:
  ; Папка распаковки больше не нужна: установленное лежит там, куда положил мастер.
  ; Свой exe мастер отпускает при выходе; если папку не отдали — уберётся при
  ; следующей установке той же версии (RMDir выше).
  RMDir /r "$INSTDIR"
  Quit
SectionEnd
