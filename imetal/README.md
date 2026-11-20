# iPods Are Beautiful & I'm No Longer Afraid to Die

Music transfer and library manager, version 1.2.0.

Run iPods Are Beautiful.exe on Windows or iPods Are Beautiful.app on macOS. The Apple Silicon build requires macOS 14.4 or newer. Python, Rust and build tools are not required.

Drag songs or albums into the window. With auto upload enabled, transfer starts immediately; otherwise press Upload after building the queue.

Open Manage library to browse albums, artists, songs and playlists on a connected device. You can delete songs or albums, edit genre and year, and replace artwork. Create, rename or delete ordinary playlists, or add selected songs from the Songs tab. Removing a song from a playlist keeps it in Music.

Hardware checks used Windows 11 with a jailbroken iPod touch 4 on iOS 6.1.6 and an iPhone 16 Pro on iOS 27.0. The Mac build is a preview; Mac device transfers have not been tested. USB and Wi-Fi are supported by the adapters. Only one device can receive a transfer at a time.

iPhone requires an existing trusted pairing; Windows also needs Apple device drivers. On Mac, connect by USB and trust the computer in Finder first. Wi-Fi also needs wireless sync enabled. iPod touch requires OpenSSH, Cycript, SSH key authentication and a pinned host key in settings.json.

Compatible files pass through unchanged; unsupported formats are converted to ALAC. Original files on the PC are kept. Eject stops the remaining queue after the current import finishes.

While idle, the app checks local iPhone albums for missing artwork and installs matching covers. Checks run after connection and imports; upload and editing take priority. Existing covers and cloud-only songs are skipped.

Settings, cache and transfer journals are stored under %LOCALAPPDATA%\iMetal on Windows or ~/Library/Application Support/iMetal on Mac. No private keys, pairing records, backups or music files are distributed.

The preview Mac app is ad-hoc signed, not notarized. If macOS blocks it, allow the downloaded app in System Settings > Privacy & Security. Allow local-network access when prompted to use Wi-Fi.

Source, setup notes and current limitations: https://github.com/metalstress/ipods-are-beautiful

Third-party credits: THIRD_PARTY.md.
