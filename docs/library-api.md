# Library backend

Source-only foundation for the next interface. It does not add controls or change the installed 1.0.3 release.

## Scope

- Browse albums and their tracks, search and sort the captured catalogue.
- Delete selected tracks or every track in selected albums.
- Replace artwork without uploading the audio again.
- Set genre and release year for tracks or albums.

Audio export, playback, playlist editing, album/artist renaming and full release dates are outside this feature. Internal file-copy helpers are retained for hardware-test backups; there is no public export command.

The tested targets remain jailbroken iPod touch 4 / iOS 6.1.6 and paired iPhone 16 Pro / iOS 27.0. Cloud-only, protected or unfinished entries can be shown but cannot be edited by this backend. Other models/firmware are not validated.

## UI contract

Use `backend_client.run_backend` from the UI's background worker. It starts the existing headless child process; none of the library modules initialize Qt. The device config is the same config already used for uploads.

```python
snapshot = run_backend('library.read', config)
albums = run_backend('library.query', config, payload={
    'snapshot_id': snapshot['snapshot_id'], 'kind': 'albums',
    'offset': 0, 'limit': 100,
})
tracks = run_backend('library.query', config, payload={
    'snapshot_id': snapshot['snapshot_id'], 'album_id': album_id,
    'sort': 'track_number',
})
```

`library.read` returns `snapshot_id`, `revision`, device/backend identity, capture time, capabilities and counts (albums, artists, tracks, local tracks, bytes and duration). A snapshot describes one device and one library revision. Queries use the cached snapshot and do not reconnect.

Album rows include `id`, `title`, `album_artist`, `track_count`, `local_count`, `size_bytes`, `duration_ms`, `genre`, `year`, `metadata_mixed`, `has_artwork` and `artwork_track_id`. A mixed genre/year is `null`, with the corresponding `metadata_mixed` flag set; an unknown year is `0`.

Track rows include exact string IDs, title, artist, album, genre, year, disc/track numbers, duration, size, availability, origin and artwork presence. `can_plan_delete` indicates an identified local entry suitable for a change; combine it with the action's device capability. Device paths, native wire IDs and audio-export flags are not exposed to table rows.

All persistent IDs are strings, including when they fit into a JavaScript number. Album/artist IDs are opaque strings. Never parse them as floating-point numbers.

Queries accept `search`, `offset`, `limit` (1–500), `sort` and `descending`. `kind` defaults to `tracks`; `albums` and `artists` provide grouped views. Use `album_id` or `artist_id` to filter tracks.

```python
cover = run_backend('library.artwork', config, payload={
    'snapshot_id': snapshot['snapshot_id'], 'track_id': artwork_track_id,
})
```

Artwork returns a cached local image `path`, or `null` when unavailable. Fetch lazily for visible albums. Touch previews are rendered by the device's MediaPlayer framework; iPhone previews read the native local artwork original. This command does not download cloud images.

## Changes

Prepare one action against the displayed snapshot, then apply its opaque `plan_id`:

```python
proposal = run_backend('library.plan', config, payload={
    'snapshot_id': snapshot['snapshot_id'],
    'action': 'update_metadata',
    'album_ids': [album_id],
    'changes': {'genre': 'Doom Metal', 'year': 2021},
})
result = run_backend('library.apply', config,
    payload={'plan_id': proposal['plan_id']}, emit=on_progress)
```

Actions:

| Action | Parameters |
| --- | --- |
| `delete_tracks` | `track_ids` and/or `album_ids` |
| `update_metadata` | Selection plus `changes`: nonempty `genre`, integer `year` from 0 to 9999; 0 clears the year |
| `replace_artwork` | Selection plus absolute local `cover_path` |

An album selection expands to all of its tracks. A mixed selection containing cloud, unfinished or unidentified tracks is refused as a whole. A session is limited to 499 tracks; larger changes must be prepared separately against each returned fresh snapshot.

Cover inputs are validated, flattened to RGB JPEG and resized to fit 1024 × 1024. Source files on the PC are unchanged. The plan holds an immutable cached image and verifies its hash before submission.

Plans report the action, exact selection, track count, deletion size, `executable` and known side effects. Native payloads, image cache details and signatures stay in the backend. The planner does not change the device.

`library.apply` reconnects, checks the device/library/revision, submits once, rereads the catalogue and checks both selected and unrelated tracks. Successful results contain a **new** snapshot summary, `action`, `changed` and `verified: true`. Refresh the UI from that new snapshot; earlier selections become stale.

Album deletion uses individual native track deletions. The iPhone receiver does not reliably implement a direct album-delete operation. Artwork changes use the native cover-only asset exchange; audio is not reimported. The touch backend uses MediaPlayer/MediaLibrary methods and preserves integer IDs through native argument buffers. Neither backend writes SQL to a device database.

## Execution and errors

- Imports and library device operations share a process-wide per-device lock. Cache queries remain available while a transfer runs. Do not start a second operation after `device_busy`.
- Progress events have `type: library_change`, `stage`, and when available `completed`/`total`. Selected tracks are counted once; completion means catalogue verification passed.
- A best-effort `connection` event refreshes the device's free/total storage after a verified change. A failed storage refresh does not undo a successful library change.
- Cancellation is checked before submission. An already submitted native change finishes and verifies before exiting; the child is not forcibly killed.
- A plan is single-use. Its receipt is created before transport/submission. Connection loss or a failed verification requires a refresh and a newly prepared plan; never automatically retry the old plan.
- Failures return `error`, `code` and `details`, as other worker commands do. Important codes: `device_busy`, `stale_snapshot`, `read_only_tracks`, `unsupported_schema`, `plan_already_submitted`, `artwork_changed`, `change_not_verified`.
- `change_not_verified` includes `refresh_required: true` and an underlying `cause` when available. It does not mean the operation was rolled back: the device may already have applied it.

Captures, cached images and operation receipts stay under `%LOCALAPPDATA%\iMetal`. They contain private library metadata and must not be committed or included in public diagnostics. Audio test backups are kept outside the repository.

The iPhone receiver may reset **recent** play/skip counters during a metadata session; pinned rustunes research observed this while lifetime counters and ratings remained intact. The plan reports `possible_recent_play_counter_reset`. This feature does not edit listening statistics or attempt to restore them.

## Validation

Hardware tests use disposable generated albums on iPhone and backed-up Lil Peep material on iPod. Test fixtures are removed afterwards; unrelated music is compared before/after. Detailed results are summarized in [testing.md](testing.md).

The interface, integration into a packaged release, other devices/firmware and large-library stress behaviour are not finished or claimed by this foundation.
