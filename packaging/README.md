# Windows installer

Atlas ships as a per-user Windows installer. The installed application contains its Python runtime
and required Python packages, so the person installing Atlas does not need to install Python or run
terminal commands. The installer does not require administrator access.

## Build locally

On a Windows x64 build machine, install Python 3.11 and Inno Setup 6, then run:

```powershell
py -3.11 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\pyinstaller.exe --noconfirm --clean packaging\Atlas.spec
New-Item -ItemType Directory -Force dist\installer | Out-Null
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\Atlas.iss
```

The installer is written to `dist/installer/AtlasSetup-1.2.0-windows-x64.exe` by default. Tagged builds use the version from the tag.

## Automated release build

The GitHub Actions workflow builds the installer on Windows and uploads it as an artifact when run
manually. Pushing a version tag such as `v1.2.0` also attaches the installer to a GitHub Release.

## Installed data

Settings, session recordings, and analysis exports are written under `%LOCALAPPDATA%\Atlas`, not
inside the installed program folder. Uninstalling Atlas leaves that user data intact.
