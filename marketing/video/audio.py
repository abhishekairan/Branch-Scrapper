"""Code-synthesized soundtrack (music + SFX) for the Branch Scrapper promo.

Everything is generated from math here: no samples, loops or downloaded audio, so the output is original and
rights-free. Pure standard library (no numpy); ffmpeg is only used to *measure* loudness.

The scene cuts and every highlight/UI event come from timeline.json (the same file app.js reads), expressed in
beats, so picture and sound cannot drift apart.

    F:/Projects/Github Scrapper/V1/.venv/Scripts/python.exe marketing/video/audio.py
    ... audio.py --target -14 --out marketing/out/soundtrack.wav

Output: marketing/out/soundtrack.wav (44.1 kHz, stereo, 16-bit, ~ -14 LUFS integrated, true peak <= -1 dBTP).
Deterministic: same code => same bytes.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
import wave
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
TL = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))

SR = 44100
BPM = TL["bpm"]
BEAT = 60.0 / BPM
FPS = TL["fps"]
TAU = 2 * math.pi

# ---------- timeline helpers ----------
SC_IDS = [s["id"] for s in TL["scenes"]]
SC_START = [0.0]
for _s in TL["scenes"]:
    SC_START.append(SC_START[-1] + _s["beats"])  # in beats
TOTAL_BEATS = SC_START[-1]
TOTAL_S = TOTAL_BEATS * BEAT
DUR_S = TOTAL_S + 0.05
N = int(math.ceil(DUR_S * SR))
DROP = float(TL["drop_beat"])
CTA_B = SC_START[SC_IDS.index("cta")]


def tb(b: float) -> float:
    """beats -> seconds"""
    return b * BEAT


def ev(scene: str, name: str, k: int | None = None) -> float:
    """absolute event time in beats"""
    v = TL["events"][scene][name]
    if k is not None:
        v = v[k]
    return SC_START[SC_IDS.index(scene)] + v


def R(seed: int) -> random.Random:
    return random.Random(seed)


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12.0)


# ---------- DSP primitives ----------
def noise(n: int, rng: random.Random) -> list[float]:
    r = rng.random
    return [r() * 2 - 1 for _ in range(n)]


def biquad(x, kind: str, f0: float, q: float = 0.707):
    w0 = TAU * f0 / SR
    cw, sw = math.cos(w0), math.sin(w0)
    al = sw / (2 * q)
    if kind == "lp":
        b0, b1, b2 = (1 - cw) / 2, 1 - cw, (1 - cw) / 2
    elif kind == "hp":
        b0, b1, b2 = (1 + cw) / 2, -(1 + cw), (1 + cw) / 2
    else:  # bp, 0 dB peak
        b0, b1, b2 = al, 0.0, -al
    a0, a1, a2 = 1 + al, -2 * cw, 1 - al
    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
    x1 = x2 = y1 = y2 = 0.0
    out = []
    ap = out.append
    for s in x:
        y = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, s
        y2, y1 = y1, y
        ap(y)
    return out


def onepole_lp(x, fc: float):
    a = 1 - math.exp(-TAU * fc / SR)
    y = 0.0
    out = []
    for s in x:
        y += a * (s - y)
        out.append(y)
    return out


def svf_band(x, fcs, q1: float):
    """Chamberlin state-variable filter, band output, per-sample cutoff list (keep fc <= ~6 kHz)."""
    low = band = 0.0
    out = []
    ap = out.append
    for s, fc in zip(x, fcs):
        f = 2 * math.sin(math.pi * fc / SR)
        low += f * band
        high = s - low - q1 * band
        band += f * high
        ap(band)
    return out


def norm(x, peak=1.0):
    m = max(max(x), -min(x)) or 1.0
    k = peak / m
    return [s * k for s in x]


def fade_tail(x, ms=12.0):
    k = min(len(x), int(ms * SR / 1000))
    for i in range(k):
        x[len(x) - 1 - i] *= 0.5 - 0.5 * math.cos(math.pi * i / k)
    return x


def fade_head(x, ms=1.0):
    k = min(len(x), int(ms * SR / 1000))
    for i in range(k):
        x[i] *= 0.5 - 0.5 * math.cos(math.pi * i / k)
    return x


_TABS: dict[int, list[float]] = {}
TN = 2048


def saw_tab(h: int):
    """band-limited saw: h harmonics, 1/k amplitudes, one guard sample for interpolation"""
    h = max(1, h)
    if h not in _TABS:
        t = [0.0] * TN
        for k in range(1, h + 1):
            a = 1.0 / k
            w = TAU * k / TN
            for i in range(TN):
                t[i] += a * math.sin(w * i)
        m = max(abs(v) for v in t)
        t = [v / m for v in t]
        t.append(t[0])
        _TABS[h] = t
    return _TABS[h]


def osc(tab, f: float, n: int, ph0: float = 0.0):
    inc = f * TN / SR
    ph = (ph0 % 1.0) * TN
    out = []
    ap = out.append
    for _ in range(n):
        j = int(ph)
        fr = ph - j
        a = tab[j]
        ap(a + (tab[j + 1] - a) * fr)
        ph += inc
        if ph >= TN:
            ph -= TN
    return out


def sine(f: float, n: int, ph0: float = 0.0):
    w = TAU * f / SR
    return [math.sin(w * i + ph0) for i in range(n)]


# ---------- stereo bus ----------
class Bus:
    def __init__(self, n: int):
        self.n = n
        self.L = [0.0] * n
        self.R = [0.0] * n

    def add(self, x, t: float, g: float = 1.0, pan: float = 0.0):
        i0 = int(round(t * SR))
        if i0 < 0:
            x = x[-i0:]
            i0 = 0
        m = min(len(x), self.n - i0)
        if m <= 0:
            return
        a = (pan + 1) * math.pi / 4
        gl, gr = g * math.cos(a), g * math.sin(a)
        seg = x[:m]
        self.L[i0:i0 + m] = [p + q * gl for p, q in zip(self.L[i0:i0 + m], seg)]
        self.R[i0:i0 + m] = [p + q * gr for p, q in zip(self.R[i0:i0 + m], seg)]

    def add_sweep(self, x, t: float, g: float, p0: float, p1: float):
        """mono x with a pan that moves p0 -> p1 across its length"""
        i0 = int(round(t * SR))
        if i0 < 0:
            x = x[-i0:]
            i0 = 0
        m = min(len(x), self.n - i0)
        if m <= 0:
            return
        gl, gr = [], []
        for i in range(m):
            p = p0 + (p1 - p0) * i / max(1, m - 1)
            a = (p + 1) * math.pi / 4
            gl.append(g * math.cos(a))
            gr.append(g * math.sin(a))
        self.L[i0:i0 + m] = [p + q * w for p, q, w in zip(self.L[i0:i0 + m], x, gl)]
        self.R[i0:i0 + m] = [p + q * w for p, q, w in zip(self.R[i0:i0 + m], x, gr)]

    def add_st(self, xl, xr, t: float, g: float = 1.0):
        i0 = int(round(t * SR))
        m = min(len(xl), self.n - i0)
        if m <= 0:
            return
        self.L[i0:i0 + m] = [p + q * g for p, q in zip(self.L[i0:i0 + m], xl[:m])]
        self.R[i0:i0 + m] = [p + q * g for p, q in zip(self.R[i0:i0 + m], xr[:m])]

    def mul(self, curve):
        self.L = [a * b for a, b in zip(self.L, curve)]
        self.R = [a * b for a, b in zip(self.R, curve)]

    def gain(self, g):
        self.L = [a * g for a in self.L]
        self.R = [a * g for a in self.R]


# ---------- instruments (one-shots) ----------
def kick():
    n = int(0.40 * SR)
    ph = 0.0
    out = []
    rng = R(1)
    for i in range(n):
        t = i / SR
        f = 46 + 120 * math.exp(-t / 0.026)
        ph += TAU * f / SR
        a = math.exp(-t / 0.15) * (1 - math.exp(-t / 0.0012))
        s = math.sin(ph) * a + (rng.random() * 2 - 1) * 0.12 * math.exp(-t / 0.0018)
        out.append(math.tanh(1.5 * s) / math.tanh(1.5))
    return fade_tail(out, 25)


def sub_boom(dur=1.3):
    n = int(dur * SR)
    ph = 0.0
    out = []
    for i in range(n):
        t = i / SR
        f = 34 + 52 * math.exp(-t / 0.22)
        ph += TAU * f / SR
        out.append(math.sin(ph) * math.exp(-t / 0.55) * (1 - math.exp(-t / 0.004)))
    return fade_tail(out, 60)


def clap():
    n = int(0.32 * SR)
    nz = biquad(noise(n, R(11)), "bp", 1500, 0.9)
    out = []
    for i, x in enumerate(nz):
        t = i / SR
        e = 0.0
        for k in range(3):
            tk = t - k * 0.0095
            if tk >= 0:
                e += math.exp(-tk / 0.0042) * 0.65
        if t >= 0.0285:
            e += math.exp(-(t - 0.0285) / 0.07)
        out.append(x * e)
    return fade_tail(norm(out), 20)


def snare():
    n = int(0.26 * SR)
    nz = biquad(noise(n, R(12)), "bp", 2200, 0.6)
    ph = 0.0
    out = []
    for i in range(n):
        t = i / SR
        ph += TAU * (170 + 70 * math.exp(-t / 0.02)) / SR
        out.append(nz[i] * 0.9 * math.exp(-t / 0.085) + math.sin(ph) * 0.5 * math.exp(-t / 0.05))
    return fade_tail(norm(out), 20)


def hat(open_: bool, seed: int):
    n = int((0.34 if open_ else 0.11) * SR)
    nz = biquad(noise(n, R(seed)), "hp", 7000, 0.7)
    tau = 0.075 if open_ else 0.014
    out = [nz[i] * math.exp(-(i / SR) / tau) for i in range(n)]
    return fade_tail(norm(out), 8)


def crash(dur=2.2, seed=21):
    n = int(dur * SR)
    nz = biquad(noise(n, R(seed)), "hp", 4500, 0.7)
    out = [nz[i] * math.exp(-(i / SR) / 0.55) * (1 - math.exp(-(i / SR) / 0.004)) for i in range(n)]
    return fade_tail(norm(out), 80)


def bass_note(f: float, dur_gate=0.24):
    n = int((dur_gate + 0.05) * SR)
    ph = 0.0
    w = TAU * f / SR
    out = []
    g = int(dur_gate * SR)
    for i in range(n):
        t = i / SR
        ph = w * i
        s = math.sin(ph) + 0.45 * math.sin(2 * ph + 0.3) + 0.18 * math.sin(3 * ph)
        a = (1 - math.exp(-t / 0.004)) * math.exp(-t / 0.28)
        if i > g:
            a *= max(0.0, 1 - (i - g) / (n - g))
        out.append(math.tanh(1.2 * s * a))
    return out


def pad(notes_midi, dur: float, cutoff: float, rel=0.4, att=0.12, spread=6.0):
    """detuned saw pad, stereo (list L, list R). cutoff = highest partial frequency kept."""
    n = int((dur + rel) * SR)
    L = [0.0] * n
    Rr = [0.0] * n
    for k, m in enumerate(notes_midi):
        f = mtof(m)
        h = max(1, min(18, int(cutoff / f)))
        tab = saw_tab(h)
        a = osc(tab, f * 2 ** (-spread / 1200), n, 0.13 * k)
        b = osc(tab, f * 2 ** (spread / 1200), n, 0.41 * k)
        L = [p + 0.72 * x + 0.28 * y for p, x, y in zip(L, a, b)]
        Rr = [p + 0.28 * x + 0.72 * y for p, x, y in zip(Rr, a, b)]
    sc = 1.0 / max(1, len(notes_midi))
    envs = []
    for i in range(n):
        t = i / SR
        e = 1 - math.exp(-t / (att / 3))
        if t > dur:
            e *= max(0.0, 1 - (t - dur) / rel)
        envs.append(e * sc)
    return [x * e for x, e in zip(L, envs)], [x * e for x, e in zip(Rr, envs)]


def pluck(f: float, bright=1.0, dur=0.42):
    n = int(dur * SR)
    h = max(1, min(11, int(7500 / f)))
    hi = osc(saw_tab(h), f, n)
    lo = sine(f, n)
    out = []
    for i in range(n):
        t = i / SR
        a = math.exp(-t / 0.11)
        b = math.exp(-t / 0.028) * bright
        out.append((0.6 * lo[i] * a + 0.4 * hi[i] * (0.2 * a + 0.8 * b)) * (1 - math.exp(-t / 0.0012)))
    return fade_tail(out, 20)


def stab(notes_midi, dur=1.6, cutoff=3500):
    n = int(dur * SR)
    acc = [0.0] * n
    for k, m in enumerate(notes_midi):
        f = mtof(m)
        tab = saw_tab(max(1, min(14, int(cutoff / f))))
        for dt in (-7, 7):
            o = osc(tab, f * 2 ** (dt / 1200), n, 0.2 * k)
            acc = [a + b for a, b in zip(acc, o)]
    sc = 1.0 / len(notes_midi) / 1.6
    out = [acc[i] * sc * math.exp(-(i / SR) / 0.7) * (1 - math.exp(-(i / SR) / 0.003)) for i in range(n)]
    return fade_tail(out, 80)


def riser(dur: float, seed=31):
    n = int(dur * SR)
    rng = R(seed)
    fcs = [300 * (6000 / 300) ** (i / n) for i in range(n)]
    nz = svf_band(noise(n, rng), fcs, 0.35)
    nz = norm(nz)
    ph = 0.0
    out = []
    for i in range(n):
        u = i / n
        f = 180 * (1900 / 180) ** (u * u)
        ph += TAU * f / SR
        tone = math.sin(ph) * 0.30
        out.append((nz[i] * 0.85 + tone) * (u ** 2.2))
    return fade_tail(norm(out), 10)


def whoosh(dur: float, f0: float, f1: float, seed: int, peak=0.55, q1=0.45):
    n = int(dur * SR)
    fcs = [f0 * (f1 / f0) ** (i / n) for i in range(n)]
    x = norm(svf_band(noise(n, R(seed)), fcs, q1))
    out = []
    for i in range(n):
        u = i / n
        e = (u / peak) ** 2 if u < peak else ((1 - u) / (1 - peak)) ** 1.6
        out.append(x[i] * e)
    return fade_tail(out, 15)


def ping(f: float, tau=0.09, dur=0.35, click=0.35, seed=41):
    n = int(dur * SR)
    rng = R(seed)
    out = []
    for i in range(n):
        t = i / SR
        s = math.sin(TAU * f * t) + 0.28 * math.sin(TAU * 2 * f * t + 0.5) * math.exp(-t / (tau * 0.5))
        s *= math.exp(-t / tau) * (1 - math.exp(-t / 0.0006))
        s += (rng.random() * 2 - 1) * click * math.exp(-t / 0.0012)
        out.append(s)
    return fade_tail(out, 25)


def tick(f=2600.0, seed=42):
    n = int(0.03 * SR)
    rng = R(seed)
    out = []
    for i in range(n):
        t = i / SR
        out.append((math.sin(TAU * f * t) * 0.8 + (rng.random() * 2 - 1) * 0.3) * math.exp(-t / 0.0045))
    return fade_tail(out, 3)


def pop(f0=820.0, f1=210.0):
    n = int(0.22 * SR)
    ph = 0.0
    out = []
    rng = R(43)
    for i in range(n):
        t = i / SR
        f = f1 + (f0 - f1) * math.exp(-t / 0.028)
        ph += TAU * f / SR
        out.append(math.sin(ph) * math.exp(-t / 0.07) * (1 - math.exp(-t / 0.0008))
                   + (rng.random() * 2 - 1) * 0.25 * math.exp(-t / 0.0015))
    return fade_tail(out, 15)


def thwack(seed=44):
    n = int(0.16 * SR)
    nz = biquad(noise(n, R(seed)), "bp", 3200, 0.8)
    ph = 0.0
    out = []
    for i in range(n):
        t = i / SR
        ph += TAU * (95 + 90 * math.exp(-t / 0.02)) / SR
        out.append(nz[i] * 0.6 * math.exp(-t / 0.03) + math.sin(ph) * 0.9 * math.exp(-t / 0.06))
    return fade_tail(norm(out), 15)


def glitch_strike():
    """crossed-out 'scrolling': bit-crushed noise stutter + falling zap + thunk"""
    n = int(0.34 * SR)
    rng = R(51)
    nz = noise(n, rng)
    hold, v = 5, 0.0
    crushed = []
    for i in range(n):
        if i % hold == 0:
            v = round(nz[i] * 7) / 7.0
        crushed.append(v)
    crushed = onepole_lp(crushed, 5500)
    gate_pat = [1, 0, 1, 1, 0, 1, 0, 0]
    seg = int(0.022 * SR)
    ph = 0.0
    out = []
    for i in range(n):
        t = i / SR
        g = gate_pat[min(len(gate_pat) - 1, i // seg)]
        f = 2100 * math.exp(-t / 0.09) + 140
        ph += TAU * f / SR
        zap = math.sin(ph + 1.8 * math.sin(ph * 0.5)) * math.exp(-t / 0.11)
        thunk = math.sin(TAU * (70 + 60 * math.exp(-t / 0.03)) * t) * math.exp(-t / 0.09)
        out.append(crushed[i] * g * 0.55 * math.exp(-t / 0.12) + zap * 0.55 + thunk * 0.9)
    return fade_tail(norm(out), 30)


# ---------- reverb (block-vectorised Schroeder, decorrelated L/R) ----------
def _comb(x, d, g):
    y = x[:]
    for s in range(d, len(y), d):
        blk = y[s:s + d]
        prev = y[s - d:s - d + len(blk)]
        y[s:s + len(blk)] = [a + g * b for a, b in zip(blk, prev)]
    return y


def _allpass(x, d, g):
    y = [-g * v for v in x[:d]]
    for s in range(d, len(x), d):
        xb = x[s:s + d]
        xd = x[s - d:s - d + len(xb)]
        yd = y[s - d:s - d + len(xb)]
        y += [-g * a + b + g * c for a, b, c in zip(xb, xd, yd)]
    return y


def reverb_chan(x, delays):
    pre = int(0.018 * SR)
    x = [0.0] * pre + x[:len(x) - pre]
    acc = [0.0] * len(x)
    for d in delays:
        c = _comb(x, d, 0.835)
        acc = [a + b for a, b in zip(acc, c)]
    acc = [a * 0.25 for a in acc]
    acc = _allpass(acc, 241, 0.5)
    acc = _allpass(acc, 563, 0.5)
    acc = onepole_lp(acc, 5200)
    hp = onepole_lp(acc, 220)
    return [a - b for a, b in zip(acc, hp)]


# ---------- loudness / dynamics ----------
def compress(L, Rr, thr_db=-15.0, ratio=2.0, atk=0.006, rel=0.14, blk=32):
    nb = len(L) // blk
    env = 0.0
    ca = math.exp(-blk / SR / atk)
    cr = math.exp(-blk / SR / rel)
    gains = []
    for b in range(nb):
        s = b * blk
        pk = max(max(map(abs, L[s:s + blk])), max(map(abs, Rr[s:s + blk])))
        env = ca * env + (1 - ca) * pk if pk > env else cr * env + (1 - cr) * pk
        db = 20 * math.log10(env + 1e-9)
        over = db - thr_db
        gains.append(10 ** ((-over * (1 - 1 / ratio)) / 20) if over > 0 else 1.0)
    return _apply_block_gain(L, Rr, gains, blk)


def _apply_block_gain(L, Rr, gains, blk):
    oL, oR = [], []
    g_prev = gains[0]
    for b, g in enumerate(gains):
        s = b * blk
        step = (g - g_prev) / blk
        ramp = [g_prev + step * (k + 1) for k in range(blk)]
        oL += [x * r for x, r in zip(L[s:s + blk], ramp)]
        oR += [x * r for x, r in zip(Rr[s:s + blk], ramp)]
        g_prev = g
    oL += L[len(oL):]
    oR += Rr[len(oR):]
    return oL, oR


def limit(L, Rr, ceiling, blk=32, W=8):
    nb = len(L) // blk
    need = []
    for b in range(nb):
        s = b * blk
        pk = max(max(map(abs, L[s:s + blk])), max(map(abs, Rr[s:s + blk])), 1e-9)
        need.append(min(1.0, ceiling / pk))
    mn = [min(need[max(0, j - W):j + W + 1]) for j in range(nb)]
    sm = []
    for j in range(nb):
        win = mn[max(0, j - W):j + W + 1]
        sm.append(sum(win) / len(win))
    return _apply_block_gain(L, Rr, sm, blk)


def write_wav(path: Path, L, Rr):
    rng = R(777)
    buf = array("h")
    for a, b in zip(L, Rr):
        for v in (a, b):
            v = v * 32767.0 + (rng.random() - rng.random())  # TPDF dither
            buf.append(max(-32768, min(32767, int(round(v)))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(buf.tobytes())


def measure(path: Path):
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    txt = r.stderr
    summ = txt[txt.rfind("Summary:"):]
    i = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1))
    lra = float(re.search(r"LRA:\s+(-?[\d.]+) LU", summ).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ).group(1))
    return i, lra, tp


# ---------- arrangement ----------
CHORDS = {
    "Am": dict(pad=[57, 60, 64], bass=33, arp=[69, 72, 76, 81]),
    "F": dict(pad=[57, 60, 65], bass=29, arp=[65, 69, 72, 77]),
    "C": dict(pad=[55, 60, 64], bass=36, arp=[72, 76, 79, 84]),
    "G": dict(pad=[55, 59, 62], bass=31, arp=[67, 71, 74, 79]),
}
PROG = ["Am", "F", "C", "G", "Am", "F", "C", "F", "G", "Am"]  # bars starting at DROP, DROP+4, ...
assert DROP + 4 * (len(PROG) - 1) == CTA_B, "progression must land on the CTA hit"
ARP_PAT = [0, None, 2, 3, 1, None, 2, 3, 0, None, 2, 3, 1, 2, 3, 2]
ARP_VEL = [1.0, 0, .55, .7, .85, 0, .55, .7, 1.0, 0, .55, .7, .85, .5, .7, .55]
PAN_NAMES = {"curtainLeft": (0.8, -0.8), "pushLeft": (0.8, -0.8), "curtainRight": (-0.8, 0.8)}


def build():
    drums, bass, music, sfx, send = Bus(N), Bus(N), Bus(N), Bus(N), Bus(N)
    kick_times: list[float] = []

    K, CL, SN = kick(), clap(), snare()
    HC = [hat(False, 61), hat(False, 62)]
    HO = hat(True, 63)

    def sendto(bus_buf: Bus, amt: float):
        send.L = [a + b * amt for a, b in zip(send.L, bus_buf.L)]
        send.R = [a + b * amt for a, b in zip(send.R, bus_buf.R)]

    # ---- intro (beats 0 .. DROP): filtered pad swell, ticking hats, quiet arp, riser, snare roll ----
    gap = 0.25  # silence before every big hit, in beats
    intro_len = tb(DROP - gap)
    pl, pr_ = pad(CHORDS["Am"]["pad"], intro_len, cutoff=420, rel=0.02, att=1.2)
    swell = [min(1.0, 0.25 + 0.75 * (i / len(pl)) ** 1.5) for i in range(len(pl))]
    music.add_st([a * s for a, s in zip(pl, swell)], [a * s for a, s in zip(pr_, swell)], 0.0, 0.55)
    arp_cache: dict[tuple, list[float]] = {}

    def arp_note(m, bright):
        k = (m, bright)
        if k not in arp_cache:
            arp_cache[k] = pluck(mtof(m), bright)
        return arp_cache[k]

    for i in range(int((DROP - gap) * 2)):  # 8th-note arp, growing
        b = i * 0.5
        m = CHORDS["Am"]["arp"][[0, 2, 1, 3][i % 4]]
        v = 0.18 + 0.30 * (b / DROP)
        music.add(arp_note(m, 0.15), tb(b), v, pan=-0.25 if i % 2 else 0.25)
    for i in range(int((DROP - gap) * 2)):  # ticking 8th-note hats, getting louder
        drums.add(HC[i % 2], tb(i * 0.5), 0.05 + 0.09 * (i * 0.5 / DROP), pan=0.2 if i % 2 else -0.2)
    # riser + roll into the drop
    r0 = DROP - 2.25
    sfx.add(riser(tb(DROP - gap - r0)), tb(r0), 0.42)
    for j, bb in enumerate([DROP - 1.0, DROP - 0.75, DROP - 0.5, DROP - 0.375, DROP - 0.25 - 0.125]):
        drums.add(SN, tb(bb), 0.18 + 0.12 * j)

    # ---- groove: DROP .. CTA_B ----
    fill_start = CTA_B - 2.0
    stop = CTA_B - gap
    for b in range(int(DROP), int(CTA_B - 2)):  # 4-on-the-floor, stops for the fill
        drums.add(K, tb(b), 1.0)
        kick_times.append(tb(b))
    for b in range(int(DROP), int(stop)):
        if (b - int(DROP)) % 4 in (1, 3) and b < fill_start:
            drums.add(CL, tb(b), 0.55)
        drums.add(HO, tb(b + 0.5), 0.30, pan=0.25)
        if b >= 14:
            drums.add(HC[0], tb(b + 0.25), 0.10, pan=-0.2)
            drums.add(HC[1], tb(b + 0.75), 0.13, pan=0.2)
        if b >= 22:
            drums.add(HC[1], tb(b), 0.09, pan=0.1)
    # fill before the CTA hit: snare 8ths then 16ths
    fill = [fill_start + x for x in (0, 0.5, 1.0, 1.25, 1.5, 1.625)]
    for j, bb in enumerate(fill):
        drums.add(SN, tb(bb), 0.30 + 0.12 * j)
    sfx.add(riser(tb(stop - fill_start), seed=32), tb(fill_start), 0.30)

    # bass, chords and arp
    bass_cache = {k: bass_note(mtof(v["bass"])) for k, v in CHORDS.items()}
    pad_cache = {}
    for bar, name in enumerate(PROG[:-1]):
        b0 = DROP + 4 * bar
        c = CHORDS[name]
        if name not in pad_cache:
            pad_cache[name] = pad(c["pad"], tb(4), cutoff=5200)
        pl, pr_ = pad_cache[name]
        music.add_st(pl, pr_, tb(b0), 0.50)
        for k in range(4):
            bb = b0 + k + 0.5
            if bb >= stop:
                continue
            note = bass_cache[name]
            g = 0.62
            bass.add(note, tb(bb), g)
        # octave pop on the last off-beat of every second bar
        if bar % 2 == 1 and b0 + 3.75 < stop:
            bass.add(bass_cache[name], tb(b0 + 3.75), 0.28)
        for s in range(16):
            idx = ARP_PAT[s]
            bb = b0 + s * 0.25
            if idx is None or bb >= stop:
                continue
            vel = ARP_VEL[s] * (0.9 if b0 < 14 else 1.0)
            music.add(arp_note(c["arp"][idx], 1.0), tb(bb), 0.30 * vel, pan=-0.3 if s % 2 else 0.3)

    # ---- the drop: impact on the downbeat ----
    sfx.add(sub_boom(), tb(DROP), 0.95)
    sfx.add(crash(), tb(DROP), 0.50, pan=0.1)
    sfx.add(stab([45, 57, 64, 69, 72], 1.4), tb(DROP), 0.55)

    # ---- final hit + tail (CTA) ----
    t_cta = tb(CTA_B)
    drums.add(K, t_cta, 1.0)
    kick_times.append(t_cta)
    sfx.add(sub_boom(2.4), t_cta, 1.0)
    sfx.add(crash(3.2, 22), t_cta, 0.55, pan=-0.1)
    sfx.add(stab([45, 57, 64, 69, 72, 83], 2.6, 4200), t_cta, 0.62)
    cp_l, cp_r = pad([57, 60, 64, 71], tb(TOTAL_BEATS - CTA_B) - 0.1, cutoff=4200, rel=0.9, att=0.25)
    music.add_st(cp_l, cp_r, t_cta, 0.45)
    for i in range(8, 8 + 13):  # sparse sparkle tail, fading
        bb = CTA_B + 0.5 * i * 0.5 + 0.5
        m = CHORDS["Am"]["arp"][[3, 2, 1, 2, 3, 1][i % 6]] + (12 if i % 3 == 0 else 0)
        music.add(arp_note(m, 0.6), tb(bb), max(0.03, 0.26 - 0.017 * (i - 8)), pan=-0.5 if i % 2 else 0.5)

    # ---- gates: hard silence just before the drop and the CTA hit ----
    gate = [1.0] * N
    for g0, g1 in ((DROP - gap, DROP), (CTA_B - gap, CTA_B)):
        a, b = int(tb(g0) * SR), int(tb(g1) * SR)
        r = int(0.006 * SR)
        for i in range(a - r, a):
            gate[i] = min(gate[i], (a - i) / r)
        for i in range(a, b):
            gate[i] = 0.0
    for bus in (drums, bass, music):
        bus.mul(gate)

    # ---- sidechain from the kick: pad/arp/bass duck, then pump back ----
    cn = int(0.36 * SR)
    curve = [1 - 0.6 * math.exp(-(i / SR) / 0.085) for i in range(cn)]
    curve_b = [1 - 0.85 * math.exp(-(i / SR) / 0.07) for i in range(cn)]
    duck_m, duck_b = [1.0] * N, [1.0] * N
    for t in kick_times:
        i0 = int(round(t * SR))
        m = min(cn, N - i0)
        duck_m[i0:i0 + m] = [min(a, b) for a, b in zip(duck_m[i0:i0 + m], curve[:m])]
        duck_b[i0:i0 + m] = [min(a, b) for a, b in zip(duck_b[i0:i0 + m], curve_b[:m])]
    music.mul(duck_m)
    bass.mul(duck_b)

    # ---- SFX tied to the picture (timeline.json events) ----
    notes_hz = {"A5": 880.0, "C6": 1046.5, "E6": 1318.5, "A6": 1760.0, "E5": 659.3}
    P = {k: ping(v, seed=70 + i) for i, (k, v) in enumerate(notes_hz.items())}
    TICK = [tick(2300 + 160 * i, seed=80 + i) for i in range(6)]

    # whooshes on the cuts (new scene enters)
    sp = {"curtainUp": (160, 5200, 0.5, 0, 0), "pushLeft": (220, 4800, 0.5, 0.8, -0.8), "iris": (350, 5800, 0.6, 0, 0),
          "curtainLeft": (200, 5000, 0.5, 0.8, -0.8), "zoom": (260, 5400, 0.62, 0, 0), "pushUp": (160, 4600, 0.5, 0, 0),
          "curtainRight": (200, 5000, 0.5, -0.8, 0.8)}
    for i, sc in enumerate(TL["scenes"]):
        if sc["trans"] is None or sc["id"] == "reveal":
            continue
        f0, f1, pk, p0, p1 = sp[sc["trans"]]
        g = 0.34 if sc["id"] != "cta" else 0.22
        sfx.add_sweep(whoosh(0.62, f0, f1, 100 + i, pk), tb(SC_START[i]) - 0.30, g, p0, p1)

    # hook: glitch strike on the crossed-out word
    sfx.add(glitch_strike(), tb(ev("hook", "strike")), 0.55)

    # reveal: icon lands, version chip
    sfx.add(P["A6"], tb(ev("reveal", "icon")) + 0.25, 0.12)
    sfx.add(P["E6"], tb(ev("reveal", "chip")) + 0.05, 0.13, pan=0.2)
    sfx.add(TICK[2], tb(ev("reveal", "chip")), 0.20)

    # callout highlights: tick + ping
    def hl(beat, f_key, g=0.16, pan=0.0, ti=0):
        t = tb(beat)
        sfx.add(TICK[ti], t, 0.22, pan)
        sfx.add(P[f_key], t + 0.015, g, pan)

    hl(ev("run", "box1"), "A5")
    hl(ev("run", "box2"), "E6", pan=0.2, ti=1)
    for k, key in enumerate(("A5", "C6", "E6")):
        hl(ev("browsers", "tiles", k), key, g=0.13, pan=(-0.35, 0, 0.35)[k], ti=k)
    sfx.add_sweep(whoosh(0.5, 400, 4200, 150, 0.45), tb(ev("browsers", "zoom")) - 0.05, 0.22, -0.3, 0.3)
    hl(ev("browsers", "status"), "E6", pan=0.15, ti=3)

    # RAM counters: a tick every time the displayed number steps (mirrors app.js outCubic count-up)
    for name, dur, steps, pan in (("bar1", 0.7, 12, -0.25), ("bar2", 0.8, 14, 0.25)):
        t0 = tb(ev("ram", name))
        for k in range(1, steps + 1):
            p = 1 - (1 - k / steps) ** (1 / 3)
            sfx.add(TICK[k % 6], t0 + dur * p, 0.07 + 0.10 * (k / steps), pan)
    t_b = tb(ev("ram", "badge")) + 0.165
    sfx.add(pop(900, 180), t_b, 0.55)
    sfx.add(P["A5"], t_b, 0.14)
    sfx.add(TICK[4], tb(ev("ram", "foot")), 0.14)

    # data: lines, highlights, theme flip
    for k in range(3):
        sfx.add(TICK[k], tb(ev("data", "lines", k)) + 0.03, 0.10, pan=-0.1)
    hl(ev("data", "box1"), "A5", ti=1)
    hl(ev("data", "box2"), "C6", ti=2)
    hl(ev("data", "box3"), "E6", pan=0.2, ti=3)
    t_f = tb(ev("data", "flip"))
    sfx.add_sweep(whoosh(0.55, 900, 5800, 160, 0.4, q1=0.3), t_f - 0.02, 0.30, -0.5, 0.5)
    sfx.add(TICK[5], t_f, 0.30)
    sfx.add(P["A6"], t_f + 0.1, 0.12)
    sfx.add(P["E6"], tb(ev("data", "chip")) + 0.05, 0.12, pan=-0.2)

    # export: button highlight, then JSON / CSV cards pop in
    hl(ev("export", "box"), "E6", ti=2)
    for name, pan in (("card1", -0.3), ("card2", 0.3)):
        t = tb(ev("export", name))
        sfx.add_sweep(whoosh(0.3, 500, 3800, 170, 0.5), t - 0.03, 0.2, -pan, pan)
        sfx.add(pop(780 if pan < 0 else 980, 200), t + 0.24, 0.50, pan)

    # ticker: four words slam in
    for k in range(4):
        t = tb(ev("ticker", "rows", k))
        sfx.add(thwack(90 + k), t + 0.02, 0.50, pan=(-0.3, 0.3, -0.3, 0.3)[k])
        sfx.add(arp_note(CHORDS["Am"]["arp"][[0, 1, 2, 3][k]] + 12, 1.0), t + 0.02, 0.22, pan=(-0.3, 0.3, -0.3, 0.3)[k])

    # CTA: download arrow, URL wipe, chips
    sfx.add(pop(700, 260), tb(ev("cta", "dl")) + 0.14, 0.45)
    for j, dt in enumerate((0.0, 0.25)):
        sfx.add_sweep(whoosh(0.22, 1200, 5600, 180 + j, 0.4, q1=0.3), tb(ev("cta", "url") + dt) - 0.02, 0.17, -0.6, 0.6)
    for k, key in enumerate(("A5", "C6", "E6")):
        t = tb(ev("cta", "chips", k)) + 0.08
        sfx.add(TICK[k], t, 0.18)
        sfx.add(P[key], t + 0.01, 0.12, pan=(-0.3, 0, 0.3)[k])

    sfx.gain(1.25)  # SFX sit ~2 dB up so ticks/whooshes stay audible over the groove

    # ---- reverb sends ----
    sendto(music, 0.30)
    sendto(sfx, 0.22)
    snare_like = Bus(N)
    snare_like.L = drums.L[:]
    snare_like.R = drums.R[:]
    sendto(snare_like, 0.07)
    wet_l = reverb_chan(send.L, [1687, 1789, 1951, 2111])
    wet_r = reverb_chan(send.R, [1721, 1847, 1993, 2161])

    # ---- master bus ----
    L = [a + b + c + d + w for a, b, c, d, w in zip(drums.L, bass.L, music.L, sfx.L, wet_l)]
    Rr = [a + b + c + d + w for a, b, c, d, w in zip(drums.R, bass.R, music.R, sfx.R, wet_r)]
    build.parts = {"drums": drums, "bass": bass, "music": music, "sfx": sfx}  # for level inspection
    return L, Rr


def master(L, Rr, target_lufs: float, out: Path, ceiling_db=-1.6):
    # fade in 50 ms, natural tail with a short cosine fade to silence at the very end of the video
    fi = int(0.05 * SR)
    for i in range(fi):
        g = 0.5 - 0.5 * math.cos(math.pi * i / fi)
        L[i] *= g
        Rr[i] *= g
    fo0, fo1 = int((TOTAL_S - 0.6) * SR), int(TOTAL_S * SR)
    for i in range(fo0, min(N, fo1)):
        g = 0.5 + 0.5 * math.cos(math.pi * (i - fo0) / (fo1 - fo0))
        L[i] *= g
        Rr[i] *= g
    for i in range(fo1, N):
        L[i] = Rr[i] = 0.0
    pk0 = max(max(map(abs, L)), max(map(abs, Rr)))
    L, Rr = compress(L, Rr)
    ceil = 10 ** (ceiling_db / 20)
    gdb = -6.0 - 20 * math.log10(max(pk0, 1e-6))  # starting guess; refined by measurement
    res = None
    for it in range(6):
        g = 10 ** (gdb / 20)
        lL, lR = limit([x * g for x in L], [x * g for x in Rr], ceil)
        write_wav(out, lL, lR)
        I, lra, tp = measure(out)
        print(f"  pass {it}: gain {gdb:+.2f} dB -> {I:.2f} LUFS, true peak {tp:.2f} dBTP, LRA {lra:.1f}", flush=True)
        res = (I, lra, tp)
        if abs(I - target_lufs) <= 0.15 and tp <= -1.0:
            break
        gdb += target_lufs - I
        if tp > -1.0:
            ceiling_db -= max(0.3, tp + 1.0 + 0.1)
            ceil = 10 ** (ceiling_db / 20)
    return res


def report_alignment():
    print("scene cuts vs nearest beat (BPM %g, beat %.5f s):" % (BPM, BEAT))
    for i, sid in enumerate(SC_IDS):
        t = tb(SC_START[i])
        nb = round(t / BEAT)
        print(f"  {sid:9s} cut {t:8.4f}s  nearest beat #{nb} at {nb * BEAT:8.4f}s  delta {abs(t - nb * BEAT) * 1000:.3f} ms"
              f"  (beat {SC_START[i]:g}, bar {SC_START[i] / 4:.2f})")
    print(f"  end       {TOTAL_S:8.4f}s  ({TOTAL_BEATS:g} beats)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=-14.0, help="integrated loudness target (LUFS)")
    ap.add_argument("--out", default=str(OUT / "soundtrack.wav"))
    a = ap.parse_args()
    report_alignment()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    print("synthesising...", flush=True)
    L, Rr = build()
    print("mastering...", flush=True)
    I, lra, tp = master(L, Rr, a.target, Path(a.out))
    print(f"wrote {a.out}  {DUR_S:.3f}s  {I:.2f} LUFS  {tp:.2f} dBTP  LRA {lra:.1f}")
