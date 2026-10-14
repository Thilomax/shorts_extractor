# Shorts Extractor

Turns a long video into vertical short-form clips (YouTube Shorts, Reels, TikTok). Whisper transcribes the video, Claude picks the moments worth clipping, and FFmpeg cuts and exports them at 1080×1920.

## Why I built this

I worked as a freelance video editor, and the slowest part of cutting shorts was scrubbing through long videos to find the few moments worth clipping. This tool does that first pass automatically: it reads the transcript, picks the strongest story inside the video, and exports a ready-to-post vertical clip. A built-in editor lets me fine-tune the cuts afterwards without running the AI again.

I built it with AI-assisted development. I designed the pipeline, wrote the editorial rules in the prompt, and tested and iterated on the output with real videos.

## How it works

```
video.mp4
   │
   ▼  FFmpeg: extract audio (16 kHz mono)
   │
   ▼  faster-whisper (runs locally): timestamped transcript
   │     GPU if available, falls back to CPU if the GPU run crashes
   │
   ▼  Claude: reads the transcript, picks segments, returns JSON
   │     Each transcript line gets an index like [42].
   │     Claude answers with line indices, never timestamps.
   │
   ▼  Python: validates the JSON, converts indices back to exact
   │     Whisper timestamps, trims any overlapping segments
   │
   ▼  FFmpeg: cut each segment, scale to 1080 wide with black bars
   │     top and bottom (1080×1920), encode H.264/AAC
   │
   ▼  FFmpeg concat → highlight_<timestamp>.mp4
```

**Why indices instead of timestamps:** if the model writes timestamps itself, it can invent times that don't exist or land in the middle of a word. Making it pick transcript lines by number means every cut starts and ends exactly on a sentence boundary that Whisper actually detected.

**How Claude chooses clips:** the prompt describes three roles from short-form editing:
- **Grab:** the single most striking moment, the reason someone stops scrolling.
- **Anchor:** the minimum context a stranger needs for the grab to make sense.
- **Payoff:** where the tension peaks or resolves.

Claude finds the grab first, then adds an anchor and payoff from the same story, and decides the playback order. Clips don't have to play in the order they appear in the video.

Transcripts longer than 150,000 characters are summarised in chunks before selection.

## Setup

**Requirements:** Python 3.10+, FFmpeg on your `PATH`, and an Anthropic API key.

| Platform | Install FFmpeg |
|----------|----------------|
| macOS    | `brew install ffmpeg` |
| Ubuntu   | `sudo apt install ffmpeg` |
| Windows  | Download from <https://www.gyan.dev/ffmpeg/builds/> and add `bin/` to `PATH` |

```bash
git clone https://github.com/Thilomax/shorts_extractor.git
cd shorts_extractor
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then add your Anthropic API key
```

Transcription runs locally, so no OpenAI key is needed.

## Usage

```bash
python extract_highlights.py <video> [options]
```

### Modes

| Mode | What it does | Uses Claude |
|------|--------------|-------------|
| `highlight` (default) | Picks the strongest story and stitches segments from it into one short | Yes |
| `split` | Cuts the whole video into sequential clips, ending each one on a cliffhanger | Yes |
| `ssplit` | Cuts the whole video into fixed-length clips by time only | No (no transcription either) |

Choose with `--mode <name>` or the shorthand `--highlight`, `--split`, `--ssplit`.

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--duration` | `75` | **highlight:** target total length in seconds. **split / ssplit:** max length per clip. |
| `--clips` | `3` | **highlight only:** number of segments stitched into one short. |
| `--variants` | `1` | **highlight only:** number of different shorts to make, each from a different story. All are chosen in one Claude call. |
| `--whisper-model` | `medium` | faster-whisper model: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3`. |
| `--output-dir` | auto | Defaults to a new folder next to the video, named `<date>_<time>_<mode>_<video name>`. |
| `--edit` | off | **highlight only:** open the browser editor after exporting. |

### Examples

```bash
# One highlight with default settings
python extract_highlights.py interview.mp4

# Three different highlights from the same video
python extract_highlights.py podcast.mp4 --variants 3

# Longer highlight from 4 segments, then fine-tune in the editor
python extract_highlights.py keynote.mp4 --clips 4 --duration 90 --edit

# Split into sub-60s clips that end on cliffhangers
python extract_highlights.py talk.mp4 --split --duration 58

# Split into 60s clips by time only (no API calls)
python extract_highlights.py talk.mp4 --ssplit --duration 60
```

## Editor (`--edit`)

After the highlight export finishes, a local editor opens in your browser (Flask, single-page HTML/JS, no build step).

- **Timeline:** gray blocks are transcript lines. Coloured blocks are the selected segments (orange = grab, blue = anchor, green = payoff).
- **Trim:** drag the handles at either end of a segment. With **Snap** on, edges snap to transcript boundaries so you never cut mid-word. Turn it off for frame-precise control.
- **Navigate:** scroll to zoom, drag to pan, click to seek.
- **Edit segments:** **+ Clip** adds a segment at the playhead, × deletes one, and clicking the role badge changes its role.
- **Check cuts:** double-click a segment to loop it, or press **Preview** to play only the selected segments in sequence.
- **Export:** re-cuts the video with your changes as `highlight_<timestamp>_edited.mp4`. It doesn't call Claude or Whisper again, so it takes a few seconds. The original export is always kept.
- With several variants, switch between them with the V1 / V2 / V3 tabs.

## Output format

- 1080×1920 (9:16). The full frame is scaled to 1080 px wide, with black bars top and bottom.
- H.264 video, AAC audio, MP4 container.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (not for `ssplit`) | Used for segment selection |
| `CLAUDE_MODEL` | No | Overrides the default Claude model |
