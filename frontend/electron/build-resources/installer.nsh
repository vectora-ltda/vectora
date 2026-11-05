; Customizações do instalador NSIS.
;
; ManifestDPIAware true: marca o stub como DPI-aware — sem isso o Windows
; escala a janela em telas HiDPI e o texto fica borrado.
;
; customInstall: cria vectora.cmd wrapper + adiciona $INSTDIR ao PATH do usuário.
; customUnInstall: remove vectora.cmd.

!macro customHeader
  ManifestDPIAware true
!macroend

!macro customInstall
  ; Encerra qualquer instância rodando antes de instalar (cenário de atualização).
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM "Vectora.exe"'
  Pop $0
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM "vectora.exe"'
  Pop $0
  Sleep 500

  ; Cria vectora.cmd no diretório de instalação para que `vectora` funcione
  ; diretamente em qualquer terminal após a instalação.
  FileOpen $0 "$INSTDIR\vectora.cmd" w
  FileWrite $0 "@echo off$\r$\n"
  FileWrite $0 '"$INSTDIR\resources\vectora-core\vectora.exe" %*$\r$\n'
  FileClose $0

  ; Adiciona $INSTDIR ao PATH do usuário (HKCU).
  ; Em terminais já abertos o novo PATH só vige numa sessão nova.
  ReadRegStr $1 HKCU "Environment" "Path"
  StrCmp $1 "" path_empty path_not_empty
  path_empty:
    WriteRegStr HKCU "Environment" "Path" "$INSTDIR"
    Goto path_done
  path_not_empty:
    WriteRegStr HKCU "Environment" "Path" "$1;$INSTDIR"
  path_done:
  SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

!macro customUnInstall
  ; Encerra Vectora completamente antes de remover os arquivos.
  ; Sem isso o Windows bloqueia deleção de arquivos abertos pelo processo.
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM "Vectora.exe"'
  Pop $0
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM "vectora.exe"'
  Pop $0
  Sleep 1000
  Delete "$INSTDIR\vectora.cmd"
!macroend
