# iPods Are Beautiful & I'm No Longer Afraid to Die

Windows music uploader, version 1.0.3.

Run iPods Are Beautiful.exe. Python, Rust and build tools are not required.

Drag songs or albums into the window. With auto upload enabled, transfer starts immediately; otherwise press Upload after building the queue.

The tested devices are a jailbroken iPod touch 4 on iOS 6.1.6 and an iPhone 16 Pro on iOS 27.0. USB and Wi-Fi are supported. Only one device can receive a transfer at a time.

iPhone requires Apple device drivers and an existing trusted pairing. Wi-Fi also needs wireless sync enabled. iPod touch requires OpenSSH, Cycript, SSH key authentication and a pinned host key configured in %LOCALAPPDATA%\iMetal\settings.json.

Compatible files pass through unchanged; unsupported formats are converted to ALAC. Original files on the PC are kept. Eject stops the remaining queue after the current import finishes.

Settings, cache and transfer journals are stored under %LOCALAPPDATA%\iMetal. No private keys, pairing records, backups or music files are distributed.

Source, setup notes and current limitations: https://github.com/metalstress/ipods-are-beautiful

Third-party credits: THIRD_PARTY.md.
