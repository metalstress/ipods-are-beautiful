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

## Library foundation (source branch)

On November 5, 2026, **81 application tests, 3 additional protocol tests and 4 Rust signer tests passed**. The installed/released 1.0.3 executable was not replaced. No desktop UI was operated.

Hardware checks for the new [library API](library-api.md):

| Target | Checked |
| --- | --- |
| iPhone 16 Pro / iOS 27.0 | Catalogue reads, genre/year edits, cover-only updates, single-track and album deletion over USB and Wi-Fi. JPEG originals were read back and compared byte for byte in the Wi-Fi artwork test. |
| iPhone worker API | The actual headless worker handled read/query/plan/apply/artwork over Wi-Fi, then removed its disposable fixture. Existing music and aggregate counts matched the baseline. |
| iPod touch 4 / iOS 6.1.6 | Catalogue reads over USB/Wi-Fi; genre/year edits and cover replacement/restoration on backed-up Lil Peep tracks; individual and album deletion on generated Lil Peep fixtures. |

iPhone tests used generated two-second ALAC tones in a disposable album. iPod tests first backed up all 46 Lil Peep audio files and the catalogue to a private folder outside Git. Original metadata and artwork were restored after edits; the generated album was removed and its audio paths checked absent. A new genre was applied to two Lil Peep tracks together and restored to the existing genre; all track metadata then matched the before snapshot. Unrelated music was compared before/after.

The checks caught and corrected three legacy-specific issues: existing genre names must reuse their native collection IDs, native signed/unsigned persistent IDs must be compared as 64-bit data, and StoreServices-installed songs live under `Purchases` rather than `iTunes_Control/Music`. On iPhone, empty unregistered Music scratch rows are excluded from the library view while genuine cloud and pending entries remain visible.

These checks cover the backend, not the future interface or a packaged library-management release. Large batches, other models/firmware, cloud-library mutation, playback/export and playlist editing are not validated by this work. Test audio, device captures, credentials and private journals are not included in the repository.

## Windows executable

The standalone 1.0.3 executable was checked on Windows 11 x64 from an empty working directory, with Python removed from PATH and fresh local settings. Empty and iPhone UI previews rendered offscreen. The frozen backend processed an empty queue without connecting to a device. Bundled FFmpeg 7.1 and the Rust signer ran successfully; the executable contains Python 3.13, blank device defaults and third-party notices. PE metadata confirms x64 and version 1.0.3.

The release ZIP passed its integrity check. These checks validate packaging on the development PC; they do not replace testing on a clean Windows installation.

```powershell
python tools/check_release.py path/to/iPods-Are-Beautiful-Windows-x64.exe path/to/check-output
```
