"""Synthesize original, royalty-free background music and sound effects.

Everything here is generated from scratch with numpy, so the channel owns it
outright (no licensing or Content ID claims).

usage: python3 make_audio.py <out_dir>
"""
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
rng = np.random.default_rng(813)


def write_wav(path, x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    peak = np.max(np.abs(x)) or 1.0
    x = (x / peak * 0.89 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x.tobytes())


def env(n, a=0.005, d=0.2, s=0.0, r=0.05):
    t = np.arange(n) / SR
    e = np.where(t < a, t / a, s + (1 - s) * np.exp(-(t - a) / d))
    tail = int(r * SR)
    if tail and tail < n:
        e[-tail:] *= np.linspace(1, 0, tail)
    return e


def lowpass(x, cutoff):
    # one-pole low-pass, good enough for taming synth edges
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def highpass(x, cutoff):
    return x - lowpass(x, cutoff)


def kick(n):
    t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 35)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.001, 0.18)


def snare(n):
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * 190 * t) * env(n, 0.001, 0.06)
    noise = highpass(rng.standard_normal(n), 1500) * env(n, 0.001, 0.12)
    return 0.5 * body + 0.6 * noise


def hat(n, open_=False):
    noise = highpass(rng.standard_normal(n), 7000)
    return noise * env(n, 0.001, 0.12 if open_ else 0.03)


def saw(freq, n, detune=0.0):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for d in (-detune, 0.0, detune):
        ph = (t * freq * (1 + d)) % 1.0
        out += 2 * ph - 1
    return out / 3


def note_hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def track(bpm, root, progression, bars, style, seed):
    """Build one loopable track. progression = list of chord offsets (semitones from root, minor/major triads)."""
    global rng
    rng = np.random.default_rng(seed)
    beat = int(SR * 60 / bpm)
    bar = beat * 4
    total = bar * bars
    drums = np.zeros(total)
    bass = np.zeros(total)
    keys = np.zeros(total)

    k, s, h, ho = kick(beat), snare(beat), hat(beat // 2), hat(beat, True)
    for b in range(bars):
        for q in range(4):
            i = b * bar + q * beat
            if style == "drive" or q in (0, 2):
                drums[i:i + beat] += 0.9 * k
            if q in (1, 3):
                drums[i:i + beat] += 0.55 * s
            for e in range(2):
                j = i + e * beat // 2
                drums[j:j + beat // 2] += (0.16 if e else 0.10) * h
        if style == "bounce":
            j = b * bar + 3 * beat + beat // 2
            drums[j:j + beat // 2] += 0.6 * k[: beat // 2]
        j = b * bar + 3 * beat + beat // 2
        drums[j:j + beat // 2] += 0.08 * ho[: beat // 2]

    for b in range(bars):
        off, quality = progression[b % len(progression)]
        third = 3 if quality == "m" else 4
        chord = [root + off, root + off + third, root + off + 7]
        start = b * bar
        # bass: eighth-note pulse on chord root
        for e in range(8):
            j = start + e * beat // 2
            n = beat // 2
            f = note_hz(chord[0] - 24)
            tone = np.sin(2 * np.pi * f * np.arange(n) / SR) + 0.3 * saw(f, n)
            bass[j:j + n] += tone * env(n, 0.003, 0.12, 0.3, 0.02)
        # keys: off-beat stabs (pluck) of the triad, one octave up
        for e in range(8):
            if style == "drive" and e % 2 == 0:
                continue
            j = start + e * beat // 2
            n = beat // 2
            stab = sum(saw(note_hz(m), n, 0.004) for m in chord) / 3
            keys[j:j + n] += stab * env(n, 0.002, 0.09)

    keys = lowpass(keys, 2600)
    bass = lowpass(bass, 400)
    mix = 0.9 * drums + 0.75 * bass + 0.45 * keys
    # gentle stereo: keys slightly wider
    left = mix + 0.08 * np.roll(keys, 300)
    right = mix + 0.08 * np.roll(keys, -300)
    return np.stack([left, right], axis=1)


def whoosh(seconds=0.7):
    n = int(SR * seconds)
    t = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    # sweep a band by mixing low- and high-passed copies over time
    lo = lowpass(noise, 600)
    hi = highpass(noise, 3000)
    x = (1 - t) * lo + t * hi
    return x * np.sin(np.pi * t) ** 1.5


def spray(seconds=8.0):
    n = int(SR * seconds)
    noise = rng.standard_normal(n)
    x = highpass(lowpass(noise, 9000), 900)
    flutter = 1 + 0.15 * np.sin(2 * np.pi * 7 * np.arange(n) / SR)
    fade = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.3 * SR))
    return x * flutter * fade


def ding():
    n = int(SR * 1.2)
    t = np.arange(n) / SR
    x = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((1318.5, 1.0), (2637, 0.35), (1975.5, 0.25)))
    return x * env(n, 0.002, 0.35)


def main(out_dir):
    out = Path(out_dir)
    (out / "music").mkdir(parents=True, exist_ok=True)
    (out / "sfx").mkdir(parents=True, exist_ok=True)
    # Bright, confident, non-distracting beds (~35-45 s each, loop cleanly on bar lines)
    write_wav(out / "music/bright_drive.wav", track(112, 62, [(0, ""), (7, ""), (9, "m"), (5, "")], 20, "drive", 1))
    write_wav(out / "music/clean_bounce.wav", track(100, 60, [(9, "m"), (5, ""), (0, ""), (7, "")], 16, "bounce", 2))
    write_wav(out / "music/fresh_start.wav", track(120, 64, [(0, ""), (5, ""), (9, "m"), (7, "")], 24, "drive", 3))
    write_wav(out / "sfx/whoosh.wav", whoosh())
    write_wav(out / "sfx/spray.wav", spray())
    write_wav(out / "sfx/ding.wav", ding())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "assets")
