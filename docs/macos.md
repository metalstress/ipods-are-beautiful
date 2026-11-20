# Apple Silicon preview

Version 1.2.0 adds a native ARM64 macOS build. It uses the same Qt widgets, custom window controls, layouts, colours, typography settings and artwork assets as the Windows app. There is no platform-specific redesign.

## Install

Requires an M-series Mac and macOS 14.4 or newer, matching the pinned Qt 6.12 runtime. Open the DMG and drag **iPods Are Beautiful.app** to Applications. Python, Rust, FFmpeg and Homebrew are not required to run the bundle.

The preview is ad-hoc signed and has no Apple notarization. If macOS blocks first launch, use **System Settings → Privacy & Security → Open Anyway** for this app. Allow local-network access when prompted to discover and use devices over Wi-Fi.

## Device setup

For iPhone, connect by USB, unlock it and trust the Mac in Finder first. For Wi-Fi, enable **Show this iPhone when on Wi-Fi** in Finder and keep both devices on the same network. No Windows Apple driver package is used. The adapter retains its existing iPhone 16 Pro / iOS 27 validation gate.

For iPod touch 4, the device still needs iOS 6.1.6, a jailbreak, OpenSSH, Cycript and the configured SSH key and pinned host key. USB uses `/var/run/usbmuxd`; Wi-Fi uses the existing SSH transport. A new Mac needs its own device setup; Windows settings are not copied automatically.

Settings, journals and captures are under `~/Library/Application Support/iMetal`. The app bundles no pairing records, SSH keys or music files.

## Build

Build on Apple Silicon with native ARM64 Python 3.13 and the Xcode command-line tools, not an Intel Python running through Rosetta:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r imetal/requirements.txt
cargo test --locked --manifest-path modern-ios/signer/Cargo.toml
cargo build --locked --release --manifest-path modern-ios/signer/Cargo.toml
.venv/bin/python imetal/build_macos.py
.venv/bin/python tools/check_macos_release.py 'outputs/macos/iPods Are Beautiful.app' work/macos-check
.venv/bin/python tools/package_macos_release.py 'outputs/macos/iPods Are Beautiful.app' outputs/macos-release
```

The app includes ARM64 FFmpeg and the native Rust signer. The legacy iOS SQLite helper is compressed as a device resource, then sent to the iPod unchanged; it is not executed or signed as a Mac program.

## Validation

The `Apple Silicon build` workflow runs on a native macOS ARM64 runner. All 164 application tests, 3 Music protocol tests and 4 Rust signing-vector tests passed. The frozen `.app` passed offscreen startup, empty transfer, artwork-worker, library-read and playlist-planning checks with a separate empty settings directory. Its main executable, signer and FFmpeg were checked for ARM64, and the app's ad-hoc signature was verified.

No device was connected to the CI runner. Real Mac USB/Wi-Fi transfers, playback, Finder coexistence and first-launch local-network permission behaviour remain untested. The Mac preview does not replace the hardware-tested Windows release.
