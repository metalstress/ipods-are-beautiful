"""Build an ARM64 macOS app with the existing interface and native helpers."""
import argparse
import gzip
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
NAME = 'iPods Are Beautiful'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist-root', type=Path, default=WORKSPACE/'outputs/macos')
    parser.add_argument('--build-root', type=Path, default=WORKSPACE/'work/macos-build')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        parser.error('Build this app with native ARM64 Python on an Apple Silicon Mac.')
    dist, build = args.dist_root.resolve(), args.build_root.resolve()
    build.mkdir(parents=True, exist_ok=True)
    resources = build/'resources';resources.mkdir(exist_ok=True)
    # The SQLite helper runs on iOS 6, not on the Mac. Compress it so the
    # macOS bundler does not treat its ARMv6 Mach-O as a host executable.
    for source in (ROOT/'resources').rglob('*'):
        if not source.is_file() or source.suffix.lower() == '.exe' or source.name == 'ipods-music-signer':
            continue
        target = resources/source.relative_to(ROOT/'resources')
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.name == 'imetal-sqlite':
            target.with_suffix('.gz').write_bytes(gzip.compress(source.read_bytes(), mtime=0))
        else:
            shutil.copyfile(source, target)
    for name in ('README.md', 'THIRD_PARTY.md'):
        shutil.copyfile(ROOT/name, resources/name)
    from PIL import Image
    icon = build/'imetal.icns'
    with Image.open(ROOT/'resources/imetal.png') as image:
        image.save(icon, format='ICNS')
    from media import ffmpeg
    signer = WORKSPACE/'modern-ios/signer/target/release/ipods-music-signer'
    if not signer.is_file():
        raise SystemExit('Build the native Rust signer with cargo build --release first.')
    for binary in (signer, Path(ffmpeg())):
        architectures = subprocess.check_output(['lipo', '-archs', str(binary)], text=True).split()
        if 'arm64' not in architectures:
            raise SystemExit(f'{binary.name} has no ARM64 architecture.')
    env = dict(os.environ, IPODS_MAC_RESOURCES=str(resources), IPODS_MAC_ICON=str(icon),
               IPODS_MAC_SIGNER=str(signer), MACOSX_DEPLOYMENT_TARGET='14.4')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(dist),
               '--workpath', str(build/'pyinstaller'), str(ROOT/'macos.spec')]
    with (build/'build.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=WORKSPACE, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print((build/'build.log').read_text(encoding='utf-8')[-9000:])
        raise SystemExit(result.returncode)
    app = dist/(NAME+'.app')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print('BUILT', app, flush=True)


if __name__ == '__main__':
    main()
