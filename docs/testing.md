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

### 1.1.0 preview

On November 5, 2026, **103 application tests passed**, including the new playlist plans, exact signed IDs, duplicate prevention, protected-playlist checks, verified capture caching and offscreen library interactions. Playlist create/add/repeated-add/rename/remove/clear/delete passed on iPod touch 4 and iPhone 16 Pro over USB; create/add/delete also passed over forced Wi-Fi on both. Tests used temporary playlists referencing existing local songs; all song records and unrelated playlists were checked unchanged, and temporary playlists were removed. No desktop UI was operated. Visual review is left to the designer.

Both devices passed the actual app worker flow: open library, create a playlist, select songs, use the picker, rename, cancel staged removal, save removal and delete the playlist. Legacy checks caught NSNumber conversion losing low bits of long song IDs; membership now uses a native decimal scanner and integer buffers. SpringBoard is preferred over suspended Music for legacy framework calls. Database files are reused only when before/after device checksums match; the volatile SHM index is rebuilt locally.

The pre-existing library hardware validation above covers album/song deletion, genre/year and artwork. The new interface connects those same backend operations. Clean-machine setup, Classic, other models and firmware remain untested.

### Published 1.0.3

The standalone 1.0.3 executable was checked on Windows 11 x64 from an empty working directory, with Python removed from PATH and fresh local settings. Empty and iPhone UI previews rendered offscreen. The frozen backend processed an empty queue without connecting to a device. Bundled FFmpeg 7.1 and the Rust signer ran successfully; the executable contains Python 3.13, blank device defaults and third-party notices. PE metadata confirms x64 and version 1.0.3.

The release ZIP passed its integrity check. These checks validate packaging on the development PC; they do not replace testing on a clean Windows installation.

### Compilation import and missing artwork — November 20, 2026

Compilation grouping checks matching album titles plus MusicBrainz release IDs, valid GTIN barcodes or label-qualified catalogue numbers. Explicit album artists are shared within a matching release; different track artists remain intact. Conflicting IDs or explicit album artists prevent automatic grouping. Original files are not retagged. Missing artwork uses Deezer with MusicBrainz / Cover Art Archive fallback; a tagged MusicBrainz release ID is tried directly first. Lookup results live only within the import batch, and an artwork miss does not fail the audio import.

The fourteen original FLAC files for The Needle Drop LP were checked against the generated native operations: one shared album identity and fourteen unchanged track artists. The existing fourteen iPhone tracks were regrouped through a signed native metadata session over USB. Their IDs, artwork, titles, artists, durations, stored paths and file sizes were preserved, and all fourteen stored files were checked. The other 349 song records and ordinary playlists were unchanged. Playback history gained a played song during the check. The compilation flag was confirmed on all fourteen tracks. No audio was deleted or uploaded again.

Regression checks cover matching and conflicting release IDs, guest artists, translated compilation labels, conversion metadata, source preservation, artwork fallback and cancellation. A live missing-cover lookup returned the 250-pixel Celestial Blues artwork; that network check did not write to a device. New missing-cover imports on hardware and compilation grouping on iPod have not been separately tested.

The final 1.1.0 source passed 142 application tests and 3 additional Music protocol tests. Its standalone executable passed the packaging checks above, including the frozen library catalogue and playlist-planning entry points. The signer implementation is unchanged.

### Background artwork repair — 1.1.1

The app checks connected iPhones while idle and after imports, one album per job. It updates only local, editable tracks that still lack artwork in a fresh capture. An existing cover from another song in the album is preferred; otherwise the existing online sources are used. Prepared download files are removed after verified completion. Inputs for an indeterminate native operation are retained for recovery, and that operation is not blindly repeated. Empty lookup results are skipped. Apple sync-lock contention defers the job.

The Day Is My Enemy by The Prodigy was missing artwork on eleven local iPhone songs. The repair path installed the online cover over paired Wi-Fi. The native mutation API checked the registered artwork IDs and downloaded the installed original bytes for every selected track. Song IDs, audio paths, file sizes, other metadata and unrelated song records were compared before/after. An initial busy Apple sync lock was left intact; a later fresh attempt succeeded.

Regression checks cover protected/cloud exclusions, preserving a cover added during lookup, cancellation, upload priority, large-album batching, partial-album cover reuse, input cleanup, uncertain-operation recovery and retaining the current library view after a background update. The first implementation targets the tested iPhone; automatic background repair on iPod is not enabled.

157 application tests and 3 additional protocol tests passed for 1.1.1. The real headless worker was also run against the already-repaired Prodigy album over Wi-Fi and correctly performed no second mutation.

```powershell
python tools/check_release.py path/to/iPods-Are-Beautiful-Windows-x64.exe path/to/check-output
```
