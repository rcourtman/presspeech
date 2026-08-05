#ifndef AppVersion
  #define AppVersion "0.1.1"
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
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
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

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Presspeech"; Filename: "{app}\Presspeech.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Presspeech"; Filename: "{app}\Presspeech.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Presspeech.exe"; Description: "Launch Presspeech"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
