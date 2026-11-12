"""Build the desktop app, the command-line tool and the lab server with PyInstaller.

    python packaging/build.py               # desktop app and command line
    python packaging/build.py gui|cli|server

Outputs in dist/: SangerSeqAnalyzer/ (Windows, Linux) or SangerSeqAnalyzer.app (macOS),
the one-file sanger-seq executable, and SangerSeqServer/ (needs the [server] extra).
Run from the repository root.
"""

from __future__ import annotations

import importlib.metadata
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
SEP = os.pathsep
APP = "SangerSeqAnalyzer"
BUNDLE_ID = "org.sangerseq.analyzer"

# Qt modules the window does not use; leaving them out keeps the bundle small.
QT_EXCLUDES = ["QtNetwork", "QtQml", "QtQuick", "QtQuickControls2", "QtQuickWidgets", "QtOpenGL",
               "QtOpenGLWidgets", "QtSql", "QtDBus", "QtDesigner", "QtHelp", "QtUiTools", "QtXml", "QtConcurrent",
               "QtPrintSupport", "QtTest", "QtSvgWidgets", "QtLabsStyleKit"]


def version() -> str:
    text = (ROOT / "src/sanger_seq_analyzer/__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text)[1]


def build_gui() -> None:
    args = ["--noconfirm", "--clean", "--windowed", "--onedir", "--name", APP,
            "--add-data", f"{ROOT / 'examples'}{SEP}examples",
            "--exclude-module", "tkinter"]
    for module in QT_EXCLUDES:
        args += ["--exclude-module", f"PySide6.{module}"]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    PyInstaller.__main__.run([*args, str(ROOT / "packaging/launcher_gui.py")])
    if sys.platform == "darwin":
        patch_macos_bundle(ROOT / "dist" / f"{APP}.app")


def build_cli() -> None:
    PyInstaller.__main__.run(["--noconfirm", "--clean", "--onefile", "--console", "--name", "sanger-seq",
                              "--exclude-module", "PySide6", "--exclude-module", "shiboken6",
                              "--exclude-module", "tkinter", str(ROOT / "packaging/launcher_cli.py")])


def build_server() -> None:
    server = ROOT / "src/sanger_seq_analyzer/server"
    PyInstaller.__main__.run([
        "--noconfirm", "--clean", "--onedir", "--console", "--name", "SangerSeqServer",
        "--add-data", f"{server / 'templates'}{SEP}sanger_seq_analyzer/server/templates",
        "--add-data", f"{server / 'static'}{SEP}sanger_seq_analyzer/server/static",
        "--collect-submodules", "uvicorn", "--collect-data", "tzdata",
        "--hidden-import", "sanger_seq_analyzer.server.app",
        "--exclude-module", "PySide6", "--exclude-module", "shiboken6", "--exclude-module", "tkinter",
        str(ROOT / "packaging/launcher_server.py")])


def minimum_macos() -> str:
    """Oldest macOS supported by the installed PySide6 wheel (from its platform tag)."""
    wheel = importlib.metadata.distribution("PySide6-Essentials").read_text("WHEEL") or ""
    found = re.findall(r"macosx_(\d+)_(\d+)", wheel)
    return ".".join(max(found, key=lambda v: (int(v[0]), int(v[1])))) if found else "11.0"


def patch_macos_bundle(app: Path) -> None:
    """Version, minimum system and HiDPI keys in Info.plist, then an ad hoc re-sign (the patch breaks the seal)."""
    plist = app / "Contents" / "Info.plist"
    with open(plist, "rb") as fh:
        info = plistlib.load(fh)
    info.update({"CFBundleShortVersionString": version(), "CFBundleVersion": version(),
                 "LSMinimumSystemVersion": minimum_macos(), "NSHighResolutionCapable": True})
    with open(plist, "wb") as fh:
        plistlib.dump(info, fh)
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(app)], check=True)


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    targets = argv or ["gui", "cli"]
    if "gui" in targets:
        build_gui()
    if "cli" in targets:
        build_cli()
    if "server" in targets:
        build_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
