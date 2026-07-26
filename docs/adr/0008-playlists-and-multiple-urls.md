# 0008. Playlists expand; several videos need a directory

- Status: Accepted
- Date: 2026-07-26

## Context

The tool exists to build a directory of knowledge files, and the unit people
actually have is a playlist. Before this, a playlist URL reached `pick_track`
with no `subtitles` key and failed with "no English captions (manual or auto)" —
technically true of a playlist, and completely misleading.

Two things then need deciding.

**What counts as a playlist URL.** YouTube's share button appends `&list=` to the
video you are watching, so `watch?v=X&list=Y` is overwhelmingly "this video",
not "these two hundred videos". Treating `list=` as decisive would turn a
one-video request into a two-hundred-video download.

**Where the output goes.** With one video, `-o` is a filename. With twelve, a
filename means eleven of them are silently overwritten by the twelfth.

## Decision

**A `v=` id wins over `list=`.** `is_playlist_url` treats a URL as a playlist only
when it has no video id: `/playlist`, `/channel/`, `/@handle`, `/c/`, `/user/`.
`probe` additionally passes `noplaylist=True` so yt-dlp resolves an ambiguous URL
the same way. This matches `yt-dlp --no-playlist`.

**Playlists expand flat.** `expand()` uses `extract_flat="in_playlist"`, so a
200-video playlist costs one request instead of 200 — each video is probed later
anyway, and only if the run gets that far. Entries that come back `None`
(deleted, private) are skipped rather than fatal.

**`-o` changes meaning with arity.** One video: `-o` is the output file, exactly
as before. Several: `-o` is a directory, created if needed, and each video is
written as `<title-slug>-<id>.md`. `-o -` still writes to stdout, and several
JSON documents become one array — because concatenated JSON objects are not JSON,
while a lone transcript piped to `jq` must not arrive wrapped in a 1-element
array.

## Consequences

- `transkrp <playlist-url> -o ./notes/` is the thing the tool was for.
- The single-URL contract is unchanged, including `-o file.md` and `-o -`.
- `-o` meaning two things is a real wart. The alternative — a separate
  `--out-dir` — means two flags to explain and a third error case when both are
  given. Arity is unambiguous at the point of use.
- A run of N videos sleeps 1s between them; see
  [0007](0007-failure-handling.md) for why.
- A channel URL (`/@handle/videos`) expands too. That can be thousands of videos
  and will be rate-limited long before it finishes. It is not prevented — the
  user asked — but it is not advertised in `--help` either.
- Two videos with the same title and id would collide. They can't: the id is in
  the filename.
