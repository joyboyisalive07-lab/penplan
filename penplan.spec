# PyInstaller build description. Run it with: pyinstaller penplan.spec
#
# One file, no console window, and the icon and the bundled profiles carried
# inside, so the executable runs from any path, including a flash drive and a
# path with spaces in it.

from pathlib import Path

ROOT = Path(SPECPATH)
PACKAGE = ROOT / "src" / "penplan"

analysis = Analysis(
    [str(PACKAGE / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(PACKAGE / "penplan.ico"), "penplan"),
        (str(PACKAGE / "profiles"), "penplan/profiles"),
    ],
    hiddenimports=[],
    # Nothing here is used, and every one of them drags in a megabyte or more.
    excludes=[
        "numpy",
        "pytest",
        "setuptools",
        "unittest",
        "pydoc",
        "doctest",
        "email",
        "http",
        "xml",
        "PIL.ImageQt",
    ],
    noarchive=False,
)
archive = PYZ(analysis.pure)

executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="penplan",
    icon=str(PACKAGE / "penplan.ico"),
    console=False,
    upx=False,
    strip=False,
    debug=False,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,
)
