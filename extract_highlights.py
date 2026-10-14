#!/usr/bin/env python3
"""
Video Highlight Extractor
Transcribes a video with Whisper, then either:
  highlight  — Claude picks the most viral-worthy segments and stitches them
               into a single vertical highlight.mp4
  split      — cuts the full video into sequential sub-60s vertical clips
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Claude model used for segment selection. Override with CLAUDE_MODEL in .env.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


# ─────────────────────────── helpers ────────────────────────────────────────

def check_ffmpeg() -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError:
        print(
            "\n[ERROR] FFmpeg is not installed or not on your PATH.\n"
            "Install it from https://ffmpeg.org/download.html\n"
            "  • macOS:   brew install ffmpeg\n"
            "  • Ubuntu:  sudo apt install ffmpeg\n"
            "  • Windows: https://www.gyan.dev/ffmpeg/builds/ (add to PATH)\n"
        )
        sys.exit(1)


def seconds_to_hms(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def get_anthropic_client():
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ERROR] ANTHROPIC_API_KEY not set in .env or environment.")
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


def default_output_dir(video_path: str, mode: str) -> Path:
    """Each run gets its own folder next to the source video.
    Folder starts with date+time so renders sort chronologically,
    then mode, then a short version of the video title for reference."""
    p = Path(video_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Truncate long video names so the folder stays readable
    stem = p.stem[:40].rstrip() if len(p.stem) > 40 else p.stem
    return p.parent / f"{ts}_{mode}_{stem}"


# ─────────────────────────── transcription ──────────────────────────────────

def extract_audio(video_path: str, audio_path: str) -> None:
    """Extract audio as a 16 kHz mono MP3 (keeps file well under Whisper's 25 MB limit)."""
    print(f"[1/?] Extracting audio from '{Path(video_path).name}' …")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-q:a", "4",
        audio_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print("[ERROR] FFmpeg failed to extract audio:")
        print(result.stderr.decode())
        sys.exit(1)


def _gpu_transcribe_worker(audio_path: str, model: str, output_path: str) -> None:
    """Runs GPU transcription in a child process and writes JSON result to output_path.
    Must be a module-level function for multiprocessing 'spawn' on Windows.
    If ctranslate2 crashes at the C level (abort/segfault), only this child dies."""
    from faster_whisper import WhisperModel
    wm = WhisperModel(model, device="cuda", compute_type="int8_float16")
    raw, info = wm.transcribe(audio_path, beam_size=5)
    segs = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in raw]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"segments": segs, "language": info.language}, f)


def transcribe(audio_path: str, model: str = "medium") -> list[dict]:
    """Return Whisper segments: [{"start": float, "end": float, "text": str}]
    Uses faster-whisper locally (free). Install with: pip install faster-whisper
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "\n[ERROR] faster-whisper is not installed.\n"
            "        Run:  pip install faster-whisper\n"
        )
        sys.exit(1)

    def _run_cpu():
        wm = WhisperModel(model, device="cpu", compute_type="int8")
        raw, info = wm.transcribe(audio_path, beam_size=5)
        segs = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in raw]
        return segs, info.language

    try:
        import ctranslate2
        cuda_ok = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda_ok = False

    language = "unknown"

    if cuda_ok:
        import multiprocessing as mp
        print(f"[2/?] Transcribing with local Whisper ({model}, GPU) …")
        result_path = audio_path + ".result.json"
        try:
            ctx = mp.get_context("spawn")
            p = ctx.Process(target=_gpu_transcribe_worker, args=(audio_path, model, result_path))
            p.start()
            p.join()
            if p.exitcode == 0 and Path(result_path).exists():
                with open(result_path, encoding="utf-8") as f:
                    data = json.load(f)
                segments = data["segments"]
                language = data["language"]
            else:
                print(f"    → GPU process crashed (exit {p.exitcode}), retrying on CPU …")
                segments, language = _run_cpu()
        finally:
            Path(result_path).unlink(missing_ok=True)
    else:
        print(f"[2/?] Transcribing with local Whisper ({model}, CPU) …")
        segments, language = _run_cpu()

    total = segments[-1]["end"] if segments else 0
    print(f"    → {len(segments)} segments, {total:.1f}s total  (lang: {language})")
    return segments


# ─────────────────────────── highlight mode: Claude selection ───────────────

MAX_TRANSCRIPT_CHARS = 150_000  # ~37k tokens — covers most videos without summarisation


def build_transcript_text(segments: list[dict]) -> str:
    """Each line is prefixed with a numeric index [N] so Claude can reference
    segments by index rather than by timestamp value."""
    return "\n".join(
        f"[{i}] [{seconds_to_hms(s['start'])} → {seconds_to_hms(s['end'])}] {s['text']}"
        for i, s in enumerate(segments)
    )


def chunk_transcript(segments: list[dict], max_chars: int) -> list[str]:
    chunks, current, length = [], [], 0
    for seg in segments:
        line = f"[{seconds_to_hms(seg['start'])} → {seconds_to_hms(seg['end'])}] {seg['text']}\n"
        if length + len(line) > max_chars and current:
            chunks.append("".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def summarize_chunk(client, chunk: str, chunk_index: int, total_chunks: int) -> str:
    """Ask Claude to condense a long chunk into a shorter timestamped summary."""
    print(f"    → Summarizing chunk {chunk_index}/{total_chunks} …")
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                "Below is part of a timestamped video transcript. "
                "Summarize it concisely, preserving the timestamps of the "
                "most important moments and keeping the text under 800 words.\n\n"
                f"{chunk}"
            ),
        }],
    )
    return response.content[0].text


def select_segments(
    segments: list[dict],
    n_clips: int,
    total_duration: int,
    n_variants: int = 1,
) -> list[list[dict]]:
    """
    Use Claude to pick highlight segments.
    Always returns a list of variants: list[list[dict]]
    Each inner list is one highlight sequence in playback order.
    When n_variants == 1, the outer list has exactly one entry (the main highlight).
    """
    client = get_anthropic_client()

    if n_variants == 1:
        print(f"[3/?] Asking Claude to find a {total_duration}s viral story arc …")
    else:
        print(f"[3/?] Asking Claude to find {n_variants} variants ({total_duration}s each) in one pass …")

    transcript_text = build_transcript_text(segments)

    if len(transcript_text) > MAX_TRANSCRIPT_CHARS:
        print(f"    → Transcript is long ({len(transcript_text):,} chars). Summarizing in chunks …")
        chunks = chunk_transcript(segments, MAX_TRANSCRIPT_CHARS)
        summarized = [
            summarize_chunk(client, c, i + 1, len(chunks))
            for i, c in enumerate(chunks)
        ]
        transcript_text = "\n\n---\n\n".join(summarized)
        print(f"    → Condensed to {len(transcript_text):,} chars")

    video_duration = segments[-1]["end"] if segments else total_duration
    per_segment = total_duration // n_clips

    # ── shared editorial brief (same rules for every variant) ──────────────
    editorial_brief = f"""You are a short-form video editor. You cut long YouTube videos into Shorts and Reels. Your job is not to summarise — it is to find the single most compelling moment in the video and build the tightest possible clip around it.

The source video is {video_duration:.1f} seconds long. Its full timestamped transcript is below.

─── THE THREE ROLES ───

Short-form clips are built around three functional roles. These are roles, not a fixed per-clip requirement — with {n_clips} clips, some roles will get more than one clip. Assign each clip the role that describes its function.

GRAB — the moment the short is built around. The single most visceral, surprising, specific, or funny thing in the transcript. A stranger sees this and stops scrolling. It can come from anywhere in the video. You find this first, before thinking about structure.

  Good GRABs:
  • A specific claim with real scale: "The ferry holds 1,200 cars — it's longer than the Eiffel Tower is tall"
  • Someone visibly panicking, overwhelmed, or in disbelief as it's happening — not recounting it afterward
  • A punchline, reveal, or unexpected turn that lands in one sentence
  • Something going visibly wrong or surprisingly right, in real time
  • A number, comparison, or fact that immediately reframes what the viewer thought they knew

  Bad GRABs — these will kill the short:
  • Introductions: "Today we're visiting...", "Hi guys, welcome back to the channel"
  • Reflections after the fact: "It was honestly the most terrifying thing I've ever done", "Looking back now..."
  • Vague stakes: "I really wanted to push myself on this one", "This whole experience really meant a lot to me"
  • Any moment that only lands because the viewer has already watched 10 minutes of this video

ANCHOR — the minimum context a total stranger needs for the GRAB to land. Not a summary. Not an intro. The specific moment where the stakes, situation, or person become clear in the fewest words. If you removed the ANCHOR, would a stranger watching the GRAB think "wait, who is this and why should I care?" — if yes, you need an ANCHOR. Keep it as short as possible.

  Good ANCHORs:
  • "My girlfriend is terrified of water. I've booked us onto the world's biggest ferry." — 8 seconds, job done
  • "We have one day to paint a full portrait. I've never painted a portrait before in my life."
  • "This guy sold his house to fund this pitch. He's about to find out if it worked."
  • A podcast guest: "We lost everything — $4 million, gone in one afternoon — because of one decision."

  The ANCHOR does not have to come from the beginning of the video. It just needs to be wherever the stakes are stated most clearly and most concisely.

PAYOFF — where the tension peaks, the question closes, or the experience reaches its sharpest point. Not a reflection or summary after the fact — the actual moment of maximum stakes. The reaction, the reveal, the in-the-moment peak. The viewer should feel like they witnessed something, not heard a summary of it.

─── HOW TO SELECT CLIPS: 4 STEPS ───

STEP 1 — FIND THE GRAB.
Read the full transcript. What is the single most arresting moment? Not the most interesting topic — the most specific, visceral, in-the-moment thing that happens. Write it as a concrete sentence before touching any index numbers: "At index [N], [person] [specific thing that happens]." If you cannot be that specific, you haven't found the GRAB yet.

STEP 2 — CHECK FOR CONTEXT DEBT.
Would a total stranger care about this GRAB, or does it only land because of buildup they don't have? If a stranger watches the GRAB and thinks "why should I care?" — you have context debt. Find the shortest ANCHOR that resolves it. If no ANCHOR exists that makes the GRAB land, find a different GRAB whose stakes are self-contained.

STEP 3 — FIND THE PAYOFF.
Where does the tension actually peak or resolve in the transcript? Is the experience shown happening, or only described after the fact? If it's only described in retrospect ("and then it all worked out"), find a different GRAB — one whose actual moment is in the transcript.

STEP 4 — DECIDE THE PLAYBACK ORDER.
Clips do NOT need to be in the same order they appear in the video. The array order you return IS the playback order. Choose whichever order makes the short land hardest:
  • ANCHOR → GRAB → PAYOFF: most common. Stakes first, then the moment, then the peak. Works for most challenge, vlog, and interview content.
  • GRAB → ANCHOR → PAYOFF: in medias res. Drop the viewer into the action first, then give context. Works when the GRAB is so immediately compelling that it earns the context — the stranger thinks "wait, what is happening?" and stays to find out.

─── WHAT KILLS SHORTS ───

❌ Context debt — three clips all clustered around the dramatic climax of a long video, with no ANCHOR.
✅ Ferry video: ANCHOR ("she's terrified of boats, we're taking the world's biggest one") + GRAB (her face when she first sees the scale) + PAYOFF (the moment she realises she's already on it and there's no way off). ANCHOR from minute 1, GRAB and PAYOFF from minute 18 — that's fine.

❌ Reflections instead of moments — "It was one of the scariest things I've ever done" / "Looking back, I can't believe we pulled it off"
✅ The moment of fear or success as it is happening. Past tense is usually dead. Present tense is usually alive.

❌ Padding — a clip whose only function is to get from A to B, with no moment of its own
✅ Every clip must have at least one beat — a reaction, a reveal, a line — that earns its place independently

❌ Outros and sign-offs — "Thanks so much for watching", "Let me know in the comments", "If you want to follow along..."
✅ Cut before any of this. The short ends at the sharpest possible endpoint.

─── HARD RULES ───
• Do NOT default to starting at the beginning of the video. The intro is almost always the weakest part. Only use early content if it contains the ANCHOR.
• start_index must be where the relevant scene or exchange BEGINS — not mid-conversation. Ask: if the viewer starts watching at start_index, do they have everything they need to follow this clip? If not, go back further.
• Every clip must end at the end of a complete sentence. Never cut mid-word or mid-thought.
• With {n_clips} clips: use at least one of each role (grab, anchor, payoff). Assign the extra clip(s) to whichever role the specific story needs more of.
• Clips must not overlap in time with each other.
• Every clip must be causally connected to the others — a stranger who watches them in sequence should follow the story without confusion.

─── TIMING ───
• Each segment: roughly {per_segment} seconds (±20s acceptable)
• Combined total per variant: {total_duration - 15}–{total_duration + 15} seconds
• Timestamps must be within [0, {video_duration:.1f}]"""

    if n_variants == 1:
        # ── single-variant prompt: returns a flat array ─────────────────────
        prompt = editorial_brief + f"""

TRANSCRIPT:
{transcript_text}

IMPORTANT — HOW TO REFERENCE SEGMENTS:
Each transcript line starts with a numeric index like [42]. Use these index numbers to identify segments.
Do NOT invent or copy timestamp values. Only use the index numbers.

Return ONLY a valid JSON array with exactly {n_clips} object(s). No markdown, no explanation, no code fences — just the raw JSON array.

Each object must have exactly these keys:
  "story": string (one sentence naming the specific story ALL clips in this sequence are from — same value for every object)
  "start_index": integer (the [N] index of the first transcript line to include)
  "end_index": integer (the [N] index of the last transcript line to include)
  "role": string (one of: "grab", "anchor", "payoff")
  "reason": string (1-2 plain sentences: what is literally happening and why a viewer keeps watching — no analysis, no invented meaning)

The array order is the playback order. If you want ANCHOR to play before GRAB even though GRAB comes earlier in the video, put ANCHOR first in the array.

Example format:
[{{"story": "They discover the cabin is tiny and spend the whole segment joking about the lack of space.", "start_index": 12, "end_index": 18, "role": "anchor", "reason": "Sets up who they are and what they booked. Short and punchy."}}, {{"story": "They discover the cabin is tiny and spend the whole segment joking about the lack of space.", "start_index": 42, "end_index": 55, "role": "grab", "reason": "Walking in and immediately reacting to the size. The disbelief is immediate and funny."}}]"""

    else:
        # ── multi-variant prompt: returns an array of arrays ─────────────────
        variant_descriptions = "\n".join(
            f"  Variant {i}: {desc}" for i, desc in enumerate([
                "THE BEST STORY — find the single most compelling self-contained story or exchange in the entire video and apply the two-step process with maximum rigour.",
                "A DIFFERENT STORY — find a completely different self-contained story or exchange from the transcript. Different subject, different scene, different event. Must follow the same two-step process: commit to one story, then pick segments from within it only.",
                "A THIRD STORY — another distinct self-contained story or exchange not covered by V1 or V2. Could be a different emotional register, a different topic, or a quieter moment that's actually compelling.",
                "A FOURTH STORY — dig into the transcript for a fourth distinct story or exchange. Something V1–V3 didn't touch.",
                "A FIFTH STORY — the most unexpected or niche self-contained story you can find. The exchange that seems minor but is actually the most interesting thing in the video.",
            ][:n_variants], 1)
        )

        prompt = editorial_brief + f"""

─── YOUR OUTPUT: {n_variants} DISTINCT VARIANTS ───

Each variant must follow the STEP A → STEP B process independently. For each variant: choose a story first, then pick segments from within that story.

{variant_descriptions}

DIVERSITY RULES:
• Each variant must be built around a DIFFERENT self-contained story or exchange from the transcript.
• No two variants may share more than one segment.
• "Different" means different events, conversations, or scenes — not just different timestamps within the same story.
• The goal: someone who watches all {n_variants} should feel they've seen {n_variants} different stories from the same source video.

TRANSCRIPT:
{transcript_text}

IMPORTANT — HOW TO REFERENCE SEGMENTS:
Each transcript line starts with a numeric index like [42]. Use these index numbers to identify segments.
Do NOT invent or copy timestamp values. Only use the index numbers.

Return ONLY a valid JSON array of exactly {n_variants} arrays. Each inner array has exactly {n_clips} segment objects. No markdown, no explanation, no code fences — just the raw JSON.

Each segment object must have exactly these keys:
  "story": string (one sentence naming the specific story — same value for every object in the same variant)
  "start_index": integer (the [N] index of the first transcript line to include)
  "end_index": integer (the [N] index of the last transcript line to include)
  "role": string (one of: "grab", "anchor", "payoff")
  "reason": string (1-2 plain sentences: what is literally happening and why a viewer keeps watching — no analysis, no invented meaning)

Array order within each variant is the playback order — not necessarily chronological.

Example for {n_variants} variants with {n_clips} segment(s) each:
[[{{"story": "The cabin tour where they discover the tiny space.", "start_index": 12, "end_index": 18, "role": "anchor", "reason": "..."}}], [{{"story": "The argument about whether to book the expensive restaurant.", "start_index": 110, "end_index": 130, "role": "grab", "reason": "..."}}]]"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512 + (n_variants * n_clips * 256),  # scale with output size
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Strip any prose preamble before the JSON array of objects
    bracket = raw.find("[{")
    if bracket > 0:
        raw = raw[bracket:]

    n_segs = len(segments)

    def validate_and_resolve(seq: list, label: str) -> list:
        """Validate index-based items and convert to start_seconds/end_seconds."""
        if not isinstance(seq, list):
            raise ValueError(f"{label}: expected a list of segment objects.")
        resolved = []
        stories = []
        for item in seq:
            for key in ("start_index", "end_index", "role", "reason"):
                if key not in item:
                    raise ValueError(f"{label}: missing key '{key}'.")
            si = int(item["start_index"])
            ei = int(item["end_index"])
            if not (0 <= si < n_segs and 0 <= ei < n_segs):
                raise ValueError(f"{label}: index out of range (start={si}, end={ei}, max={n_segs-1}).")
            if ei < si:
                raise ValueError(f"{label}: end_index {ei} is before start_index {si}.")
            if "story" in item:
                stories.append(item["story"].strip().lower())
            resolved.append({
                **item,
                "start_seconds": segments[si]["start"],
                "end_seconds":   segments[ei]["end"],
            })

        # Warn if segments claim to be from different stories
        if len(set(stories)) > 1:
            print(f"    [WARNING] {label}: segments reference different stories — Claude may have drifted:")
            for j, s in enumerate(stories, 1):
                print(f"      seg {j}: \"{s}\"")

        # Detect overlapping segments across all pairs (clips may be non-chronological)
        for j in range(len(resolved)):
            for k in range(j + 1, len(resolved)):
                a, b = resolved[j], resolved[k]
                if a["start_seconds"] < b["end_seconds"] and b["start_seconds"] < a["end_seconds"]:
                    overlap = min(a["end_seconds"], b["end_seconds"]) - max(a["start_seconds"], b["start_seconds"])
                    print(f"    [WARNING] {label} clips {j+1} and {k+1} overlap by {overlap:.2f}s — trimming clip {k+1}")
                    resolved[k]["start_seconds"] = resolved[j]["end_seconds"]

        return resolved

    try:
        parsed = json.loads(raw)

        if n_variants == 1:
            resolved_seq = validate_and_resolve(parsed, "Variant 1")
            return [resolved_seq]
        else:
            if not isinstance(parsed, list) or len(parsed) != n_variants:
                raise ValueError(
                    f"Expected outer array of {n_variants} variants, "
                    f"got {type(parsed).__name__} len={len(parsed) if isinstance(parsed, list) else '?'}."
                )
            return [validate_and_resolve(seq, f"Variant {i}") for i, seq in enumerate(parsed, 1)]

    except (json.JSONDecodeError, ValueError) as exc:
        print(f"\n[ERROR] Failed to parse Claude's response as JSON: {exc}")
        print("─── Raw Claude response ───")
        print(raw)
        print("───────────────────────────")
        sys.exit(1)


# ─────────────────────────── split mode ─────────────────────────────────────

def select_split_points(
    segments: list[dict],
    max_duration: int,
) -> list[dict]:
    """
    Use Claude to find narrative cliffhanger split points in the transcript.
    Returns a list of dicts: {"index": int, "reason": str}
    index = the [N] of the LAST segment in that clip; cut happens after that segment ends.
    Empty list means the video fits in one clip.
    """
    total = segments[-1]["end"] if segments else 0
    n_clips = math.ceil(total / max_duration)
    n_splits = n_clips - 1

    if n_splits == 0:
        return []

    client = get_anthropic_client()
    transcript_text = build_transcript_text(segments)
    n_segs = len(segments)

    # Build per-split target windows so Claude knows the acceptable index range
    window_lines = []
    for i in range(n_splits):
        target_s = max_duration * (i + 1)
        # Find the segment index whose timestamp is closest to the target
        closest = min(range(n_segs), key=lambda j: abs(segments[j]["end"] - target_s))
        # Allow ±30% of max_duration on either side, clamped to valid range
        slack = int(n_segs * 0.30 * max_duration / total)
        lo = max(0, closest - slack)
        hi = min(n_segs - 2, closest + slack)
        window_lines.append(
            f"  Split {i + 1}: target ~{target_s}s → search indices [{lo}–{hi}] "
            f"(~{seconds_to_hms(segments[lo]['start'])} to ~{seconds_to_hms(segments[hi]['end'])})"
        )
    windows_str = "\n".join(window_lines)

    print(f"[3/?] Asking Claude to find {n_splits} cliffhanger split point(s) …")

    prompt = f"""You are editing a video into {n_clips} sequential clips, each roughly {max_duration} seconds long.
The full timestamped transcript is below. Each line has a numeric index [N].

Your job: find exactly {n_splits} split point(s). A split point is the index [N] of the LAST transcript line in a clip — the cut happens immediately after that line ends.

─── PHASE 1: READ THE WHOLE TRANSCRIPT FIRST ───
Before choosing any split points, read the entire transcript and map out the natural narrative structure:
• What are the 3–6 main beats, scenes, or topic shifts in this video?
• Where does tension build and where does it resolve?
• What are the 2–3 most suspenseful or unresolved moments — places where something has been set up but the payoff hasn't landed yet?

─── PHASE 2: CHOOSE SPLIT POINTS ───
Each split must satisfy ALL of:

1. SENTENCE COMPLETE — the last line before the cut ends a full sentence or clause. Never mid-word, never mid-sentence, never after "um", "like", "so", "and", or other trailing connectors.

2. CLIFFHANGER — the viewer ends the clip feeling something is unresolved. They NEED the next clip to find out what happens. The payoff for what was just set up is in the next clip, not in this one.

   Strong cliffhanger patterns:
   • A question is asked but the answer hasn't come yet
   • Someone says "and then..." / "so I..." / "that's when..." but the consequence hasn't landed
   • A dramatic revelation is set up but not yet delivered ("you won't believe what happened next")
   • A decision is announced but the outcome is unknown
   • Tension peaks mid-event and the resolution is cut off

3. NEXT CLIP HAS CONTEXT — the line AFTER the split point starts a clip that makes sense on its own. The viewer of clip N+1 is not dropped into the middle of a sentence or a punchline with no setup.

   Bad starts for a new clip: mid-explanation, mid-joke, mid-reaction, mid-list, continuation of a sentence from the previous clip.
   Good starts for a new clip: a new question, a scene change, a new speaker turn, "So...", "Then...", "After that...", any fresh setup.

4. NEAR TARGET TIME — stay within the search window for each split:
{windows_str}
   If no good cliffhanger exists inside the window, go slightly outside it rather than making a bad cut.

─── WHAT TO AVOID ───
• Splitting after a full resolution ("...and it all worked out", "so that's the story")
• Splitting mid-anecdote right before the punchline (the payoff should be in the NEXT clip, but there must be enough setup in this clip)
• Splitting where the next clip's opening line only makes sense if you heard the previous clip

TRANSCRIPT:
{transcript_text}

Return ONLY a valid JSON array of exactly {n_splits} object(s) in ascending index order. No markdown, no explanation.

Each object:
  "index":  integer — the [N] of the last transcript line in the clip
  "reason": string  — one plain sentence: what is unresolved at this cut that makes the viewer need the next clip

Example:
[{{"index": 42, "reason": "He's just said 'I had no idea what was waiting for me' — the reveal is in the next clip."}}]"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=256 + n_splits * 128,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Strip prose preamble: find the start of the JSON array of objects ([{)
    bracket = raw.find("[{")
    if bracket > 0:
        raw = raw[bracket:]

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or len(parsed) != n_splits:
            raise ValueError(f"Expected list of {n_splits} object(s), got: {raw!r}")
        result = []
        for item in parsed:
            if "index" not in item:
                raise ValueError(f"Missing 'index' key in: {item}")
            idx = int(item["index"])
            if not (0 <= idx < n_segs - 1):
                raise ValueError(f"Split index {idx} out of range (0–{n_segs - 2})")
            result.append({"index": idx, "reason": item.get("reason", "")})
        result.sort(key=lambda x: x["index"])
        return result
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"\n[ERROR] Failed to parse Claude's split-point response: {exc}")
        print("─── Raw Claude response ───")
        print(raw)
        print("───────────────────────────")
        sys.exit(1)


# ─────────────────────────── FFmpeg: cut & concat ───────────────────────────

# Scale full width to 1080px, pad top/bottom with black bars to reach 1920px height.
VERTICAL_FILTER = (
    "scale=1080:-2,"
    "pad=1080:1920:0:(oh-ih)/2:black"
)


SEGMENT_END_BUFFER = 0.0


def cut_segment(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    label: str,
) -> None:
    """Cut one segment, apply vertical filter, encode as h264/aac."""
    duration = end - start
    print(f"    Cutting {label}: {seconds_to_hms(start)} → {seconds_to_hms(end)} ({duration:.1f}s)")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-avoid_negative_ts", "make_zero",
        "-vf", VERTICAL_FILTER,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"[ERROR] FFmpeg failed on {label}:")
        print(result.stderr.decode())
        sys.exit(1)


def concat_segments(segment_paths: list[str], output_path: str) -> None:
    """Join pre-encoded segments into a single file using the concat demuxer."""
    print(f"    Concatenating {len(segment_paths)} segment(s) → '{Path(output_path).name}' …")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        concat_list = f.name
        for p in segment_paths:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            output_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print("[ERROR] FFmpeg failed to concatenate segments:")
            print(result.stderr.decode())
            sys.exit(1)
    finally:
        Path(concat_list).unlink(missing_ok=True)


# ─────────────────────────── ssplit mode (time-based, no API) ───────────────

def get_video_duration(video_path: str) -> float:
    """Return total duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        print("[ERROR] ffprobe could not read video duration:")
        print(result.stderr.decode())
        sys.exit(1)
    return float(result.stdout.decode().strip())


def run_ssplit(args, video_path: str, output_dir: Path) -> None:
    """Split video into fixed-duration clips purely by time. No API calls needed."""
    output_dir.mkdir(parents=True, exist_ok=True)

    total = get_video_duration(video_path)
    clip_dur = args.duration
    n_clips = math.ceil(total / clip_dur)

    print(f"\n[1/1] Simple time split: {total:.1f}s → {n_clips} clip(s) of ≤{clip_dur}s …\n")

    for i in range(n_clips):
        start = i * clip_dur
        end = min(start + clip_dur, total)
        out_file = str(output_dir / f"clip_{i + 1:03d}.mp4")
        cut_segment(video_path, start, end, out_file, label=f"clip {i + 1:03d}/{n_clips}")

    print(f"\nDone!  {n_clips} clip(s)  →  {output_dir.resolve()}")


# ─────────────────────────── main ───────────────────────────────────────────

def export_variant(
    video_path: str,
    selected: list[dict],
    transcript_segments: list[dict],
    output_file: str,
    variant_label: str,
) -> float:
    """Cut and concat one variant. Returns combined duration."""
    combined = sum(s["end_seconds"] - s["start_seconds"] for s in selected)
    print(f"      Story: {selected[0].get('story', '?')}")
    for i, seg in enumerate(selected, 1):
        dur = seg["end_seconds"] - seg["start_seconds"]
        print(
            f"      [{i}/{len(selected)}] {seconds_to_hms(seg['start_seconds'])} → "
            f"{seconds_to_hms(seg['end_seconds'])}  ({dur:.1f}s)  [{seg['role'].upper()}]"
        )
        print(f"          {seg['reason']}")

    temp_files: list[str] = []
    try:
        for i, seg in enumerate(selected, 1):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            temp_files.append(tmp.name)
            cut_segment(
                video_path,
                start=float(seg["start_seconds"]),
                end=float(seg["end_seconds"]),
                output_path=tmp.name,
                label=f"{variant_label} seg {i}/{len(selected)} [{seg['role']}]",
            )
        concat_segments(temp_files, output_file)
    finally:
        for f in temp_files:
            Path(f).unlink(missing_ok=True)

    return combined


def run_highlight(args, video_path: str, output_dir: Path, transcript_segments: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    n_variants = args.variants

    all_variants = select_segments(
        transcript_segments,
        n_clips=args.clips,
        total_duration=args.duration,
        n_variants=n_variants,
    )

    print(f"\n[4/4] Exporting {n_variants} variant(s) …\n")

    for v_idx, selected in enumerate(all_variants, 1):
        if n_variants == 1:
            out_name = f"highlight_{timestamp}.mp4"
        else:
            out_name = f"highlight_{timestamp}_v{v_idx}.mp4"

        output_file = str(output_dir / out_name)

        print(f"  ── Variant {v_idx}/{n_variants} → {out_name}")
        combined = export_variant(
            video_path, selected, transcript_segments, output_file,
            variant_label=f"v{v_idx}",
        )
        print(f"      ✓ {out_name}  ({combined:.1f}s, 1080×1920)\n")

    if getattr(args, 'edit', False):
        from editor import launch_editor  # type: ignore[import]
        launch_editor(
            video_path=video_path,
            transcript_segments=transcript_segments,
            all_variants=all_variants,
            output_dir=output_dir,
            export_fn=export_variant,
        )


def run_split(args, video_path: str, output_dir: Path, transcript_segments: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    split_points = select_split_points(transcript_segments, max_duration=args.duration)

    # Build (start, end) pairs from split point indices
    segs = transcript_segments
    if not split_points:
        clips = [(segs[0]["start"], segs[-1]["end"])]
        reasons = ["Single clip — video fits within target duration."]
    else:
        indices = [sp["index"] for sp in split_points]
        boundaries = [segs[0]["start"]] + [segs[i]["end"] for i in indices] + [segs[-1]["end"]]
        clips = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]
        reasons = [sp["reason"] for sp in split_points] + ["(last clip)"]

    total_clips = len(clips)
    print(f"\n[4/4] Cutting {total_clips} clip(s) …\n")

    for i, (start, end) in enumerate(clips, 1):
        dur = end - start
        cliffhanger = reasons[i - 1] if i - 1 < len(reasons) else ""
        print(f"  Clip {i:03d}: {seconds_to_hms(start)} → {seconds_to_hms(end)}  ({dur:.1f}s)")
        if cliffhanger and cliffhanger != "(last clip)":
            print(f"           ↳ ends on: {cliffhanger}")
        out_file = str(output_dir / f"clip_{i:03d}.mp4")
        cut_segment(
            video_path,
            start=start,
            end=end,
            output_path=out_file,
            label=f"clip {i:03d}/{total_clips}",
        )

    print(f"\nDone!  {total_clips} clip(s)  →  {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract vertical highlights from a video using Whisper + Claude + FFmpeg.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
modes:
  highlight   Claude selects the most viral-worthy segments and stitches them
              into a single highlight.mp4  (default)
  split       Splits the full video at transcript boundaries (requires Whisper)
  ssplit      Splits the full video at fixed time intervals — no API calls at all

examples:
  python extract_highlights.py interview.mp4
  python extract_highlights.py interview.mp4 --highlight --clips 4 --duration 90
  python extract_highlights.py interview.mp4 --split --duration 58
  python extract_highlights.py interview.mp4 --ssplit --duration 60
  python extract_highlights.py interview.mp4 --output-dir D:/Exports
""",
    )
    parser.add_argument("video", help="Path to the input video file (MP4 / MOV / MKV …).")
    parser.add_argument(
        "--mode",
        choices=["highlight", "split", "ssplit"],
        default="highlight",
        help="'highlight' (AI arc), 'split' (AI split points), 'ssplit' (time-based, no API). Default: highlight.",
    )
    # Convenience shorthand flags — set the mode without typing --mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--highlight", dest="mode", action="store_const", const="highlight",
        help="Shorthand for --mode highlight.",
    )
    mode_group.add_argument(
        "--split", dest="mode", action="store_const", const="split",
        help="Shorthand for --mode split.",
    )
    mode_group.add_argument(
        "--ssplit", dest="mode", action="store_const", const="ssplit",
        help="Shorthand for --mode ssplit.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=75,
        metavar="SECONDS",
        help=(
            "highlight mode: target combined duration (default 75). "
            "split mode: max duration per clip (default 75)."
        ),
    )
    parser.add_argument(
        "--clips",
        type=int,
        default=3,
        metavar="N",
        help="highlight mode only: number of source segments to stitch (default 3).",
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=1,
        metavar="N",
        help=(
            "highlight mode only: number of distinct highlight videos to produce "
            "from a single Claude call (default 1). Each variant explores a different "
            "angle or story thread. Files are named highlight_<timestamp>_v1.mp4, _v2.mp4 …"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            "Where to save output files. "
            "Defaults to a new folder next to the source video, "
            "named <date>_<time>_<mode>_<video name>."
        ),
    )
    parser.add_argument(
        "--whisper-model",
        default="medium",
        metavar="MODEL",
        help="Local faster-whisper model to use for transcription. "
             "Options: tiny, base, small, medium, large-v2, large-v3. "
             "Default: medium (~14 min on CPU for 25-min video, free).",
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help=(
            "highlight mode only: after exporting, open an interactive browser editor "
            "where you can drag cut handles, preview the original footage, and re-export. "
            "Requires flask (pip install flask)."
        ),
    )
    args = parser.parse_args()

    video_path = str(Path(args.video).resolve())
    if not Path(video_path).exists():
        print(f"[ERROR] File not found: {video_path}")
        sys.exit(1)

    suffix = Path(video_path).suffix.lower()
    if suffix not in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
        print(f"[WARNING] Unrecognized extension '{suffix}'. Proceeding anyway …")

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else default_output_dir(video_path, args.mode)
    )

    check_ffmpeg()

    print(f"\n=== Video Highlight Extractor ===")
    print(f"  Mode     : {args.mode}")
    print(f"  Input    : {video_path}")
    if args.mode == "highlight":
        print(f"  Segments : {args.clips}  |  Target: ~{args.duration}s combined  |  Variants: {args.variants}")
    else:
        print(f"  Clip dur : ≤{args.duration}s")
    print(f"  Output   : {output_dir.resolve()}\n")

    # ssplit needs no API calls at all — skip directly to cutting
    if args.mode == "ssplit":
        run_ssplit(args, video_path, output_dir)
        return

    # highlight and split both need a transcript
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        audio_path = tmp.name
    try:
        extract_audio(video_path, audio_path)
        transcript_segments = transcribe(audio_path, model=args.whisper_model)
    finally:
        Path(audio_path).unlink(missing_ok=True)

    if not transcript_segments:
        print("[ERROR] Whisper returned an empty transcript. Cannot continue.")
        sys.exit(1)

    if args.mode == "highlight":
        run_highlight(args, video_path, output_dir, transcript_segments)
    else:
        run_split(args, video_path, output_dir, transcript_segments)


if __name__ == "__main__":
    main()
