; Installeur Windows (Inno Setup 6) : lancé par build_windows.ps1, qui passe /DVersion=…
[Setup]
AppName=Biblio
AppVersion={#Version}
DefaultDirName={localappdata}\Programs\Biblio
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Biblio-{#Version}-setup
ChangesAssociations=yes
UninstallDisplayIcon={app}\Biblio.exe
; Mise à jour depuis Biblio (installeur lancé en /SILENT) : fermer Biblio, puis le relancer (section [Run]).
CloseApplications=force
RestartApplications=no

[Files]
Source: "..\dist\Biblio\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{autoprograms}\Biblio"; Filename: "{app}\Biblio.exe"
Name: "{autodesktop}\Biblio"; Filename: "{app}\Biblio.exe"

[Registry]
; Double-clic sur un paquet .biblio : Biblio.exe "<fichier>" (aperçu avant import). Installation par utilisateur.
Root: HKCU; Subkey: "Software\Classes\.biblio"; ValueType: string; ValueData: "Biblio.Package"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Biblio.Package"; ValueType: string; ValueData: "Paquet Biblio"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Biblio.Package\DefaultIcon"; ValueType: string; ValueData: "{app}\Biblio.exe,0"
Root: HKCU; Subkey: "Software\Classes\Biblio.Package\shell\open\command"; ValueType: string; ValueData: """{app}\Biblio.exe"" ""%1"""

[Run]
Filename: "{app}\Biblio.exe"; Description: "Ouvrir Biblio"; Flags: nowait postinstall
