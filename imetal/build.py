"""Build the local Windows distribution; never packages personal credentials."""
import os
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
NAME = "iPods Are Beautiful"
parser = argparse.ArgumentParser()
parser.add_argument('--dist-root', type=Path, default=WORKSPACE/'outputs')
parser.add_argument('--build-root', type=Path, default=WORKSPACE/'work/ipods-build')
args = parser.parse_args()
DIST = args.dist_root.resolve()
BUILD = args.build_root.resolve()
assert (DIST/NAME).resolve().parent == DIST
assert len(BUILD.parts) >= 3

sys.path.insert(0, str(ROOT))
from media import ffmpeg
from version import windows_resource

BUILD.mkdir(parents=True, exist_ok=True)
version_file = BUILD / 'windows-version.txt'
version_file.write_text(windows_resource(), encoding='utf-8')
command = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
    "--name", NAME, "--icon", str(ROOT / "resources/imetal.ico"),
    "--version-file", str(version_file),
    "--distpath", str(DIST), "--workpath", str(BUILD),
    "--specpath", str(BUILD), "--paths", str(ROOT),
    "--add-data", str(ROOT / "resources") + os.pathsep + "resources",
    "--add-data", ffmpeg() + os.pathsep + "resources",
    "--collect-submodules", "mutagen",
]
for module in ["imageio_ffmpeg", "PySide6.QtWebEngineCore",
               "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
               "PySide6.QtPdf", "PySide6.QtNetworkAuth"]:
    command += ["--exclude-module", module]
command += [str(ROOT / "app.py")]
with (BUILD / "build.log").open("w", encoding="utf-8") as log:
    result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
if result.returncode:
    print((BUILD / "build.log").read_text(encoding="utf-8")[-7000:])
    raise SystemExit(result.returncode)
# Qt uses the Windows ICU API. A different ICU from an unrelated PATH entry
# (for example Poppler) has version-suffixed exports and must not shadow it.
icu = DIST / NAME / "_internal/icuuc.dll"
if icu.exists():
    import pefile
    pe = pefile.PE(str(icu))
    exports = {entry.name for entry in pe.DIRECTORY_ENTRY_EXPORT.symbols}
    pe.close()
    if b"ucnv_open" not in exports:
        icu.unlink()
for name in ["README.md", "THIRD_PARTY.md"]:
    shutil.copyfile(ROOT / name, DIST / NAME / name)
print("BUILT", DIST / NAME / (NAME + ".exe"), flush=True)
