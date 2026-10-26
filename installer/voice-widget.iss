; Inno Setup 6 script (not compiled in CI by default). Build the app first: python -m PyInstaller --noconfirm voice-widget.spec
; Compile: ISCC.exe installer\voice-widget.iss   ->  dist\voice-widget-setup.exe
#define AppName "voice-widget"
#define AppVersion GetEnv("VW_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6D1F3E0A-5B7C-4C1E-9A44-7E2A0C5B9A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=skiddgoddamn
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=voice-widget-setup
SetupIconFile=..\assets\icons\icon.ico
UninstallDisplayIcon={app}\voice-widget.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
WizardStyle=modern

[Files]
Source: "..\dist\voice-widget\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\voice-widget.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\voice-widget.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\voice-widget.exe"; Description: "Start voice-widget"; Flags: nowait postinstall skipifsilent

[Messages]
; models (about 2 GB) are downloaded on first start into %LOCALAPPDATA%\voice-widget\models
