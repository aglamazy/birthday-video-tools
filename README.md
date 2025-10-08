# Photo Sequence Toolkit

Utilities for organising a raw photo dump into dated folders, generating a chronological `sequence/` directory, converting HEIC assets, and producing an MP4 slideshow with optional overlays.

## Project Layout

- `data/` – raw input photos/videos (initially unsorted)
- `sequence/` – flattened chronological copies (`YYYY-####.ext`)
- `photo_sorter.py` – moves recognisable files in `data/` into `data/YYYY/yyyymmdd-ii.ext`
- `sequence_builder.py` – copies the organised tree into the single `sequence/` folder
- `convert_heic.py` – converts/renames HEIC/HEIF files within `sequence/`
- `sequence_to_video.py` – renders the slideshow MP4 from `sequence/`

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

4. **Generate the slideshow video**
   ```bash
   # Quick preview (first 10 entries, verbose ffmpeg, and year labels)
   python sequence_to_video.py --limit 10 --label-year --verbose

   # Full render with overlays and custom font
   python sequence_to_video.py --label-year --label-font /path/to/YourFont.ttf
   ```

   - Text slides (`.txt`/`.pug`) in `sequence/` become full-frame cards.
   - Use `--label-year` to stamp detected years (from filenames like `20240119-01.jpg`).

## Text Slide Format

```
# Summer 1995
- Family trip to Paris
  - Louvre
  - Eiffel Tower
- Cousins reunion
```

Blank lines add spacing; leading `#` renders as a headline; indented bullets create nested lists.

## Tips

- Keep backups of the original `data/` before bulk moves.
- Use `--limit N` on `sequence_to_video.py` to experiment before running the full batch.
- If `--label-year` warns about a missing font, supply one via `--label-font`.

## License

MIT (see `LICENSE` if provided).
