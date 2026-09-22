#ifndef AppVersion
  #define AppVersion "0.1.12"
#endif

#ifndef SourceDir
  #define SourceDir "dist\Presspeech"
#endif

#ifndef InstallerOutputDir
  #define InstallerOutputDir "dist\installer"
#endif

[Setup]
AppId={{33F4F983-1C0C-4E1C-9706-C4B693043E81}
AppName=Presspeech
AppVersion={#AppVersion}
AppVerName=Presspeech {#AppVersion}
AppPublisher=rcourtman
AppPublisherURL=https://github.com/rcourtman/presspeech
AppSupportURL=https://github.com/rcourtman/presspeech/issues
AppUpdatesURL=https://github.com/rcourtman/presspeech/releases
DefaultDirName={localappdata}\Programs\Presspeech
DefaultGroupName=Presspeech
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; Keep Setup's admission checks aligned with the documented support boundary.
; Inno otherwise defaults to Windows 7 SP1, and x64compatible also accepts
; Arm64 through emulation even though the packaged runtime is qualified only
; on native x64 Windows 10 and 11.
MinVersion=10.0
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputDir={#InstallerOutputDir}
OutputBaseFilename=Presspeech-Setup-{#AppVersion}-x64
SetupIconFile=assets\presspeech.ico
UninstallDisplayIcon={app}\Presspeech.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\PresspeechSingleInstance
LicenseFile=..\LICENSE
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=rcourtman
VersionInfoDescription=Presspeech local push-to-talk dictation
VersionInfoProductName=Presspeech
VersionInfoProductVersion={#AppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Registry]
; Presspeech creates this value only after the user confirms the choice in
; Setup or Settings. Do not enable startup during installation, but register
; ownership so a later uninstall cannot leave a dead login entry behind.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Presspeech"; Flags: dontcreatekey uninsdeletevalue

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Presspeech"; Filename: "{app}\Presspeech.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Presspeech"; Filename: "{app}\Presspeech.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Presspeech.exe"; Description: "Launch Presspeech"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
