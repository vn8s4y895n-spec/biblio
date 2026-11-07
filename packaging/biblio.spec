# PyInstaller : application Biblio (Mac : Biblio.app ; Windows : dossier Biblio avec Biblio.exe).
# Construire avec packaging/build_mac.sh ou packaging/build_windows.ps1, pas directement.
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

from biblio import __version__

datas = collect_data_files("biblio") + collect_data_files("citeproc") + collect_data_files("sqlite_vec")
binaries = collect_dynamic_libs("sqlite_vec")
hiddenimports = (collect_submodules("biblio") + collect_submodules("uvicorn") + collect_submodules("mcp")
                 + collect_submodules("keyring.backends") + ["webview.platforms.cocoa" if sys.platform == "darwin"
                                                              else "webview.platforms.winforms"])
icon = "build.nosync/Biblio.icns" if sys.platform == "darwin" else "icon.png"  # Windows : converti par Pillow

a = Analysis(["entry.py"], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=["tkinter", "torch", "matplotlib", "IPython"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Biblio", console=False, icon=icon,
          argv_emulation=False)  # les fichiers ouverts depuis le Finder arrivent par app.py (application:openFiles:)
coll = COLLECT(exe, a.binaries, a.datas, name="Biblio")

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="Biblio.app", icon=icon, bundle_identifier="fr.biblio.app", version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Paquet Biblio", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner",
                "LSItemContentTypes": ["fr.biblio.package"], "CFBundleTypeIconFile": "Biblio.icns",
            }],
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": "fr.biblio.package", "UTTypeDescription": "Paquet Biblio",
                "UTTypeConformsTo": ["public.data", "public.zip-archive"], "UTTypeIconFile": "Biblio.icns",
                "UTTypeTagSpecification": {"public.filename-extension": ["biblio"], "public.mime-type": ["application/x-biblio"]},
            }],
        },
    )  # fmt: skip
