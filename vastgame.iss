; vastgame.iss — установщик VastGame для Windows (Inno Setup 6). Собирает build-win.ps1 после PyInstaller:
;   ISCC.exe /DAppVersion=0.4.7 vastgame.iss   →   dist\VastGame-Setup-0.4.7.exe
;
; Для одного пользователя, без прав администратора: %LOCALAPPDATA%\Programs\VastGame, ярлык в «Пуске» с именем
; приложения для Windows VastGame.App (по нему уведомления подписаны «VastGame», notify.WIN_APP_ID).
; Тихое обновление (/VERYSILENT) запускает сторож программы (updater.WATCH_WIN) после её выхода; он же открывает
; VastGame после установки. Удаление («Параметры → Приложения») убирает и автозапуск; настройки и историю
; (%APPDATA%\vastgame) оставляет.

#ifndef AppVersion
  #error Передай версию: ISCC /DAppVersion=X.Y.Z vastgame.iss
#endif

[Setup]
AppId={{1FC0B0A6-7F3C-47B2-936A-C5453A694969}
AppName=VastGame
AppVersion={#AppVersion}
AppVerName=VastGame {#AppVersion}
AppPublisher=WetFoxx
AppPublisherURL=https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-
AppSupportURL=https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/issues
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\VastGame
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=VastGame-Setup-{#AppVersion}
SetupIconFile=assets\vastgame.ico
UninstallDisplayIcon={app}\VastGame.exe
UninstallDisplayName=VastGame
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[InstallDelete]
; файлы прежней версии — чтобы при обновлении не оставались лишние
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\VastGame\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\VastGame"; Filename: "{app}\VastGame.exe"; AppUserModelID: "VastGame.App"

[Registry]
; «Открывать при входе» (autostart.py) — убрать при удалении; сама установка эту запись не создаёт
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "VastGame"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\VastGame.exe"; Description: "{cm:LaunchProgram,VastGame}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; «Продолжать после перезагрузки» (autostart.py) — задача Планировщика
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""VastGame Resume"" /F"; Flags: runhidden; RunOnceId: "DelResumeTask"
