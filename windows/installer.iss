#ifndef AppVersion
  #define AppVersion "0.1.12"
#endif

#ifndef SourceDir
  #define SourceDir "dist\Presspeech"
#endif

#ifndef InstallerOutputDir
  #define InstallerOutputDir "dist\installer"
#endif

#ifndef BundleMetadataFile
  #error Build through build-release.ps1: verified bundle metadata is required
#endif
#include BundleMetadataFile

#ifdef Qualification
  #ifndef QualificationAppId
    #error Qualification installers require an isolated application identity
  #endif
  #define InstallerAppId QualificationAppId
  #define SetupAppId QualificationAppId
  #define InstanceMutex "Local\PresspeechQualification-" + QualificationAppId
#else
  #define InstallerAppId "{33F4F983-1C0C-4E1C-9706-C4B693043E81}"
  #define SetupAppId "{{33F4F983-1C0C-4E1C-9706-C4B693043E81}"
  #define InstanceMutex "Local\PresspeechSingleInstance"
#endif

[Setup]
AppId={#SetupAppId}
AppName=Presspeech
AppVersion={#AppVersion}
AppVerName=Presspeech {#AppVersion}
AppPublisher=rcourtman
AppPublisherURL=https://github.com/rcourtman/presspeech
; Match the app's Report a Problem action: the support guide explains safe
; recovery and current reporting routes before anyone opens a public issue.
AppSupportURL=https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md
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
AppMutex={#InstanceMutex}
SetupMutex=Local\PresspeechSetup
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
#ifdef Qualification
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "PresspeechQualification-{#QualificationAppId}"; Flags: dontcreatekey uninsdeletevalue
#else
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Presspeech"; Flags: dontcreatekey uninsdeletevalue
#endif

[Files]
; No transaction entry overwrites the live executable or an active bundle.
Source: "{#SourceDir}\{#BundleDir}\*"; DestDir: "{app}\{#BundleDir}"; Excludes: "presspeech-bundle.owner,Presspeech.exe.pending"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: CopyBundle; AfterInstall: PayloadCheckpoint
Source: "{#SourceDir}\Presspeech.exe"; DestDir: "{app}\{#BundleDir}"; DestName: "Presspeech.exe.pending"; Flags: ignoreversion

[UninstallDelete]
; The staged executable is moved after Inno has committed its uninstall log.
Type: files; Name: "{app}\Presspeech.exe"

[Icons]
Name: "{group}\Presspeech"; Filename: "{app}\Presspeech.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Presspeech"; Filename: "{app}\Presspeech.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Presspeech.exe"; Description: "Launch Presspeech"; WorkingDir: "{app}"; Check: Activated; Flags: nowait postinstall skipifsilent

[Code]
var
  ReuseBundle, ActivationOK, OwnedInstall: Boolean;
#ifdef Qualification
  PayloadPaused: Boolean;
#endif

function MoveFileEx(const ExistingName, NewName: String; Flags: LongWord): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';
function FileAttributes(const Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function BundlePath: String;
begin
  Result := ExpandConstant('{app}\{#BundleDir}');
end;

function SafeChain(Path: String): Boolean;
var
  Parent: String;
  Attributes: LongWord;
begin
  Result := False;
  repeat
    Attributes := FileAttributes(Path);
    if (Attributes <> $FFFFFFFF) and
       ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) then Exit;
    Parent := ExtractFileDir(Path);
    if (Parent = '') or (CompareText(Parent, Path) = 0) then Break;
    Path := Parent;
  until False;
  Result := True;
end;

function OwnedBundle(const Path, Name: String): Boolean;
var
  Marker: AnsiString;
begin
  Result := False;
  if not SafeChain(Path + '\presspeech-bundle.owner') then Exit;
  if not LoadStringFromFile(Path + '\presspeech-bundle.owner', Marker) then Exit;
  Result := String(Marker) = Name;
end;

function OwnedRoot: Boolean;
var
  Marker: AnsiString;
  Path: String;
begin
  Result := False;
  Path := ExpandConstant('{app}\.presspeech-install-owner');
  if not SafeChain(Path) then Exit;
  if not LoadStringFromFile(Path, Marker) then Exit;
  Result := String(Marker) = '{#InstallerAppId}';
end;

function BundleName(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  if (Length(Name) <> 44) or (Copy(Name, 1, 12) <> '_presspeech-') then Exit;
  for I := 13 to Length(Name) do
    if Pos(Name[I], '0123456789abcdef') = 0 then Exit;
  Result := True;
end;

function ExactTree(const Path, Relative: String; Files: TStringList): Boolean;
var
  Entry: TFindRec;
  Child: String;
begin
  Result := False;
  if FindFirst(Path + '\*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          if (Entry.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then Exit;
          Child := Relative + Entry.Name;
          if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
            if not ExactTree(Path + '\' + Entry.Name, Child + '\', Files) then Exit;
          end else if (Child <> 'presspeech-bundle.owner') and
                      (Child <> 'presspeech-bundle.sha256') and
                      (Child <> 'Presspeech.exe.pending') and
                      (Files.IndexOf(Child) < 0) then Exit;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  Result := True;
end;

function VerifyBundle: Boolean;
var
  Lines: TArrayOfString;
  I: Integer;
  Relative, Filename: String;
  Files: TStringList;
begin
  Result := False;
  if not OwnedBundle(BundlePath, '{#BundleDir}') then Exit;
  Files := TStringList.Create;
  try
    try
      if GetSHA256OfFile(BundlePath + '\presspeech-bundle.sha256') <> '{#ManifestSHA256}' then Exit;
      if not LoadStringsFromFile(BundlePath + '\presspeech-bundle.sha256', Lines) then Exit;
      if GetArrayLength(Lines) = 0 then Exit;
      for I := 0 to GetArrayLength(Lines) - 1 do begin
        if (Length(Lines[I]) < 67) or (Copy(Lines[I], 65, 2) <> '  ') then Exit;
        Relative := Copy(Lines[I], 67, Length(Lines[I]));
        if (Relative = '') or (Relative[1] = '\') or
           (Pos(':', Relative) > 0) or (Pos('..', Relative) > 0) or
           (Pos('/', Relative) > 0) or (Files.IndexOf(Relative) >= 0) then Exit;
        Filename := BundlePath + '\' + Relative;
        if not SafeChain(Filename) then Exit;
        if GetSHA256OfFile(Filename) <> Copy(Lines[I], 1, 64) then Exit;
        Files.Add(Relative);
      end;
      Result := ExactTree(BundlePath, '', Files);
    except
      Log('Bundle integrity verification failed.');
      Result := False;
    end;
  finally
    Files.Free;
  end;
end;

function KnownInstall: Boolean;
var
  Previous: String;
begin
  Result := RegQueryStringValue(HKCU64,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#InstallerAppId}_is1',
    'Inno Setup: App Path', Previous) and
    (CompareText(RemoveBackslashUnlessRoot(Previous),
                 RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) = 0);
end;

function EmptyDirectory(const Path: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := True;
  if FindFirst(Path + '\*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Result := False;
          Exit;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

function LiveUsesBundle: Boolean;
var
  Image: AnsiString;
  Live: String;
begin
  Live := ExpandConstant('{app}\Presspeech.exe');
  Result := False;
  if not FileExists(Live) then Exit;
  { The pinned bootloader's contents-directory option is stored verbatim in
    its archive table. A conservative match also covers a modified executable.
    Build verification checks that option before packaging. }
  Result := True;
  if not LoadStringFromFile(Live, Image) then Exit;
  Result := Pos(AnsiString('{#BundleDir}'), Image) > 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Parent: String;
  FreeBytes, TotalBytes, RequiredBytes: Int64;
begin
  Result := 'Setup cannot safely use this installation directory.';
  if not SafeChain(ExpandConstant('{app}\Presspeech.exe')) or
     not SafeChain(BundlePath) then Exit;
  OwnedInstall := KnownInstall;
  if FileExists(ExpandConstant('{app}\.presspeech-install-owner')) and not OwnedRoot then Exit;
  if DirExists(ExpandConstant('{app}')) and not OwnedInstall and not OwnedRoot and
     not EmptyDirectory(ExpandConstant('{app}')) then begin
    Result := 'Choose an empty directory or the registered Presspeech installation.';
    Exit;
  end;
  ReuseBundle := DirExists(BundlePath) and VerifyBundle;
  if DirExists(BundlePath) and not ReuseBundle then begin
    if LiveUsesBundle or not OwnedBundle(BundlePath, '{#BundleDir}') then begin
      Result := 'This build has a damaged or unrecognised bundle. Setup has preserved it. To repair this build, uninstall it and run Setup again, or install a newer build.';
      Exit;
    end;
  end;
  if Length(BundlePath) + 1 + StrToInt('{#MaxBundlePath}') > 32700 then begin
    Result := 'Choose a shorter installation path for this bundle.';
    Exit;
  end;
  Parent := ExpandConstant('{app}');
  while not DirExists(Parent) do Parent := ExtractFileDir(Parent);
  RequiredBytes := StrToInt64('{#BundleBytes}') + 268435456;
  if ReuseBundle then RequiredBytes := StrToInt64('{#ExeBytes}') + 268435456;
  { The complete old and new payloads coexist; a skipped folder page or a
    suppressed warning must never opt out of this hard admission check. }
  if not GetSpaceOnDisk64(Parent, FreeBytes, TotalBytes) or
     (FreeBytes < RequiredBytes) then begin
    Result := 'Not enough free space to stage the complete new bundle while preserving the current installation. Free space and run Setup again.';
    Exit;
  end;
  Result := '';
end;

function CopyBundle: Boolean;
begin
  Result := not ReuseBundle;
end;

function Activated: Boolean;
begin
  Result := ActivationOK;
end;

procedure QualificationCheckpoint(const Stage: String);
#ifdef Qualification
var
  Checkpoint, ReleaseFile: String;
  I: Integer;
#endif
begin
#ifdef Qualification
  if ExpandConstant('{param:QASTAGE|}') <> Stage then Exit;
  Checkpoint := ExpandConstant('{param:QACHECKPOINT|}');
  ReleaseFile := Checkpoint + '.release';
  if Checkpoint = '' then Exit;
  SaveStringToFile(Checkpoint, IntToStr(WizardForm.Handle), False);
  for I := 1 to 600 do begin
    if FileExists(ReleaseFile) then Exit;
    Sleep(50);
  end;
  RaiseException('Qualification checkpoint timed out');
#endif
end;

procedure PayloadCheckpoint;
begin
#ifdef Qualification
  if not PayloadPaused then begin
    PayloadPaused := True;
    QualificationCheckpoint('payload');
  end;
#endif
end;

procedure CleanupBundles(const Keep: String);
var
  Entry: TFindRec;
  Path: String;
begin
  if not SafeChain(ExpandConstant('{app}')) then Exit;
  if FindFirst(ExpandConstant('{app}\_presspeech-*'), Entry) then begin
    try
      repeat
        if BundleName(Entry.Name) and (Entry.Name <> Keep) and
           ((Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
           ((Entry.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) then begin
          Path := ExpandConstant('{app}\') + Entry.Name;
          if OwnedBundle(Path, Entry.Name) and not DelTree(Path, True, True, True) then
            Log('An inactive bundle could not be removed; a later setup or uninstall can retry.');
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

procedure CurStepChanged(Step: TSetupStep);
var
  Pending: String;
begin
  if Step = ssInstall then begin
    if not SafeChain(ExpandConstant('{app}')) or not ForceDirectories(ExpandConstant('{app}')) or
       not SaveStringToFile(ExpandConstant('{app}\.presspeech-install-owner'), '{#InstallerAppId}', False) then
      RaiseException('Installation ownership could not be recorded safely');
    if not ReuseBundle then begin
      { Recover only this package's owned, inactive interrupted staging. }
      if DirExists(BundlePath) then begin
        if LiveUsesBundle or not OwnedBundle(BundlePath, '{#BundleDir}') or
           not DelTree(BundlePath, True, True, True) then
          RaiseException('Inactive staging could not be recovered safely');
      end;
      if not ForceDirectories(BundlePath) or
         not SaveStringToFile(BundlePath + '\presspeech-bundle.owner', '{#BundleDir}', False) then
        RaiseException('Bundle staging could not be created');
    end;
  end;
  if Step <> ssPostInstall then Exit;
  ActivationOK := False;
  try
    QualificationCheckpoint('staged');
    Pending := BundlePath + '\Presspeech.exe.pending';
    if not VerifyBundle or not SafeChain(Pending) or
       (GetSHA256OfFile(Pending) <> '{#ExeSHA256}') then
      RaiseException('The staged payload is incomplete or damaged');
    QualificationCheckpoint('verified');
    if CheckForMutexes('{#InstanceMutex}') then
      RaiseException('Presspeech is running');
    { This is the only live-file change, after Inno's rollback boundary.
      No copy/delete fallback or delayed reboot replacement is permitted. }
    if not MoveFileEx(Pending, ExpandConstant('{app}\Presspeech.exe'), 1 or 8) then
      RaiseException('The launcher could not be replaced');
    ActivationOK := True;
    Log('PRESSPEECH: verified bundle activated {#BundleDir}');
    QualificationCheckpoint('activated');
    CleanupBundles('{#BundleDir}');
    { Legacy contents are automatically removed only in the dedicated default
      directory. A custom/shared legacy directory retains unlogged files. }
    if OwnedInstall and
       (CompareText(ExpandConstant('{app}'), ExpandConstant('{localappdata}\Programs\Presspeech')) = 0) and
       SafeChain(ExpandConstant('{app}\_internal')) then
      DelTree(ExpandConstant('{app}\_internal'), True, True, True);
  except
    if ActivationOK then
      Log('PRESSPEECH: bundle is active; cleanup was deferred.')
    else begin
      Log('PRESSPEECH: activation did not complete; existing installation preserved.');
      SuppressibleMsgBox('Presspeech could not be activated. Existing application files have been preserved. Close Presspeech and run Setup again. If the problem continues, see the support guide.', mbError, MB_OK, IDOK);
    end;
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if not ActivationOK then Result := 23;
end;

procedure CurUninstallStepChanged(Step: TUninstallStep);
begin
  if Step = usPostUninstall then begin
    CleanupBundles('');
    if OwnedRoot then DeleteFile(ExpandConstant('{app}\.presspeech-install-owner'));
    { The owner marker is deliberately independent of Inno's rollback log.
      Remove the app directory only if no unrelated files remain. }
    if SafeChain(ExpandConstant('{app}')) then RemoveDir(ExpandConstant('{app}'));
  end;
end;
