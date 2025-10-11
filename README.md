# Photo Sequence Toolkit

Utilities for organising a raw photo dump into dated folders, generating a chronological `sequence/` directory, converting HEIC assets, producing an MP4 slideshow (with overlays + timed audio cues), and trimming audio clips for that workflow.

## Project Layout

- `data/` – raw input photos/videos (initially unsorted)
- `sequence/` – flattened chronological copies (`YYYY-####.ext`)
- `photo_sorter.py` – moves recognisable files in `data/` into `data/YYYY/yyyymmdd-ii.ext`
- `sequence_builder.py` – copies the organised tree into the single `sequence/` folder
- `convert_heic.py` – converts/renames HEIC/HEIF files within `sequence/`
- `sequence_to_video.py` – renders the slideshow MP4 (and optional timeline audio)
- `extract_audio.py` – trims audio out of a video file into an MP3 snippet
- `incremental_builder.py` – incremental slide renderer with cache + watcher
- `lib/` – shared helpers used by the renderer (`text_utils.py`, `text_renderer.py`, `subtitle_renderer.py`, `collage.py`)
- `find_duplicates.py` – detects duplicate or visually similar photos

## Recent Changes

- Slides that share a prefix (e.g., `1978-0001.jpg`, `1978-0001_2.jpg`) are combined into a padded collage for both full renders and incremental builds; matching text overlays drive captions for the entire group.
- Text overlays can include directives such as `@duration: 8` to keep a slide on screen for a fixed number of seconds.
- Output filenames default to `.mp4` if no extension is provided (`--output test` now produces `test.mp4`).

## Environment Setup

1. Install system dependencies:
   ```bash
   sudo apt update
   sudo apt install python3.12-venv ffmpeg libheif1 libheif-examples heif-gdk-pixbuf
   ```

2. Create and activate a virtual environment, then install Python helpers:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install pillow pillow-heif exifread piexif
   ```

3. (Optional) Install ImageMagick as a fallback HEIC converter:
   ```bash
   sudo apt install imagemagick
   ```

## Workflow

1. **Sort raw files into year folders**
   ```bash
   source .venv/bin/activate
   python photo_sorter.py --dry-run    # review first
   python photo_sorter.py              # moves matching files under data/YYYY
   ```

2. **Flatten into the chronological sequence**
   ```bash
   python sequence_builder.py --dry-run
   python sequence_builder.py          # copies to sequence/YYYY-####.ext
   ```

3. **Convert or rename HEIC files**
   ```bash
   python convert_heic.py --format jpg --remove-original
   # Add --dry-run to inspect without writing
   ```

4. **Trim supporting audio (optional)**
   ```bash
   # Remove 2 s of lead-in silence and export the full soundtrack
   python extract_audio.py raw_clip.mp4 --trim-start 2 -o cleaned_intro.mp3

   # Extract a 30 s highlight starting 10 s into the source
   python extract_audio.py raw_clip.mp4 30 --start 10 --trim-start 0.5 -o highlight.mp3
   ```

5. **Generate the slideshow video**
   ```bash
   # Quick preview (first 10 entries, verbose progress, and year labels)
   python sequence_to_video.py --limit 10 --label-year --verbose

   # Full render with text overlays, custom font, and timeline audio cues
   python sequence_to_video.py --label-year --label-font /path/to/YourFont.ttf
   ```

   - Place `.txt`/`.pug` files beside images to create full slides or overlays.
   - Drop `.mp3` (or `.wav`/`.m4a`) files alongside the matching stem (e.g., `001.jpg` + `001.mp3`). The audio plays from that slide until the next audio file and crossfades over the transition.
   - Use `--audio-file` to append a global soundtrack instead of (or alongside) timeline cues.

## Text Slide Format

```
# Summer 1995
- Family trip to Paris
  - Louvre
  - Eiffel Tower
- Cousins reunion
```

Blank lines add spacing; leading `#` renders as a headline; indented bullets create nested lists.

Optional directives can sit at the very top of the file. For example:

```
@duration: 8
# Surprise Party
- Decorations by Dana
- Cake by Amir
```

`@duration: 8` keeps that slide visible for eight seconds in both the full renderer and the incremental builder.

## Script Reference

### `photo_sorter.py`

```
python photo_sorter.py [--dry-run] [--source data] [--dest data]
```
- Parses EXIF date/captured timestamps and renames/moves recognised files into `dest/YYYY/yyyymmdd-##.ext`.
- Use `--dry-run` to preview operations.

### `sequence_builder.py`

```
python sequence_builder.py [--dry-run] [--source data] [--dest sequence]
```
- Copies the organised tree (recursively) into a single flattened `sequence/NNN.ext` directory ordered by date/time.

### `convert_heic.py`

```
python convert_heic.py [--format jpg] [--remove-original] [--dry-run]
```
- Converts HEIC/HEIF assets in place (using Pillow/HEIF or ImageMagick fallback) and optionally cleans up originals.

### `extract_audio.py`

```
python extract_audio.py SOURCE [DURATION]
                        [--start OFFSET]
                        [--trim-start TRIM]
                        [--output OUTPUT]
                        [--overwrite]
```
- Pulls an MP3 clip out of a video file. Omit `DURATION` to export the entire track.
- `--trim-start` removes leading silence before encoding; combine with `--start` to jump to a position and trim additional padding.

### `sequence_to_video.py`

```
python sequence_to_video.py [--source-dir sequence]
                            [--output slideshow.mp4]
                            [--limit N] [--fps 30]
                            [--duration D] [--duration-overlay D] [--duration-text D]
                            [--label-year] [--label-font FONT]
                            [--debug-filename]
                            [--start-at FILENAME]
                            [--chunk-size N] [--chunk-index M] [--batch]
                            [--audio-file track.mp3 ...]
                            [--verbose] [--debug-ffmpeg]
```
- Builds MP4 segments for images, videos, and text slides, merges them, and (when audio cues exist) writes
  - `slideshow.mp4` – video-only output
  - `slideshow_audio.mp3` – timeline soundtrack (with automatic crossfades)
  - `slideshow_with_audio.mp4` – muxed final video
- `--limit` is invaluable for quick spot checks.
- `--keep-temp` retains intermediate `segment_*.mp4` + subtitle PNGs for debugging.
- `--start-at` jumps directly to the first file matching a given name (useful for resuming long renders).
- `--debug-filename` overlays each slide's original filename in the top-left corner—handy when auditing sequences.
- Audio cues: add `NNN.mp3` alongside `NNN.jpg` (or `.mp4`). Each cue plays until the next cue and crossfades over the transition.
- Global mixes: pass `--audio-file background.mp3` to append a traditional soundtrack instead of per-slide cues.
- Use `--chunk-size 120 --batch` to render every 120-item slice automatically (outputs `slideshow-1.mp4`, `slideshow-2.mp4`, ...). For a single slice, combine `--chunk-size` with `--chunk-index`.
- Tweak typography via `title_font_size` / `body_font_size` in `config.json`.
- Slides sharing a prefix (`1978-0001.jpg`, `1978-0001_2.jpg`, …) are combined into a padded collage; matching `.txt` overlays annotate the entire group and can add directives such as `@duration: 8` to keep the slide visible longer.

### `find_duplicates.py`

```
python find_duplicates.py [root] [--follow-links] [--min-size BYTES] [--deep] [--hamming N]
```
- Walks the directory (default `sequence/`), hashes each file, and prints groups with identical byte signatures.
- `--min-size` helps skip thumbnails; `--follow-links` inspects symlinked trees; adding `--deep` computes a perceptual hash to flag visually similar images within the chosen Hamming threshold (`--hamming`, default 5).

## Tips

- Keep backups of `data/` before bulk moves.
- Use `extract_audio.py` to clean lead-in silence before dropping tracks into `sequence/`.
- Run `sequence_to_video.py --verbose` for concise `[idx/total]` progress, and `--debug-ffmpeg` when you need the exact command lines.
- If `--label-year` warns about a missing font, point to a suitable `.ttf`/`.otf` via `--label-font`.
- When a slide needs more time, add `@duration: N` (seconds) to the top of its `.txt` overlay; both renderers respect the override.

## License

MIT (see `LICENSE` if provided).
- Walks the directory (default `sequence/`), hashes each file, and prints groups with identical byte signatures.
- `--min-size` helps skip thumbnails; `--follow-links` inspects symlinked trees; adding `--deep` computes a perceptual hash to flag visually similar images within the chosen Hamming threshold (`--hamming`, default 5).

### `incremental_builder.py`

```
python incremental_builder.py [--segments-dir segments]
                              [--output slideshow.mp4]
                              [--limit N] [--verbose] [--force]
                              [--watch] [--interval 1.0]
```
- Renders each slide into `segments/segment_XXXX.mp4`, reusing cached segments when sources are unchanged. Image groups (`prefix_*.jpg`) are combined into collages before rendering so they mirror the main slideshow output.
- After updating the necessary segments, emits a versioned MP4 (e.g., `slideshow-001.mp4`) and attaches any audio tracks listed in `config.json`.
- Use `--force` to rebuild everything, or simply edit media/text files and rerun for incremental updates.
- Add `--watch` to keep rebuilding automatically when `sequence/`, `config.json`, or configured audio tracks change (requires the `watchfiles` package for event-driven mode; falls back to polling otherwise).
  - Install with `python -m pip install watchfiles` (optional).
- Text overlays honour the same `@duration: N` directive as the main renderer.

### `watch_incremental.py`

```
python watch_incremental.py [--interval 1.0] [--sequence-dir sequence] -- [builder args]
```
- Polls `sequence/` for changes and reruns `incremental_builder.py` with the provided arguments whenever files change (useful if you prefer to keep the watcher separate or don’t have `watchfiles` installed).
- Example: `python watch_incremental.py -- --limit 20 --verbose`.
