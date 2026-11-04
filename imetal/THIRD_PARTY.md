# Third-party components

- Qt / PySide6 — LGPLv3; dynamically bundled Qt libraries remain replaceable.
  https://doc.qt.io/qtforpython-6/licenses.html
  https://code.qt.io/
  LGPL notice: resources/licenses/Qt-LGPL-3.0.txt.
- Paramiko 4.0.0 — LGPL-2.1.
  https://github.com/paramiko/paramiko
- Mutagen 1.47.0 — GPL-2.0-or-later.
  https://github.com/quodlibet/mutagen
- Pillow 13.0.0 — MIT-CMU; used for the cached Gaussian inner-shadow mask.
  https://github.com/python-pillow/Pillow
- FFmpeg 7.1, distributed separately as an executable from the imageio-ffmpeg wheel.
  https://github.com/imageio/imageio-ffmpeg
  https://github.com/imageio/imageio-binaries
  https://ffmpeg.org/legal.html
- Cycript 0.9.594 — AGPLv3; separately installed on the user's jailbroken device.
  Package source: https://apt.saurik.com/
  https://www.cycript.org/
  https://git.saurik.com/cycript.git
- SQLite CLI from the Telesphoreo sqlite3 package (3.5.9-13), public domain.
  Only the CLI is extracted; the device's system SQLite is used, not replaced.
  https://sqlite.org/copyright.html
- The StoreServices import sequence is based on documented open source work by
  Árpád Goretity / H2CO3, libipodimport, 3-clause BSD license:
  https://github.com/H2CO3/libipodimport
- The USB wire protocol is compatible with usbmux:
  https://github.com/libimobiledevice/libusbmuxd
- pymobiledevice3 11.49.1 — GPL-3.0; paired Apple USB/Wi-Fi services.
  https://github.com/doronz88/pymobiledevice3
  GPL notice: resources/licenses/pymobiledevice3-GPL-3.0.txt.
- rustunes — MIT, commit 057ee8fc9c9da65795a6f14497fc408c2fe23c76.
  The bundled Rust Music signer uses its original signing implementation;
  the Windows ATC adapter follows its protocol research.
  https://github.com/fbiba/rustunes
  MIT notice: resources/licenses/rustunes-MIT.txt.
- Apple iPhone 16 Pro Home Screen press image, June 9, 2025.
  The sidebar renders the right-hand device from the unmodified source image.
  https://www.apple.com/newsroom/2025/06/apple-elevates-the-iphone-experience-with-ios-26/
  Apple notice: resources/licenses/Apple-Press-Image-LEGAL_NOTICE.rtf.

No private SSH keys, passwords, Apple account credentials, or user's audio files are bundled.
