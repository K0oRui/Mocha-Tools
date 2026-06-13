; ─────────────────────────────────────────────────────────────────────────────
; Mocha Tools — NSIS Installer Script
;
; Requires NSIS 3.x (https://nsis.sourceforge.io)
;
; Build with:
;   makensis installer.nsi
;
; Produces:
;   MochaTools-Setup-<version>.exe
;
; This installer packages dist\Mocha Tools.exe (built by PyInstaller).
; ─────────────────────────────────────────────────────────────────────────────

!define APP_NAME      "Mocha Tools"
!define APP_EXE       "Mocha Tools.exe"
!define PUBLISHER     "nxllxvxxd2"
!define REGKEY        "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define INSTDIR_REG   "Software\${PUBLISHER}\${APP_NAME}"

; APP_VERSION can be overridden at build time:
;   makensis /DAPP_VERSION=3.0.1 installer.nsi
!ifndef APP_VERSION
  !define APP_VERSION "1.0.0"
!endif

; Output filename
OutFile "MochaTools-Setup-${APP_VERSION}.exe"

; Default installation directory
InstallDir "$PROGRAMFILES64\${APP_NAME}"

; Registry key to store install path (used by uninstaller)
InstallDirRegKey HKLM "${INSTDIR_REG}" "InstallDir"

; Require admin rights (writes to Program Files + registry)
RequestExecutionLevel admin

; Modern UI
!include "MUI2.nsh"
!include "LogicLib.nsh"

; ── UI Pages ─────────────────────────────────────────────────────────────────
!define MUI_ABORTWARNING
!define MUI_ICON    "builditems\windows\icon.ico"
!define MUI_UNICON  "builditems\windows\icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_RUN          "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT     "Launch ${APP_NAME}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED  ; unchecked by default — remove this line to default to checked
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ── Metadata ─────────────────────────────────────────────────────────────────
Name          "${APP_NAME}"
BrandingText  "${APP_NAME} ${APP_VERSION}"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "FileVersion"     "${APP_VERSION}.0"
VIAddVersionKey "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey "LegalCopyright"  "© ${PUBLISHER}"

; ── Installer ─────────────────────────────────────────────────────────────────
Section "Install" SecInstall
  SectionIn RO  ; mandatory

  SetOutPath "$INSTDIR"

  ; Main executable — comes from PyInstaller's dist\ folder
  ; FIX: single-quote the /oname flag so the space in "Mocha Tools.exe" is handled correctly
  File '/oname=${APP_EXE}' "dist\Mocha Tools.exe"

  ; Optional: icon file for shortcuts
  File "builditems\windows\icon.ico"

  ; Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
                  "$INSTDIR\${APP_EXE}" "" \
                  "$INSTDIR\icon.ico" 0

  ; Desktop shortcut
  CreateShortcut  "$DESKTOP\${APP_NAME}.lnk" \
                  "$INSTDIR\${APP_EXE}" "" \
                  "$INSTDIR\icon.ico" 0

  ; Write registry: install path + uninstall info
  WriteRegStr HKLM "${INSTDIR_REG}" "InstallDir" "$INSTDIR"

  WriteRegStr   HKLM "${REGKEY}" "DisplayName"          "${APP_NAME}"
  WriteRegStr   HKLM "${REGKEY}" "DisplayVersion"       "${APP_VERSION}"
  WriteRegStr   HKLM "${REGKEY}" "Publisher"            "${PUBLISHER}"
  WriteRegStr   HKLM "${REGKEY}" "InstallLocation"      "$INSTDIR"
  WriteRegStr   HKLM "${REGKEY}" "UninstallString"      "$INSTDIR\Uninstall.exe"
  WriteRegStr   HKLM "${REGKEY}" "DisplayIcon"          "$INSTDIR\icon.ico"
  WriteRegDWORD HKLM "${REGKEY}" "NoModify"             1
  WriteRegDWORD HKLM "${REGKEY}" "NoRepair"             1

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

; ── Uninstaller ───────────────────────────────────────────────────────────────
Section "Uninstall"
  ; Kill the process if running
  ExecWait 'taskkill /F /IM "${APP_EXE}"' $0

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\icon.ico"
  Delete "$INSTDIR\Uninstall.exe"

  ; Remove shortcuts
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  ; Remove install directory (only if empty after deletes above)
  RMDir "$INSTDIR"

  ; Remove registry entries
  DeleteRegKey HKLM "${REGKEY}"
  DeleteRegKey HKLM "${INSTDIR_REG}"
SectionEnd
