# Testing

Hardware checks were performed on Windows 11 x64 with iPod touch 4 / iOS 6.1.6 and iPhone 16 Pro / iOS 27.0.

On November 4, 2026, the publication checkout passed 49 application tests, 3 additional Music protocol tests and 4 Rust signer tests. These runs used an isolated local settings directory and an offscreen Qt backend; no device was contacted.

Both devices accepted USB and Wi-Fi imports into their built-in Music app. Playback was checked manually on selected tracks. The iPhone Wi-Fi test imported King Woman's nine-track Celestial Blues album from 24-bit/96 kHz FLAC sources, skipping two tracks that were already present. The resulting catalogue contained all nine tracks once each, with complete audio assets. This was not an end-to-end listening test of every track.

Automated checks run without a visible desktop window or a device connection:

```powershell
python -m unittest discover -s imetal -p "test_*.py"
python -m unittest discover -s modern-ios -p "test_*.py"
cargo test --manifest-path modern-ios/signer/Cargo.toml
```

The Python suite covers PCM-preserving FLAC conversion, metadata, duplicate handling, pending-import cleanup, queue modes, cancellation, progress, light-theme controls, device discovery, paired Wi-Fi sessions, backend IPC and ATC keepalive handling. Protocol tests check catalogue identity and signed-operation construction. Rust tests use the pinned upstream vectors.

Other hardware, fresh machine setup, every audio format, long-running stress transfers and future firmware updates have not been validated. iPod Classic has no implementation. The portfolio animation demonstrates the interaction and compresses transfer timing; it is not a speed benchmark.
