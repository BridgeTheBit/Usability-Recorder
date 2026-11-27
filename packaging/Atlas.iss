#define AppName "Atlas"
#ifndef AppVersion
#define AppVersion "1.3.1"
#endif
#define AppPublisher "BridgeTheBit"
#define AppExeName "Atlas.exe"

[Setup]
AppId={{9B2D2DF2-96C3-4A8B-A150-207450B0AC90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/BridgeTheBit/Usability-Recorder
DefaultDirName={localappdata}\Programs\Atlas
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\assets\atlas-icon.ico
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist\installer
OutputBaseFilename=AtlasSetup-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\Atlas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\browser_extension\*"; DestDir: "{app}\browser_extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Atlas"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Browser extension setup"; Filename: "{app}\browser_extension\INSTALL.txt"
Name: "{autodesktop}\Atlas"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch Atlas"; Flags: nowait postinstall skipifsilent
