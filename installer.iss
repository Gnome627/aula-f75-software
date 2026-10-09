; Inno Setup script for the Windows installer. Built by .github/workflows/build.yml:
;   iscc /DAppVersion=1.2.3 installer.iss
; Expects the one-folder PyInstaller build in dist-dir\AULA-F75.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C0E5A53-6E0B-4B1F-9C55-F75A01A0F750}
AppName=AULA F75
AppVersion={#AppVersion}
AppPublisher=Gnome627
AppPublisherURL=https://github.com/Gnome627/aula-f75-software
AppSupportURL=https://github.com/Gnome627/aula-f75-software/issues
DefaultDirName={autopf}\AULA F75
DisableProgramGroupPage=yes
; installs for the current user without administrator rights; the dialog offers "for all users" too
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=AULA-F75-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\AULA-F75.exe
UninstallDisplayName=AULA F75
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist-dir\AULA-F75\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\AULA F75"; Filename: "{app}\AULA-F75.exe"
Name: "{autodesktop}\AULA F75"; Filename: "{app}\AULA-F75.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AULA-F75.exe"; Description: "{cm:LaunchProgram,AULA F75}"; Flags: nowait postinstall skipifsilent
