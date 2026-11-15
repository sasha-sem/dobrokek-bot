"""Locate words in synthesized speech and apply gentle pitch contours."""

from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import wave

import numpy as np

from meme_direction import WORD, choose_aligned_cue, word_options
from meme_speech import CHANNELS, SAMPLE_RATE


def key(text):
    return text.casefold().replace("ё", "е")


def words_from_whisper(data):
    words = []
    for entry in data["transcription"]:
        pieces = []
        for token in entry["tokens"]:
            raw = token["text"]
            if raw.startswith("[_") or not any(ch.isalpha() for ch in raw):
                continue
            if raw[0].isspace() or not pieces:
                pieces.append({"word": "", "tokens": []})
            pieces[-1]["word"] += raw.strip()
            pieces[-1]["tokens"].append(token)
        for piece in pieces:
            match = WORD.search(piece["word"])
            if not match:
                continue
            tokens = piece["tokens"]
            dtw = [t["t_dtw"] * .01 for t in tokens if t.get("t_dtw", -1) >= 0]
            start = tokens[0]["offsets"]["from"] / 1000
            end = max(t["offsets"]["to"] for t in tokens) / 1000
            if dtw:
                end = max(end, dtw[-1])
            words.append({"word": match.group(), "start": start, "end": end,
                          "attention": dtw[0] if dtw else None})
    return words


def executable_path(binary, path):
    if os.name != "nt" and str(binary).lower().endswith(".exe"):
        return subprocess.check_output(["wslpath","-w",str(path)],text=True).strip()
    return str(path)


def whisper_words(input_path, prefix, cli, model, dtw, language="ru"):
    subprocess.run([str(cli), "-m", executable_path(cli,model), "-f", executable_path(cli,input_path), "-l", language,
                    "-ng", "-t", "5", "-nfa", "-dtw", dtw, "-ojf", "-of", executable_path(cli,prefix), "-np",
                    "--suppress-regex", "^[[:space:]]*[0-9]"],
                   check=True, capture_output=True, timeout=155)
    return words_from_whisper(json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8")))


def timemap(expected, observed):
    matcher = SequenceMatcher(None, [key(x) for x in expected], [key(x["word"]) for x in observed], autojunk=False)
    return {i+n: j+n for i,j,length in matcher.get_matching_blocks() for n in range(length)}


def active_bounds(pcm, start, end):
    first, last = round(start*SAMPLE_RATE), round(end*SAMPLE_RATE)
    block = SAMPLE_RATE//100
    data = pcm[first:last]
    blocks = len(data)//block
    if blocks:
        power = np.sqrt(np.mean(data[:blocks*block].astype(np.float32).reshape(blocks,block,CHANNELS)**2,
                                axis=(1,2)))
        sounded = np.flatnonzero(power >= max(105.,np.max(power)*.045))
        if len(sounded):
            return (first+sounded[0]*block)/SAMPLE_RATE, (first+(sounded[-1]+1)*block)/SAMPLE_RATE
    return start, end


def select_windows(plan, line_times, observed, pcm):
    flat, starts = [], []
    for segment in plan["segments"]:
        starts.append(len(flat))
        flat.extend(WORD.findall(segment["text"]))
    matched = timemap(flat, observed)
    windows = []
    for cue in plan["cues"]:
        line, pos = cue["line"], cue["word_index"]
        phrase_words = WORD.findall(plan["segments"][line]["text"])
        left, right = active_bounds(pcm, *line_times[line])
        if len(phrase_words) == 1:
            start, end = left, right
        else:
            i = matched.get(starts[line]+pos)
            if i is None:
                hits = [n for n, word in enumerate(observed)
                        if key(word["word"]) == key(cue["word"]) and left-.14 <= word["start"] <= right+.08]
                if len(hits) != 1:
                    continue
                i = hits[0]
            word = observed[i]
            start = word["start"]
            attention = word["attention"]
            if attention is not None and pos and left+.06 < attention < right+.04:
                if start < left+.02 or attention-start > .35:
                    start = attention-.12
            if not left-.28 <= start <= right-.035:
                continue
            start = max(left, start-.025)
            end = min(right, word["end"] + .025)
            if pos == 0:
                start = left
            if pos == len(phrase_words)-1:
                end = right
            if i+1 < len(observed):
                following = observed[i+1]["start"]
                if start+.085 < following < right+.05:
                    end = min(right, following-.02)
            if end-start < .095:
                end = min(right, start+.14)
        if end-start < .09:
            continue
        if end-start > 1.05:
            end = start + 1.05
        if any(start < other["to"] and end > other["from"] for other in windows):
            continue
        windows.append({**cue, "from": float(start), "to": float(end)})
    return sorted(windows, key=lambda row: row["from"])


def write_wav(path, pcm, rate=SAMPLE_RATE):
    temporary = path.with_suffix(".partial.wav")
    with wave.open(str(temporary), "wb") as wav:
        wav.setparams((CHANNELS, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm.tobytes())
    temporary.replace(path)


def softened_pitch(pcm, windows, ffmpeg):
    count = len(pcm)
    curve = np.zeros(count, dtype=np.float32)
    mix = np.zeros(count, dtype=np.float32)
    commands = []
    for wnd in windows:
        left, right = [round(wnd[n]*SAMPLE_RATE) for n in ("from", "to")]
        pos = np.linspace(0., 1., right-left)
        edge = np.sin(np.pi * pos)**1.45
        mode = wnd["mode"]
        if mode == "accent":
            offsets = np.interp(pos, [0,.42,1], [0,1.55,0]) * edge
        elif mode == "rise":
            offsets = np.interp(pos, [0,.33,.77,1], [0,0,1.6,0]) * edge
        else:
            offsets = np.interp(pos, [0,.3,.79,1], [0,.3,-1.35,0]) * edge
        curve[left:right] = offsets
        fade = max(.011, min(.025, (right-left)/SAMPLE_RATE*.14))
        t = np.arange(right-left)/SAMPLE_RATE
        ramp = np.minimum(np.clip(t/fade,0,1), np.clip((t[-1]-t)/fade,0,1))
        mix[left:right] = .5-.5*np.cos(np.pi*ramp)
        for frame in range(left, right, 960):
            commands.append(f"{frame/SAMPLE_RATE:.5f} rubberband pitch {2**(curve[frame]/12):.7f}")
        commands.append(f"{right/SAMPLE_RATE:.5f} rubberband pitch 1")
    if not commands:
        return pcm, 0
    dry = pcm.astype(np.float32)/32768
    graph = (f"asetnsamples=n=240:p=0,asendcmd=c='{';'.join(commands)}',"
             "rubberband=tempo=1:formant=preserved:window=standard:pitchq=consistency:channels=together,"
             f"apad,atrim=end_sample={count}")
    with tempfile.TemporaryDirectory(prefix="meme-pitch-") as folder:
        source = Path(folder) / "dry.f32"
        changed_audio = Path(folder) / "pitch.f32"
        dry.astype("<f4").tofile(source)
        subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(SAMPLE_RATE),
                        "-ac", str(CHANNELS), "-i", executable_path(ffmpeg, source), "-af", graph,
                        "-f", "f32le", executable_path(ffmpeg, changed_audio)],
                       check=True, capture_output=True, timeout=75)
        wet = np.fromfile(changed_audio, dtype="<f4").reshape(-1, CHANNELS)
    assert wet.shape == dry.shape and np.isfinite(wet).all()
    applied = 0
    for wnd in windows:
        start,end = [round(wnd[n]*SAMPLE_RATE) for n in ("from","to")]
        track = dry[start:end]
        moved = wet[start:end].copy()
        power = np.linalg.norm(moved)
        if power < 0.15*np.linalg.norm(track):
            mix[start:end] = 0
            continue
        applied += 1
    blended = dry*(1-mix[:,None])+wet*mix[:,None]
    changed = mix > 0
    result = pcm.copy()
    result[changed] = np.rint(np.clip(blended[changed], -1, 32767/32768)*32768).astype("<i2")
    return result, applied


def aligned_candidates(plan, times, observed, pcm):
    choices = []
    for cue in word_options(plan["segments"]):
        if len(cue["word"]) < 3:
            continue
        window = select_windows({**plan,"cues":[{**cue,"mode":"accent"}]},times,observed,pcm)
        if window and window[0]["to"]-window[0]["from"] >= .12:
            choices.append(window[0])
    focused = {cue["line"] for cue in plan["cues"]}
    return sorted(choices,key=lambda cue:(cue["line"] not in focused,cue["line"],cue["word_index"]))[:20]


def alignment_language(segments):
    text = " ".join(line["text"] for line in segments)
    if re.search(r"[а-яёіїєґ]", text, re.IGNORECASE):
        return "uk" if re.search(r"[іїєґ]", text, re.IGNORECASE) else "ru"
    if re.search(r"[ğĞıİşŞüÜöÖçÇ]", text):
        return "tr"
    return "en" if text.isascii() or re.search(r"[a-z]", text, re.IGNORECASE) else "auto"


def apply_direction(plan, times, raw, output, model, settings, ffmpeg):
    """Apply only cues with a measured word window, leaving unverified regions intact."""
    with wave.open(str(raw),"rb") as audio:
        pcm = np.frombuffer(audio.readframes(audio.getnframes()),dtype="<i2").reshape(-1,CHANNELS)
    if not plan["cues"]:
        write_wav(output,pcm)
        return [], 0
    input_path = raw.with_suffix(".asr.wav")
    subprocess.run([str(ffmpeg),"-nostdin","-v","error","-y","-i",str(raw),"-ac","1","-ar","16000",
                    "-c:a","pcm_s16le",str(input_path)],check=True)
    cli, weights, dtw = settings["cli"], settings["model"], settings["dtw"]
    log_path = raw.with_name(raw.stem+"-words")
    language = alignment_language(plan["segments"])
    observed = whisper_words(input_path,log_path,cli,weights,dtw,language)
    windows = select_windows(plan,times,observed,pcm)
    alternatives = aligned_candidates(plan,times,observed,pcm) if not windows else []
    if not windows and not alternatives and settings.get("fallback_model"):
        observed = whisper_words(input_path,log_path,cli,settings["fallback_model"],settings["fallback_dtw"],language)
        windows = select_windows(plan,times,observed,pcm)
        if not windows:
            alternatives = aligned_candidates(plan,times,observed,pcm)
    if not windows and alternatives:
        if cue := choose_aligned_cue(model,alternatives,plan):
            windows = [cue]
    result, count = softened_pitch(pcm,windows,settings["pitch_ffmpeg"])
    write_wav(output,result)
    return windows, count
