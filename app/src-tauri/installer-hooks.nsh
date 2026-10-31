; Branch Scrapper NSIS installer hooks (bundle.windows.nsis.installerHooks in tauri.conf.json).
;
; v0.1.0 shipped as "Ghost Scraper" (identifier com.ghostscraper.desktop) and registered its own per-user
; uninstall entry. Without this hook, installing Branch Scrapper would leave BOTH apps installed side by side.
; NSIS_HOOK_PREINSTALL removes the old app silently first. It only removes the program files, shortcuts and
; registry entries: the old uninstaller keeps %APPDATA%\GhostScraper, which Branch Scrapper copies to
; %APPDATA%\BranchScrapper on its first start (ghscraper.config.migrate_legacy_data_dir).
;
; The uninstaller is run in place (`_?=<dir>`, like the stock reinstall logic in installer.nsi) because that is
; the only way ExecWait can wait for it; in that mode it cannot delete itself, so we do that afterwards.

!macro NSIS_HOOK_PREINSTALL
  Push $R0
  Push $R1
  Push $R2

  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ghost Scraper" "UninstallString"
  ReadRegStr $R1 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ghost Scraper" "InstallLocation"
  ${If} $R0 != ""
  ${AndIf} $R1 != ""
    ; InstallLocation is stored in quotes; the path given to _?= must not be quoted
    StrCpy $R2 $R1 1
    ${If} $R2 == '"'
      StrCpy $R1 $R1 "" 1
      StrCpy $R1 $R1 -1
    ${EndIf}
    ${If} ${FileExists} "$R1\uninstall.exe"
      DetailPrint "Removing the previous Ghost Scraper installation (your data is kept)..."
      ClearErrors
      ExecWait '$R0 /S _?=$R1' $R2
      ${IfThen} ${Errors} ${|} StrCpy $R2 2 ${|}
      ${If} $R2 == 0
        Delete "$R1\uninstall.exe"
        RMDir "$R1"
        ; The old uninstaller only clears its "install dir" registry key when the delete-app-data box is ticked
        ; (never, in silent mode). It holds nothing but the install path.
        DeleteRegKey HKCU "Software\ghostscraper\Ghost Scraper"
        DeleteRegKey /ifempty HKCU "Software\ghostscraper"
      ${Else}
        DetailPrint "The previous Ghost Scraper installation could not be removed automatically (exit code $R2)."
      ${EndIf}
    ${EndIf}
  ${EndIf}

  Pop $R2
  Pop $R1
  Pop $R0
!macroend
