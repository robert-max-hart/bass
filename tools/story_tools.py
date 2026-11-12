#!/usr/bin/env python3
"""
story_tools.py - helpers for writing bass story files.

  convert       Draft a story .json from a manuscript (.odt, .txt or .md)
  check         Validate a story .json (links, line length, images, engine support)
  placeholders  Make placeholder images for every image a story references but that doesn't exist yet
  finalize      Turn raw generator output (stories/<name>/raw/) into game-ready art (stories/<name>/art/)
  relink        Point the story at the best image available: art/, then originals/, then placeholders/

Run `story_tools.py <command> -h` for options. Only the standard library is required;
Pillow (PIL) is optional and enables accurate text measuring, image-size checks and placeholders.
See tools/README.md for the manuscript directive syntax.
"""
import argparse
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # optional
    Image = ImageDraw = ImageFont = None

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---- engine constants (src/main.js, src/ui/dialog.js) ----
CANVAS_W, CANVAS_H = 1170, 540
TEXT_WIDTH = 1042 - 32          # dialog box width minus padding
FONT_PX = 24                    # "24px Verdana"
ITALIC_THRESHOLD = 0.6          # fraction of italic letters that makes an event italic

KNOWN_EVENT_KEYS = {
    "label", "name", "dialog", "italics", "bold", "next", "chapter", "wait", "sound_effect",
    "background", "background_clear", "foreground", "foreground_clear", "effects", "effects_clear",
    "fadein", "fadeout", "choice", "voice_line",
}
IMAGE_KEYS = ("background", "foreground", "effects")


# =====================================================================
# Text with a per-character italic mask
# =====================================================================
class T:
    """A string plus a parallel '0'/'1' mask marking italic characters; slices and joins keep both in step."""
    __slots__ = ("s", "m")

    def __init__(self, s="", m=None):
        self.s = s
        self.m = m if m is not None else "0" * len(s)

    def __len__(self):
        return len(self.s)

    def __getitem__(self, k):
        return T(self.s[k], self.m[k])

    def __add__(self, o):
        return T(self.s + o.s, self.m + o.m)

    def strip(self):
        a = len(self.s) - len(self.s.lstrip())
        b = len(self.s.rstrip())
        return self[a:b] if b > a else T()

    def italic_fraction(self):
        n = i = 0
        for ch, m in zip(self.s, self.m):
            if ch.isalnum():
                n += 1
                i += (m == "1")
        return i / n if n else 0.0

    def set_char(self, idx, ch):
        return T(self.s[:idx] + ch + self.s[idx + 1:], self.m)


def tjoin(parts, sep=" "):
    out = T()
    for i, p in enumerate(parts):
        if i:
            out = out + T(sep, "0")
        out = out + p
    return out


def collapse(t):
    """Collapse whitespace runs to single spaces and strip, keeping the mask aligned."""
    s, m, prev_space = [], [], True
    for ch, mk in zip(t.s, t.m):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        s.append(ch)
        m.append(mk)
    r = T("".join(s), "".join(m))
    return r.strip()


# =====================================================================
# Text measuring (mirrors the engine's word-wrap)
# =====================================================================
class Measure:
    DIRS = ["/usr/share/fonts", str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts"),
            "/Library/Fonts", "/System/Library/Fonts/Supplemental", "C:\\Windows\\Fonts"]
    FILES = {  # (italic, bold) -> candidate file names, Verdana first (what the engine asks for)
        (False, False): ["verdana.ttf", "Verdana.ttf", "DejaVuSans.ttf"],
        (True, False): ["verdanai.ttf", "Verdana Italic.ttf", "DejaVuSans-Oblique.ttf"],
        (False, True): ["verdanab.ttf", "Verdana Bold.ttf", "DejaVuSans-Bold.ttf"],
        (True, True): ["verdanaz.ttf", "Verdana Bold Italic.ttf", "DejaVuSans-BoldOblique.ttf"],
    }

    def __init__(self, margin=0.95):
        self.limit = TEXT_WIDTH * margin
        self.fonts = {}
        self.exact = False
        if ImageFont is not None:
            for key, names in self.FILES.items():
                path = self._find(names)
                if path:
                    try:
                        self.fonts[key] = ImageFont.truetype(path, FONT_PX)
                    except OSError:
                        pass
            self.exact = (False, False) in self.fonts

    def _find(self, names):
        for name in names:
            for d in self.DIRS:
                if not os.path.isdir(d):
                    continue
                for dp, _, files in os.walk(d):
                    if name in files:
                        return os.path.join(dp, name)
        return None

    def width(self, text, italic=False, bold=False):
        f = self.fonts.get((italic, bold)) or self.fonts.get((False, False))
        return f.getlength(text) if f else len(text) * 14.0

    def lines(self, text, italic=False, bold=False):
        """Number of lines the engine would wrap `text` onto (same algorithm as STRINGUTILS.GetLines)."""
        words = text.split(" ")
        cur, n = words[0], 1
        for w in words[1:]:
            if self.width(cur + " " + w, italic, bold) < self.limit:
                cur += " " + w
            else:
                n += 1
                cur = w
        return n


# =====================================================================
# Reading manuscripts
# =====================================================================
NS_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS_STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
NS_FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def read_odt(path, bold=False):
    """paragraphs as ('p'|'h', T). The mask is '1' for italic; with bold=True it is a style code instead:
    '0' plain, '1' italic, '2' bold, '3' bold italic (only voice_lines.py asks for bold)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    styles = {}
    for s in root.iter(f"{{{NS_STYLE}}}style"):
        tp = s.find(f"{{{NS_STYLE}}}text-properties")
        ital = tp is not None and tp.get(f"{{{NS_FO}}}font-style") == "italic"
        bld = tp is not None and tp.get(f"{{{NS_FO}}}font-weight") in ("bold", "600", "700", "800", "900")
        styles[s.get(f"{{{NS_STYLE}}}name")] = (ital, bld, s.get(f"{{{NS_STYLE}}}parent-style-name"))

    def has(name, which, depth=0):
        if not name or name not in styles or depth > 8:
            return False
        v = styles[name]
        return v[which] or has(v[2], which, depth + 1)

    def inline(el, st, parts):
        if el.text:
            parts.append((el.text, st))
        for c in el:
            tag = c.tag.rsplit("}", 1)[-1]
            if tag == "s":
                parts.append((" " * int(c.get(f"{{{NS_TEXT}}}c", "1")), st))
            elif tag in ("tab", "line-break"):
                parts.append((" ", st))
            elif tag != "note":
                cs = st
                if tag == "span":
                    name = c.get(f"{{{NS_TEXT}}}style-name")
                    cs = (st[0] or has(name, 0), st[1] or has(name, 1))
                inline(c, cs, parts)
            if c.tail:
                parts.append((c.tail, st))

    body = root.find(f".//{{{NS_OFFICE}}}text")
    items = []
    for el in body.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("p", "h"):
            continue
        parts = []
        name = el.get(f"{{{NS_TEXT}}}style-name")
        inline(el, (has(name, 0), has(name, 1)), parts)
        t = T()
        for txt, (ital, bld) in parts:
            code = str(int(ital) + 2 * int(bld)) if bold else ("1" if ital else "0")
            t = t + T(txt, code * len(txt))
        items.append(("h" if tag == "h" else "p", t))
    return items


def read_plain(path, bold=False):
    """.txt / .md: one paragraph per non-empty line. '# ' lines are headings, *text* is italic.
    With bold=True, **text** is bold and the mask is a style code as in read_odt."""
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        kind = "p"
        if re.match(r"#{1,6}\s", line) and not re.match(r"#{3,}\s*$", line):
            kind, line = "h", re.sub(r"^#+\s*", "", line)
        if re.fullmatch(r"\s*(\*\s*){3,}", line):  # '* * *' scene break, not italics
            items.append((kind, T(line.strip())))
            continue
        t = T()
        if bold:
            toks = re.split(r"(\*\*|\*)", line)
            singles_ok = toks.count("*") % 2 == 0
            doubles_ok = toks.count("**") % 2 == 0
            ital = b = False
            for tok in toks:
                if tok == "**" and doubles_ok:
                    b = not b
                elif tok == "*" and singles_ok:
                    ital = not ital
                elif tok:
                    t = t + T(tok, str(int(ital) + 2 * int(b)) * len(tok))
        else:
            for i, piece in enumerate(line.split("*")):
                ital = (i % 2 == 1) and line.count("*") % 2 == 0
                t = t + T(piece, ("1" if ital else "0") * len(piece))
        items.append((kind, t))
    return items


def read_manuscript(path, bold=False):
    ext = Path(path).suffix.lower()
    if ext == ".odt":
        return read_odt(path, bold)
    if ext in (".txt", ".md", ".markdown"):
        return read_plain(path, bold)
    sys.exit(f"Unsupported manuscript type '{ext}' (use .odt, .txt or .md)")


# =====================================================================
# Source lint
# =====================================================================
def lint_source(items):
    notes = []
    straight = curly = 0
    for kind, t in items:
        raw = t.s
        txt = collapse(t).s
        if not txt:
            continue
        snip = txt[:40]
        if "  " in raw.strip():
            notes.append(f"double space: \"{snip}…\"")
        for m in re.finditer(r"\b(\w+)\s+\1\b", txt, re.I):
            if m.group(1).lower() not in ("had", "that"):
                notes.append(f"repeated word '{m.group(1)} {m.group(1)}': \"{snip}…\"")
        if re.search(r"\s[,.;:!?]", txt):
            notes.append(f"space before punctuation: \"{snip}…\"")
        straight += txt.count('"')
        curly += txt.count("“") + txt.count("”")
        if kind == "p" and len(txt) <= 3 and not re.fullmatch(r"[-*#—–]{3,}|\[.*\]", txt):
            notes.append(f"stray very short paragraph \"{txt}\" (skipped)")
    if straight and curly:
        notes.append(f"mixed straight ({straight}) and curly ({curly}) double quotes")
    full = " ".join(collapse(t).s for _, t in items)
    for sur in set(re.findall(r"\bDr\.? ([A-Z][a-z]+)", full)) & set(re.findall(r"\bDoctor ([A-Z][a-z]+)", full)):
        notes.append(f"'Dr. {sur}' and 'Doctor {sur}' are both used")
    return notes


# =====================================================================
# Speakers
# =====================================================================
TITLES = r"(?:Doctor|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Miss|Professor|Prof\.?|Captain|Detective|Officer|Nurse)"
TITLE_NORMAL = {"dr": "Doctor", "doctor": "Doctor", "mr": "Mr.", "mrs": "Mrs.", "ms": "Ms.", "prof": "Professor"}
THE_TITLES = {"Doctor": "doctor", "Professor": "professor", "Captain": "captain", "Detective": "detective",
              "Officer": "officer", "Nurse": "nurse"}
STOP_NAMES = {"The", "And", "But", "She", "He", "They", "Her", "His", "Yes", "Now", "Then", "Just", "Well",
              "January", "February", "March", "April", "June", "July", "August", "September", "October",
              "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
SPEECH_VERBS = ("said|says|replied|asked|whispered|murmured|uttered|responded|recited|inquired|commented|"
                "insisted|continued|remarked|blurted|intoned|echoed|added|answered|called|muttered|drawled|"
                "persisted|exclaimed|spoke|went on|agreed|suggested|explained|announced|cried|shouted|yelled|"
                "stammered|interrupted|snapped|declared|noted|observed|demanded|admitted|confessed")


class Speaker:
    def __init__(self, canon, extra=()):
        self.canon = canon
        parts = [re.escape(canon)]
        m = re.fullmatch(rf"({TITLES})\s+([A-Z]\w+)", canon)
        if m:
            sur = m.group(2)
            parts += [rf"{TITLES}\s+{sur}", sur]
            norm = TITLE_NORMAL.get(m.group(1).rstrip(".").lower(), m.group(1))
            if norm in THE_TITLES:
                parts.append(rf"(?i:the {THE_TITLES[norm]})")
        parts += [re.escape(a) for a in extra]
        self.pat = r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])"
        self.rx = re.compile(self.pat)

    def search(self, s):
        return self.rx.search(s)


def detect_speakers(text, min_count=3):
    titled = {}
    for m in re.finditer(rf"\b({TITLES})\s+([A-Z][a-z]+)", text):
        title, sur = m.groups()
        norm = TITLE_NORMAL.get(title.rstrip(".").lower(), title.rstrip("."))
        titled.setdefault(sur, Counter())[f"{norm} {sur}"] += 1
    out = []
    for sur, forms in titled.items():
        if sum(forms.values()) >= 2:
            out.append((sum(forms.values()), forms.most_common(1)[0][0]))
    cleaned = re.sub(rf"\b{TITLES}\s+[A-Z][a-z]+", " ", text)
    title_words = {w.rstrip(".") for w in re.findall(r"[A-Za-z]+\.?", TITLES.replace("\\", ""))}
    counts = Counter(m.group(1) for m in re.finditer(r"(?<=[a-z,;] )([A-Z][a-z]{2,})(?:[’']s)?\b", cleaned))
    for m in re.finditer(rf"\b([A-Z][a-z]{{2,}})\s+(?:{SPEECH_VERBS})\b|\b(?:{SPEECH_VERBS})\s+([A-Z][a-z]{{2,}})\b", cleaned):
        counts[m.group(1) or m.group(2)] += 3  # 'Mara said' / 'said Mara' is strong evidence of a speaker
    for name, n in counts.items():
        if n >= min_count and name not in STOP_NAMES and name not in titled and name not in title_words:
            out.append((n, name))
    out.sort(key=lambda x: -x[0])
    return [Speaker(name) for _, name in out]


def parse_speaker_specs(specs):
    out = []
    for spec in specs:
        canon, _, extra = spec.partition("=")
        out.append(Speaker(canon.strip(), [a.strip() for a in extra.split("|") if a.strip()]))
    return out


# =====================================================================
# Sentence handling
# =====================================================================
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "jr", "sr", "no"}


def sentence_spans(s):
    """(start, end) spans of sentences in plain string s."""
    cuts = []
    for m in re.finditer(r"(?:[.!?]+|…)[\"”’)]*(\s+)", s):
        nxt = s[m.end():m.end() + 1]
        if not (nxt.isupper() or nxt in "“\"‘(" or nxt.isdigit()):
            continue
        term = m.group(0).strip()
        if ("…" in term or "..." in term) and re.match(r"I\b", s[m.end():]):
            continue  # "Yes… I understand" is one sentence
        if term == ".":
            prev = re.search(r"(\w+)\.\s*$", s[:m.start() + 1])
            if prev and (prev.group(1).lower() in ABBREVIATIONS or len(prev.group(1)) == 1):
                continue
        cuts.append((m.start(1), m.end()))
    spans, start = [], 0
    for ws, end in cuts:
        spans.append((start, ws))
        start = end
    spans.append((start, len(s)))
    return [(a, b) for a, b in spans if b > a]


def split_sentences(t):
    return [t[a:b] for a, b in sentence_spans(t.s)]


# =====================================================================
# Converter
# =====================================================================
CHAPTER_RX = re.compile(r"^\s*chapter\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b\s*[-–—:.]?\s*(.*)$", re.I)
SCENE_BREAK_RX = re.compile(r"^\s*(?:-{3,}|\*{3,}|(?:\*\s+){2,}\*|#{3,}|—{3,}|_{3,})\s*$")
DIRECTIVE_PARA_RX = re.compile(r"^(?:\s*\[[^\]]*\])+\s*$")


class Converter:
    def __init__(self, args, speakers, measure, image_dir):
        self.a = args
        self.speakers = speakers
        self.me = measure
        self.image_dir = image_dir
        names = "|".join(sp.pat for sp in speakers) or "(?!)"
        self.subj = rf"(?:{names}|she|he|they|I|the \w+)"
        adv = r"(?:\w+ly|again|back|out|up|aloud|then|too)"
        self.attr_rx = re.compile(
            rf"^(?:{self.subj}\s+(?:\w+ly\s+)?(?:{SPEECH_VERBS})(?:\s+{adv})*|(?:{SPEECH_VERBS})\s+{self.subj})$", re.I)
        self.thought_tag_rx = re.compile(rf"^(?P<body>.+?),?\s+{self.subj}\s+thought(?P<end>[.!?…]*)$", re.I | re.S)
        self.thought_lead_rx = re.compile(rf"^{self.subj}\s+thought,?\s+(?P<rest>.+)$", re.I | re.S)
        self.chapters = [{"label": None, "events": []}]
        self.needs_fadein = True
        self.pending = {}
        self.hist = []              # speakers in order of appearance
        self.last_subject = None
        self.prev_para_had_quote = False
        self.review = []            # (event, note)
        self.stats = Counter()
        self.chapter_started = False
        self.warnings = []

    # ---------- plumbing ----------
    @property
    def events(self):
        return self.chapters[-1]["events"]

    def image_path(self, v):
        if re.match(r"^(\./|/|https?://|data:)", v):
            return v
        return self.image_dir + v

    def attach_pending(self, ev):
        for k, v in self.pending.items():
            ev.setdefault(k, v)
        self.pending = {}

    def emit(self, ev):
        silent = "dialog" not in ev
        if self.needs_fadein:
            if silent and ev.get("fadein"):
                pass
            else:
                fi = {"label": "Fade In", "wait": "800", "fadein": True}
                self.attach_pending(fi)
                self.events.append(fi)
            self.needs_fadein = False
        self.attach_pending(ev)
        self.events.append(ev)
        self.chapter_started = True
        if ev.get("fadeout") and "chapter" not in ev:
            self.needs_fadein = True
        return ev

    def snippet(self, t, n=5):
        w = t.split()
        return " ".join(w[:n]) + ("…" if len(w) > n else "")

    # ---------- speakers ----------
    def first_name(self, s):
        best = None
        for sp in self.speakers:
            m = sp.search(s)
            if m and (best is None or m.start() < best[1]):
                best = (sp.canon, m.start())
        return best

    def other_speaker(self):
        if not self.hist:
            return None
        for sp in reversed(self.hist):
            if sp != self.hist[-1]:
                return sp
        return None

    def decide_speaker(self, segs, i, qtext):
        """Returns (speaker or None, rule). segs[i] is the quote."""
        sp, rule = self._decide_speaker(segs, i, qtext)
        if (sp is None or "guess" in rule) and len(self.speakers) == 2:
            # a quote that addresses one of the two speakers by name is spoken by the other one
            named = [s.canon for s in self.speakers if s.search(qtext.s)]
            if len(named) == 1:
                other = next(s.canon for s in self.speakers if s.canon != named[0])
                return other, "addressed by name (guess)"
        return sp, rule

    def _decide_speaker(self, segs, i, qtext):
        before = segs[i - 1] if i > 0 and segs[i - 1]["kind"] == "n" else None
        after = segs[i + 1] if i + 1 < len(segs) and segs[i + 1]["kind"] == "n" else None
        prev_quote = i > 0 and segs[i - 1]["kind"] == "q"

        m = re.search(r"\b(?:I[’']m|I am|[Mm]y name(?: is|[’']s)|[Tt]his is)\s+(.{0,40})", qtext.s)
        if m:
            fn = self.first_name(m.group(1))
            if fn and fn[1] <= 2:
                return fn[0], "self-introduction"
        if after:
            first = split_sentences(after["t"].strip())
            first = first[0].s if first else ""
            fn = self.first_name(first)
            if fn and fn[1] <= 15 and re.search(rf"\b(?:{SPEECH_VERBS})\b", first[:70], re.I):
                return fn[0], "name + speech verb after quote"
        if before:
            sents = split_sentences(before["t"].strip())
            last = sents[-1].s if sents else ""
            fn = self.first_name(last)
            if fn:
                return fn[0], "subject of narration before quote"
        if after:
            first = split_sentences(after["t"].strip())
            first = first[0].s if first else ""
            fn = self.first_name(first)
            if fn and fn[1] <= 15:
                return fn[0], "name after quote"
        for nb, label in ((before, "before"), (after, "after")):
            if nb and re.match(r"^\W*(?:she|he|they)\b", nb["t"].strip().s if label == "after" else
                               (split_sentences(nb["t"].strip()) or [T()])[-1].s, re.I):
                if self.last_subject:
                    return self.last_subject, f"pronoun {label} quote (guess)"
        if prev_quote and self.hist:
            return self.hist[-1], "continues previous quote"
        if self.prev_para_had_quote:
            sp = self.other_speaker()
            if sp:
                return sp, "turn-taking (guess)"
        elif self.last_subject:
            return self.last_subject, "subject of preceding narration (guess)"
        sp = self.other_speaker()
        if sp:
            return sp, "turn-taking (guess)"
        return None, "unknown"

    # ---------- dialog events ----------
    def fit(self, t, italic):
        """Split text t into pieces that each fit in max_lines."""
        if self.me.lines(t.s, italic) <= self.a.max_lines:
            return [t]
        pieces, start = [], 0
        for m in re.finditer(r"(?:[,;:]|\s[-–—]\s|…)\s+", t.s):
            pieces.append(t[start:m.end()].strip())
            start = m.end()
        pieces.append(t[start:].strip())
        pieces = [p for p in pieces if len(p)]
        out, cur = [], None
        for p in pieces:
            cand = p if cur is None else tjoin([cur, p])
            if cur is not None and self.me.lines(cand.s, italic) > self.a.max_lines:
                out.append(cur)
                cur = p
            else:
                cur = cand
        if cur is not None:
            out.append(cur)
        final = []
        for p in out:  # last resort: break on words
            if self.me.lines(p.s, italic) <= self.a.max_lines:
                final.append(p)
                continue
            words, cur = p.s.split(" "), None
            pos = 0
            for w in words:
                seg = p[pos:pos + len(w)]
                pos += len(w) + 1
                cand = seg if cur is None else tjoin([cur, seg])
                if cur is not None and self.me.lines(cand.s, italic) > self.a.max_lines:
                    final.append(cur)
                    cur = seg
                else:
                    cur = cand
            if cur is not None:
                final.append(cur)
        return final

    def chunks(self, sents, force_italic=False):
        """Pack sentences into dialog-box-sized chunks."""
        parts = []
        for s in sents:
            s = s.strip()
            if not re.search(r"\w", s.s):
                continue
            ital = force_italic or s.italic_fraction() >= ITALIC_THRESHOLD
            parts.extend(self.fit(s, ital))
        out, cur = [], []
        for p in parts:
            cand = tjoin(cur + [p])
            ital = force_italic or cand.italic_fraction() >= ITALIC_THRESHOLD
            if cur and (len(cur) >= self.a.max_sentences or self.me.lines(cand.s, ital) > self.a.max_lines):
                out.append(tjoin(cur))
                cur = [p]
            else:
                cur.append(p)
        if cur:
            out.append(tjoin(cur))
        return out

    def add_dialog(self, t, speaker=None, force_italic=False, note=None):
        t = t.strip()
        if t.s.endswith(","):
            t = t[:-1] + T(".", t.m[-1])
        frac = t.italic_fraction()
        italic = force_italic or frac >= ITALIC_THRESHOLD
        if 0 < frac < ITALIC_THRESHOLD:
            self.stats["partial italics ignored"] += 1
        ev = {"label": (f"{speaker}: " if speaker else "") + self.snippet(t.s)}
        if speaker:
            ev["name"] = speaker
        if italic:
            ev["italics"] = True
        ev["dialog"] = t.s
        self.emit(ev)
        if note:
            self.review.append((ev, note))
        return ev

    # ---------- narration helpers ----------
    def attribution_only(self, t):
        core = re.sub(r"[\s,.:;!?—–-]+$", "", t.s.strip())
        core = re.sub(r"^[\s,.:;—–-]+", "", core)
        return bool(core) and len(core.split()) <= 7 and bool(self.attr_rx.match(core))

    def thought_sentence(self, t):
        """If sentence t is a 'thought' (tagged 'she thought' or an italic lead-in), return the bare thought text."""
        m = self.thought_tag_rx.match(t.s)
        if m:
            body = t[:m.end("body")].strip()
            if body.s.endswith(","):
                body = body[:-1]
            if not re.search(r"[.!?…]$", body.s):
                body = body + T(m.group("end")[:1] or ".", "1")
            elif m.group("end") and body.s.endswith(","):
                pass
            return body
        m = self.thought_lead_rx.match(t.s)
        if m:
            rest = t[m.start("rest"):]
            if rest.italic_fraction() >= 0.5:
                if rest.s and rest.s[0].islower():
                    rest = rest.set_char(0, rest.s[0].upper())
                return rest
        return None

    def add_narration(self, t):
        sents = split_sentences(t.strip())
        buf = []

        def flush():
            for c in self.chunks(buf):
                self.add_dialog(c)
            buf.clear()

        for s in sents:
            fn = self.first_name(s.s)
            th = self.thought_sentence(s)
            if th is not None:
                flush()
                for c in self.chunks([th], force_italic=True):
                    self.add_dialog(c, force_italic=True)
                self.stats["thoughts"] += 1
            else:
                buf.append(s)
            if fn:
                self.last_subject = fn[0]
        flush()

    # ---------- paragraph processing ----------
    def split_quotes(self, par):
        segs, s, start, in_q = [], par.s, 0, False

        def add(kind, a, b, unclosed=False):
            t = par[a:b]
            if kind == "n" and not re.search(r"\w", t.s):
                return
            if kind == "q" and not t.s.strip():
                return
            segs.append({"kind": kind, "t": t, "unclosed": unclosed})

        for i, ch in enumerate(s):
            if not in_q and ch in "“\"":
                add("n", start, i)
                start, in_q = i + 1, True
            elif in_q and ch in "”\"":
                add("q", start, i)
                start, in_q = i + 1, False
        add("q" if in_q else "n", start, len(s), unclosed=in_q)
        if in_q:
            self.warnings.append(f"unclosed quotation mark: \"{self.snippet(par.s, 8)}\"")
        return segs

    def process_paragraph(self, par):
        segs = self.split_quotes(par)
        if not segs:
            return
        has_quote = any(sg["kind"] == "q" for sg in segs)
        # pass 1: decide speakers (sequentially, so last_subject tracks the narration)
        for i, sg in enumerate(segs):
            if sg["kind"] == "n":
                for s in split_sentences(sg["t"].strip()):
                    fn = self.first_name(s.s)
                    if fn:
                        self.last_subject = fn[0]
                continue
            sp, rule = self.decide_speaker(segs, i, sg["t"])
            sg["speaker"], sg["rule"] = sp, rule
            self.stats[f"speaker rule: {rule}"] += 1
            if sp:
                self.hist.append(sp)
                self.last_subject = sp
        # pass 2: drop attribution-only narration, merge split quotes
        items = []
        for i, sg in enumerate(segs):
            if sg["kind"] == "n":
                touches_quote = (i > 0 and segs[i - 1]["kind"] == "q") or (i + 1 < len(segs) and segs[i + 1]["kind"] == "q")
                if touches_quote and not self.a.keep_attributions and self.attribution_only(sg["t"]):
                    self.stats["attribution tags dropped"] += 1
                    items.append({"kind": "gap"})
                else:
                    items.append(sg)
            else:
                items.append(dict(sg))
        merged = []
        for it in items:
            if (it["kind"] == "q" and len(merged) >= 2 and merged[-1]["kind"] == "gap" and merged[-2]["kind"] == "q"
                    and merged[-2]["speaker"] == it["speaker"] and it["t"].s.lstrip()[:1].islower()
                    and merged[-2]["t"].s.rstrip().endswith(",")):
                merged.pop()
                prev = merged[-1]
                prev["t"] = tjoin([prev["t"].strip(), it["t"].strip()])
                self.stats["split quotes merged"] += 1
                continue
            merged.append(it)
        # pass 3: emit
        for it in merged:
            if it["kind"] == "gap":
                continue
            if it["kind"] == "n":
                self.add_narration(it["t"])
                continue
            sp = it["speaker"]
            note = None
            if sp is None:
                note = "speaker unknown"
            elif "guess" in it["rule"]:
                note = f"speaker guessed: {sp} ({it['rule']})"
            for k, c in enumerate(self.chunks(split_sentences(it["t"].strip()))):
                self.add_dialog(c, sp, note=note if k == 0 else None)
        self.prev_para_had_quote = has_quote

    # ---------- directives ----------
    def process_directive(self, text):
        for group in re.findall(r"\[([^\]]*)\]", text):
            ev, has_flow = {}, False
            for item in group.split(";"):
                item = item.strip()
                if not item:
                    continue
                km = re.match(r"(\w+)\s*(?::|\s)\s*(.*)$", item)  # 'bg: a.png', 'fadein 600', 'fadeout'
                key, val = (km.group(1).lower(), km.group(2).strip()) if km else (item.lower(), "")
                if key in ("bg", "background", "fg", "foreground", "fx", "effects"):
                    field = {"bg": "background", "fg": "foreground", "fx": "effects"}.get(key, key)
                    if val.lower() == "clear":
                        ev[field + "_clear"] = True
                    elif val:
                        ev[field] = self.image_path(val)
                    else:
                        self.warnings.append(f"directive '{item}' needs a value")
                elif key in ("sfx", "sound", "sound_effect"):
                    ev["sound_effect"] = self.image_path(val)
                elif key in ("fadein", "fadeout"):
                    ev[key], has_flow = True, True
                    if val.isdigit():
                        ev["wait"] = val
                elif key == "wait":
                    ev["wait"], has_flow = val, True
                elif key == "label":
                    ev["label"] = val
                else:
                    self.warnings.append(f"unknown directive '{item}' ignored")
            self.stats["directives"] += 1
            if has_flow:
                label = ev.pop("label", None) or ("Fade Out" if ev.get("fadeout") else "Fade In" if ev.get("fadein") else "Wait")
                ev = {"label": label, **ev}
                if "wait" not in ev and (ev.get("fadein") or ev.get("fadeout")):
                    ev["wait"] = "800"
                self.emit(ev)
            else:
                ev.pop("label", None)
                self.pending.update(ev)

    # ---------- structure ----------
    def end_chapter(self):
        idx = len(self.chapters)
        self.events.append({"label": f"End of chapter {idx}", "chapter": str(idx), "next": "0", "fadeout": True})
        self.chapters.append({"label": None, "events": []})
        self.needs_fadein = True
        self.chapter_started = False

    def run(self, items):
        for kind, raw in items:
            t = collapse(raw)
            if not t.s:
                continue
            if kind == "p" and len(t.s) <= 3 and not SCENE_BREAK_RX.match(t.s):
                self.warnings.append(f"skipped stray short paragraph \"{t.s}\"")
                continue
            cm = CHAPTER_RX.match(t.s)
            if kind == "h" or cm:
                label = (cm.group(1).strip() if cm and cm.group(1).strip() else t.s)
                if self.chapter_started:
                    self.end_chapter()
                if self.chapters[-1]["label"] is None:
                    self.chapters[-1]["label"] = label
                continue
            if SCENE_BREAK_RX.match(t.s):
                self.emit({"label": "Scene Break - Fade Out", "wait": "1000", "fadeout": True})
                self.stats["scene breaks"] += 1
                continue
            if DIRECTIVE_PARA_RX.match(t.s):
                self.process_directive(t.s)
                continue
            self.process_paragraph(t)
        if self.pending:
            self.warnings.append("trailing image directives had no event to attach to: " + ", ".join(self.pending))
        story_chapters = {}
        for ci, ch in enumerate(self.chapters):
            evs = {}
            for i, ev in enumerate(ch["events"]):
                ev.setdefault("next", str(i + 1))
                evs[str(i)] = ev
            story_chapters[str(ci)] = {"label": ch["label"] or f"Chapter {ci + 1}", "events": evs}
        return story_chapters


# =====================================================================
# Writing JSON
# =====================================================================
def j(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def dump_story(story):
    """JSON the engine and editor can load, but with one event per line so it diffs and hand-edits well."""
    out = ["{", f'"story_name":{j(story["story_name"])},']
    if story.get("title_data"):
        out.append(f'"title_data":{j(story["title_data"])},')
    out.append('"chapters":{')
    chs = list(story["chapters"].items())
    for ci, (ck, ch) in enumerate(chs):
        out.append(f'{j(ck)}:{{"label":{j(ch["label"])},"events":{{')
        evs = list(ch["events"].items())
        for ei, (ek, ev) in enumerate(evs):
            out.append(f"{j(ek)}:{j(ev)}" + ("," if ei < len(evs) - 1 else ""))
        out.append("}}" + ("," if ci < len(chs) - 1 else ""))
    out.append("}")
    out.append("}")
    return "\n".join(out) + "\n"


# =====================================================================
# convert
# =====================================================================
def cmd_convert(a):
    src = Path(a.source)
    items = read_manuscript(src)
    out_path = Path(a.out) if a.out else REPO_ROOT / "stories" / (src.stem.split("_chapter")[0] + ".json")
    if out_path.exists() and not (a.force or a.dry_run):
        sys.exit(f"{out_path} already exists; use --force to overwrite or --out to choose another path")
    full_text = " ".join(collapse(t).s for _, t in items)
    speakers = parse_speaker_specs(a.speakers) if a.speakers else detect_speakers(full_text)
    image_dir = a.image_dir or f"./stories/{out_path.stem}/"
    if not image_dir.endswith("/"):
        image_dir += "/"
    me = Measure(a.margin)
    conv = Converter(a, speakers, me, image_dir)
    chapters = conv.run(items)

    title = a.title
    if not title:
        title = out_path.stem.replace("_", " ").title()
    story = {"story_name": title, "chapters": chapters}
    if a.title_screen:
        story["title_data"] = {"title_screen": conv.image_path(a.title_screen)}

    n_events = sum(len(c["events"]) for c in chapters.values())
    n_dialog = sum("dialog" in e for c in chapters.values() for e in c["events"].values())
    print(f"Converted {src.name}: {len(chapters)} chapter(s), {n_events} events ({n_dialog} with dialog)")
    print("Speakers" + (" (given)" if a.speakers else " (auto-detected; override with --speakers)") + ": "
          + (", ".join(s.canon for s in speakers) or "none"))
    if not me.exact:
        print("note: Verdana/DejaVu fonts or Pillow not found - line lengths are estimated (use --margin to adjust)")
    for k, v in sorted(conv.stats.items()):
        print(f"  {k}: {v}")

    index = {id(e): (ck, ek) for ck, c in chapters.items() for ek, e in c["events"].items()}
    if conv.review:
        print(f"\nSpeaker attributions to review ({len(conv.review)}):")
        for ev, note in conv.review:
            ck, ek = index[id(ev)]
            print(f"  [{ck}:{ek}] {note} - \"{conv.snippet(ev['dialog'], 7)}\"")
    notes = lint_source(items) + conv.warnings
    if notes:
        print(f"\nSource notes ({len(notes)}):")
        for n in notes:
            print("  - " + n)

    if a.dry_run:
        print("\n(dry run: nothing written)")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dump_story(story), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


# =====================================================================
# check
# =====================================================================
def image_refs(story):
    """Yield (path, field, chapter, event_key, event) for every image / sound reference."""
    td = (story.get("title_data") or {}).get("title_screen")
    if td:
        yield td, "title_screen", None, None, None
    for ck, ch in (story.get("chapters") or {}).items():
        for ek, ev in (ch.get("events") or {}).items():
            if not isinstance(ev, dict):
                continue
            for f in IMAGE_KEYS + ("sound_effect",):
                if ev.get(f):
                    yield ev[f], f, ck, ek, ev


def is_remote(p):
    return bool(re.match(r"^(https?:)?//|^data:", p))


def resolve(root, p):
    return Path(root) / p[2:] if p.startswith("./") else Path(root) / p.lstrip("/")


def cmd_check(a):
    story_path = Path(a.story)
    try:
        story = json.loads(story_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"Cannot read {story_path}: {e}")
    root = Path(a.root) if a.root else REPO_ROOT
    errors, warns = [], []
    chapters = story.get("chapters")
    if not isinstance(chapters, dict) or not chapters:
        sys.exit("ERROR: no 'chapters' object (is this an old pre-chapter story file?)")
    if not story.get("story_name"):
        warns.append("no story_name")
    me = Measure(a.margin)

    def evs(ck):
        return (chapters.get(ck) or {}).get("events") or {}

    chap_keys = sorted(chapters, key=lambda k: int(k) if k.isdigit() else 1 << 30)
    last_chap = chap_keys[-1]
    for ck in chap_keys:
        events = evs(ck)
        keys = list(events)
        if not all(k.isdigit() for k in keys) or sorted(map(int, keys)) != list(range(len(keys))):
            warns.append(f"chapter {ck}: event ids are not contiguous 0..{len(keys) - 1} (the editor assumes they are)")
        for ek, ev in events.items():
            where = f"[{ck}:{ek}]"
            if not isinstance(ev, dict):
                errors.append(f"{where} event is not an object")
                continue
            for k in ev:
                if k not in KNOWN_EVENT_KEYS:
                    warns.append(f"{where} unknown field '{k}'")
            tgt_chap = str(ev["chapter"]) if "chapter" in ev else ck
            if "chapter" in ev and str(ev["chapter"]) not in chapters:
                errors.append(f"{where} chapter {ev['chapter']} does not exist")
            nxt = ev.get("next")
            if nxt is None:
                if "choice" not in ev:
                    warns.append(f"{where} has no 'next' - the engine will replay this event forever")
            else:
                try:
                    n = int(nxt)
                    tgt_events = evs(tgt_chap)
                    if str(n) not in tgt_events:
                        if n == len(tgt_events) and tgt_chap == last_chap:
                            pass  # normal end of story
                        elif n == len(tgt_events):
                            warns.append(f"{where} runs off the end of chapter {tgt_chap} (no chapter jump)")
                        else:
                            errors.append(f"{where} next={nxt} does not exist in chapter {tgt_chap}")
                except (TypeError, ValueError):
                    errors.append(f"{where} next={nxt!r} is not a number")
            if "choice" in ev:
                ch = ev["choice"]
                if not isinstance(ch, list):
                    errors.append(f"{where} 'choice' must be a list of {{text, target}} (old object format?)")
                else:
                    if len(ch) > 4:
                        warns.append(f"{where} has {len(ch)} choices; the engine shows only 4")
                    if "dialog" not in ev:
                        warns.append(f"{where} has choices but no dialog")
                    for c in ch:
                        if not isinstance(c, dict) or "text" not in c or "target" not in c:
                            errors.append(f"{where} malformed choice {c!r}")
                        elif str(c["target"]) not in evs(ck):
                            errors.append(f"{where} choice target {c['target']} does not exist")
            if "wait" in ev:
                try:
                    float(ev["wait"])
                except (TypeError, ValueError):
                    errors.append(f"{where} wait={ev['wait']!r} is not a number")
            if ev.get("fadein") and ev.get("fadeout"):
                warns.append(f"{where} has both fadein and fadeout (fadein wins)")
            if "dialog" in ev:
                d = ev["dialog"]
                if not isinstance(d, str) or not d.strip():
                    warns.append(f"{where} empty dialog")
                    continue
                if "\n" in d:
                    warns.append(f"{where} dialog contains a newline; the engine does not break lines on \\n")
                n = me.lines(d.replace("\n", " "), bool(ev.get("italics")), bool(ev.get("bold")))
                if n > a.max_lines:
                    warns.append(f"{where} dialog wraps to {n} lines (box fits {a.max_lines}): \"{d[:40]}…\"")

    # reachability
    seen, stack = set(), [("0" if "0" in chapters else chap_keys[0], "0")]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        ck, ek = node
        ev = evs(ck).get(ek)
        if not isinstance(ev, dict):
            continue
        seen.add(node)
        nc = str(ev["chapter"]) if "chapter" in ev else ck
        if "next" in ev:
            stack.append((nc, str(ev["next"])))
        if isinstance(ev.get("choice"), list):
            for c in ev["choice"]:
                if isinstance(c, dict) and "target" in c:
                    stack.append((ck, str(c["target"])))
    for ck in chap_keys:
        dead = [ek for ek in evs(ck) if (ck, ek) not in seen]
        if dead:
            warns.append(f"chapter {ck}: {len(dead)} unreachable event(s): {', '.join(dead[:12])}{' …' if len(dead) > 12 else ''}")

    # images
    missing, sized = Counter(), set()
    uses_effects = False
    for path, field, ck, ek, ev in image_refs(story):
        uses_effects |= field == "effects"
        if is_remote(path):
            continue
        p = resolve(root, path)
        where = "title_data" if ck is None else f"[{ck}:{ek}]"
        if not p.exists():
            missing[path] += 1
            continue
        if Image is not None and field in IMAGE_KEYS + ("title_screen",) and (path, field) not in sized:
            sized.add((path, field))
            try:
                with Image.open(p) as im:
                    w, h = im.size
            except OSError:
                warns.append(f"{where} cannot open image {path}")
                continue
            if field in ("background", "title_screen") and (w, h) != (CANVAS_W, CANVAS_H):
                warns.append(f"{where} {field} {path} is {w}x{h}; the engine stretches it to {CANVAS_W}x{CANVAS_H}")
            if field == "foreground" and (h > CANVAS_H or w > CANVAS_W):
                warns.append(f"{where} foreground {path} is {w}x{h}, larger than the {CANVAS_W}x{CANVAS_H} canvas (edges are clipped)")
            if field == "effects" and (w, h) != (CANVAS_W, CANVAS_H):
                warns.append(f"{where} effects {path} is {w}x{h}; effects are meant to be {CANVAS_W}x{CANVAS_H}")
    # voice lines (made by tools/voice_lines.py build)
    missing_voice, voiceless = [], []
    for ck, ch in (story.get("chapters") or {}).items():
        for ek, ev in (ch.get("events") or {}).items():
            if not isinstance(ev, dict) or not ev.get("voice_line"):
                continue
            if not ev.get("dialog"):
                voiceless.append(f"[{ck}:{ek}]")
            if not is_remote(ev["voice_line"]) and not resolve(root, ev["voice_line"]).exists():
                missing_voice.append(f"[{ck}:{ek}]")
    if missing_voice:
        warns.append(f"{len(missing_voice)} voice clip(s) missing (run `tools/voice_lines.py build`): "
                     + ", ".join(missing_voice[:8]) + (" …" if len(missing_voice) > 8 else ""))
    if voiceless:
        warns.append(f"voice_line on event(s) without dialog (never played): " + ", ".join(voiceless[:8]))
    if missing:
        warns.append(f"{len(missing)} referenced file(s) missing (run `placeholders` to create stand-ins): "
                     + ", ".join(sorted(missing)[:6]) + (" …" if len(missing) > 6 else ""))
    if uses_effects:
        em = root / "src" / "manager" / "event_manager.js"
        if em.exists() and '"effects"' not in em.read_text(encoding="utf-8") and "'effects'" not in em.read_text(encoding="utf-8") \
                and ".effects" not in em.read_text(encoding="utf-8"):
            warns.append("story uses 'effects' but the player (event_manager.js) does not handle them yet - see docs/effects_proposal.md")

    n_ev = sum(len(evs(ck)) for ck in chap_keys)
    print(f"{story_path.name}: {len(chap_keys)} chapter(s), {n_ev} events")
    for e in errors:
        print("ERROR  " + e)
    for w in warns:
        print("warn   " + w)
    if not errors and not warns:
        print("OK - no problems found")
    return 1 if errors else 0


# =====================================================================
# placeholders
# =====================================================================
def wrap_px(draw, text, font, width):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            cand = (cur + " " + w).strip()
            if cur and draw.textlength(cand, font=font) > width:
                lines.append(cur)
                cur = w
            else:
                cur = cand
        lines.append(cur)
    return lines


def load_font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf", "C:\\Windows\\Fonts\\arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()


def cmd_placeholders(a):
    if Image is None:
        sys.exit("placeholders needs Pillow (pip install pillow)")
    story = json.loads(Path(a.story).read_text(encoding="utf-8"))
    root = Path(a.root) if a.root else REPO_ROOT
    descs = json.loads(Path(a.descriptions).read_text(encoding="utf-8")) if a.descriptions else {}
    uses = {}
    for path, field, ck, ek, ev in image_refs(story):
        if field == "sound_effect" or is_remote(path):
            continue
        entry = uses.setdefault(path, {"kind": "background" if field in ("background", "title_screen") else field, "events": []})
        if ev is not None:
            entry["events"].append((ck, ek, ev))
    made = []
    for path, info in sorted(uses.items()):
        p = resolve(root, path)
        if p.exists():
            continue
        kind, name = info["kind"], p.name
        desc = descs.get(name) or descs.get(path) or ""
        first = info["events"][0] if info["events"] else None
        lines = [f"PLACEHOLDER - {kind.upper()}", name, ""]
        if desc:
            lines += [desc, ""]
        if first:
            ck, ek, ev = first
            lines.append(f"First used at [{ck}:{ek}] {ev.get('label', '')}")
            text = ev.get("dialog")
            if not text and ek.isdigit():  # silent scene-change event: borrow the next line of dialog for context
                for k in range(int(ek) + 1, int(ek) + 4):
                    nxt = ((story["chapters"].get(ck) or {}).get("events") or {}).get(str(k)) or {}
                    if nxt.get("dialog"):
                        text = nxt["dialog"]
                        break
            if text:
                lines.append("\"" + text[:110] + ("…" if len(text) > 110 else "") + "\"")
            if len(info["events"]) > 1:
                lines.append("Also used at " + ", ".join(f"[{c}:{k}]" for c, k, _ in info["events"][1:8]))
        if a.dry_run:
            made.append((path, kind, desc, info["events"]))
            continue
        if kind == "background":
            im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (42, 47, 58, 255))
            d = ImageDraw.Draw(im)
            d.rectangle([20, 20, CANVAS_W - 21, CANVAS_H - 21], fill=(58, 65, 80, 255))
            box, fill = (60, 50, CANVAS_W - 60), (216, 220, 230, 255)
        elif kind == "foreground":
            im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle([310, 90, 860, 470], 24, fill=(192, 96, 128, 120), outline=(255, 159, 192, 255), width=3)
            box, fill = (330, 110, 840), (255, 224, 236, 255)
        else:
            im = Image.new("RGBA", (CANVAS_W, CANVAS_H), (96, 192, 255, 40))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=(150, 220, 255, 255), width=3)
            box, fill = (60, 50, CANVAS_W - 60), (191, 230, 255, 255)
        f_big, f_small = load_font(26), load_font(20)
        x, y, right = box
        for i, ln in enumerate(lines):
            font = f_big if i < 2 else f_small
            for wl in wrap_px(d, ln, font, right - x):
                d.text((x, y), wl, font=font, fill=fill)
                y += font.size + 6 if hasattr(font, "size") else 26
        p.parent.mkdir(parents=True, exist_ok=True)
        (im if kind != "background" else im.convert("RGB")).save(p)
        made.append((path, kind, desc, info["events"]))
    verb = "Would create" if a.dry_run else "Created"
    print(f"{verb} {len(made)} placeholder(s)")
    for path, kind, desc, events in made:
        print(f"  {path} ({kind}, used {len(events)}x)")
    if a.list and made:
        with open(a.list, "w", encoding="utf-8") as f:
            f.write("# Placeholder images\n\nAll 1170x540. background = opaque; foreground / effects = transparent "
                    "(the engine draws a foreground bottom-centered at native size).\n\n")
            for path, kind, desc, events in made:
                where = ", ".join(f"{c}:{k}" for c, k, _ in events[:10])
                first = events[0][2].get("label", "") if events else ""
                f.write(f"- **{Path(path).name}** ({kind}; events {where}) - {desc or first}\n")
        print(f"Wrote list to {a.list}")
    return 0


# =====================================================================
# Art pipeline: finalize (raw/ -> art/) and relink (story -> best image)
# =====================================================================
# Story art folder layout (stories/<name>/):
#   raw/           generator output, any size or format; raw/_rejects/ is ignored
#   raw/finalize.json  optional per-image settings (see cmd_finalize)
#   art/           game-ready files written by `finalize` (never edit by hand)
#   originals/     old art, used until a replacement exists in art/
#   placeholders/  stand-in cards, used until either exists
RAW_EXTS = (".png", ".jpg", ".jpeg", ".webp")
LINK_ORDER = ("art", "originals", "placeholders", ".")


def story_image_dir(story_path, image_dir=None):
    return Path(image_dir) if image_dir else REPO_ROOT / "stories" / Path(story_path).stem


def image_kinds(story):
    """stem -> 'background' | 'foreground' | 'effects', from how the story uses each image."""
    kinds = {}
    for path, field, *_ in image_refs(story):
        if field != "sound_effect" and not is_remote(path):
            kinds.setdefault(Path(path).stem, "background" if field == "title_screen" else field)
    return kinds


def guess_kind(stem):
    return "foreground" if stem.startswith("fg_") else "effects" if stem.startswith("fx_") else "background"


def crop_to_canvas(im, anchor=(0.5, 0.5), zoom=1.0):
    """Cover-crop to the canvas aspect ratio (anchor 0 keeps the left/top edge, 1 the right/bottom), then resize.
    zoom > 1 crops a smaller window (1/zoom of the cover-crop size) first, so the picture is magnified; the anchor
    then picks which part of the frame that window sits over."""
    w, h = im.size
    target = CANVAS_W / CANVAS_H
    if w / h > target:
        cw, ch = round(h * target), h
    else:
        cw, ch = w, round(w / target)
    if zoom > 1:
        cw, ch = round(cw / zoom), round(ch / zoom)
    x0 = round((w - cw) * min(max(anchor[0], 0), 1))
    y0 = round((h - ch) * min(max(anchor[1], 0), 1))
    lost = 1 - (cw * ch) / (w * h)
    return im.crop((x0, y0, x0 + cw, y0 + ch)).resize((CANVAS_W, CANVAS_H), Image.LANCZOS), lost


def border_color(im):
    """Median colour of the image's outer 4-px frame (the generator's backdrop)."""
    w, h = im.size
    px = im.load()
    pts = [px[x, y] for x in range(0, w, 3) for y in (0, 2, h - 3, h - 1)]
    pts += [px[x, y] for y in range(0, h, 3) for x in (0, 2, w - 3, w - 1)]
    return tuple(sorted(p[i] for p in pts)[len(pts) // 2] for i in range(3))


def detect_key(im, kind):
    r, g, b = border_color(im)
    if g - max(r, b) > 80:
        return "green", (r, g, b)
    if min(r, b) - g > 80:
        return "magenta", (r, g, b)
    if max(r, g, b) < 40 and kind == "effects":
        return "black", (r, g, b)
    return "none", (r, g, b)


def chroma_key(im, key_rgb, channel, full=0.85):
    """Unmix a green or magenta backdrop. `key_rgb` is the sampled backdrop colour (generators never give pure
    #00FF00, and JPEG adds noise), so keyness is measured relative to it; >= `full` of it counts as fully
    transparent. Edges get partial alpha, the backdrop colour is removed from them, and leftover spill is clamped."""
    kr, kg, kb = key_rgb
    if channel == "green":
        kk = kg - max(kr, kb)
        keyness = lambda r, g, b: g - max(r, b)
    else:
        kk = min(kr, kb) - kg
        keyness = lambda r, g, b: min(r, b) - g
    out = []
    for r, g, b in im.convert("RGB").getdata():
        m = min(max(keyness(r, g, b) / (kk * full), 0.0), 1.0)
        if m >= 1.0:
            out.append((0, 0, 0, 0))
            continue
        a = 1.0 - m
        fr, fg, fb = ((r - m * kr) / a, (g - m * kg) / a, (b - m * kb) / a)
        if channel == "green":
            fg = min(fg, max(fr, fb))           # despill
        else:
            spill = min(fr, fb) - fg            # magenta tint = red and blue both above green
            if spill > 0:
                fr, fb = fr - spill, fb - spill
        out.append((max(0, min(255, round(fr))), max(0, min(255, round(fg))), max(0, min(255, round(fb))),
                    round(a * 255)))
    res = Image.new("RGBA", im.size)
    res.putdata(out)
    return res


def luma_key(im, black_rgb=(0, 0, 0)):
    """Light-on-black effects art -> alpha. Brightness becomes opacity and the colour is un-premultiplied, so the
    result composited normally over anything looks like the glow was added on top. Good for glows, sparkles, rays."""
    floor = max(black_rgb)
    out = []
    for px in im.convert("RGB").getdata():
        r, g, b = (max(0, c - floor) * 255 / (255 - floor) for c in px)
        a = max(r, g, b)
        if a < 1:
            out.append((0, 0, 0, 0))
        else:
            out.append((round(r * 255 / a), round(g * 255 / a), round(b * 255 / a), round(a)))
    res = Image.new("RGBA", im.size)
    res.putdata(out)
    return res


def adjust_skin(im, region):
    """Shift skin tones inside a feathered ellipse: region = {"ellipse": [cx, cy, rx, ry] (canvas px),
    "lightness": -0.15, "saturation": 0.15, "hue": 0.0 (degrees)}. Only skin-coloured pixels are touched (warm hue,
    medium saturation), so hoodies, eye whites, hair and blue upholstery inside the ellipse are left alone."""
    import colorsys
    cx, cy, rx, ry = region["ellipse"]
    dl, ds = region.get("lightness", 0.0), region.get("saturation", 0.0)
    dh = region.get("hue", 0.0) / 360.0
    feather = region.get("feather", 0.3)
    px = im.load()
    w, h = im.size
    ramp = lambda v, lo, hi: min(1.0, max(0.0, (v - lo) / (hi - lo)))
    for y in range(max(0, int(cy - ry)), min(h, int(cy + ry) + 1)):
        for x in range(max(0, int(cx - rx)), min(w, int(cx + rx) + 1)):
            d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            if d >= 1:
                continue
            p = px[x, y]
            r, g, b = (c / 255 for c in p[:3])
            hh, s, v = colorsys.rgb_to_hsv(r, g, b)
            if not (hh < 0.13 or hh > 0.97):
                continue
            wgt = ramp(s, 0.15, 0.25) * ramp(-s, -0.85, -0.75) * ramp(v, 0.2, 0.3) * min(1.0, (1 - d) / feather)
            if wgt <= 0:
                continue
            hh, l, s = colorsys.rgb_to_hls(r, g, b)
            hh = (hh + dh * wgt) % 1.0
            l = min(1.0, l * (1 + dl * wgt))
            s = min(1.0, s * (1 + ds * wgt))
            px[x, y] = tuple(round(c * 255) for c in colorsys.hls_to_rgb(hh, l, s)) + tuple(p[3:])


def cmd_finalize(a):
    if Image is None:
        sys.exit("finalize needs Pillow (pip install pillow)")
    from PIL import ImageDraw as _ID
    story = json.loads(Path(a.story).read_text(encoding="utf-8"))
    base = story_image_dir(a.story, a.image_dir)
    raw, art = base / "raw", base / "art"
    if not raw.is_dir():
        sys.exit(f"No raw/ folder in {base}")
    art.mkdir(exist_ok=True)
    cfg_path = raw / "finalize.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    kinds = image_kinds(story)
    sources = {}
    for p in sorted(raw.iterdir()):
        if p.is_file() and p.suffix.lower() in RAW_EXTS and not p.name.startswith(("_", ".")):
            if p.stem in sources:
                print(f"  ! two raw files for {p.stem}: {sources[p.stem].name} and {p.name}; using the newer")
                if p.stat().st_mtime < sources[p.stem].stat().st_mtime:
                    continue
            sources[p.stem] = p
    manifest_path = art / ".finalize.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    only = set(a.only or [])
    done = skipped = 0
    for stem, src in sources.items():
        if only and stem not in only:
            continue
        opts = cfg.get(stem, {})
        kind = opts.get("kind") or kinds.get(stem) or guess_kind(stem)
        ext = ".jpg" if kind == "background" else ".png"
        dst = art / (stem + ext)
        st = src.stat()
        sig = {"src": src.name, "size": st.st_size, "mtime": int(st.st_mtime), "opts": opts, "kind": kind}
        if dst.exists() and manifest.get(stem) == sig and not a.force:
            skipped += 1
            continue
        with Image.open(src) as im0:
            im = im0.convert("RGB")
        notes = []
        zoom = float(opts.get("zoom", 1.0))
        win_w = im.size[0] if im.size[0] / im.size[1] <= CANVAS_W / CANVAS_H else round(im.size[1] * CANVAS_W / CANVAS_H)
        im, lost = crop_to_canvas(im, opts.get("anchor", (0.5, 0.5)), zoom)
        if zoom > 1:
            notes.append(f"zoom x{zoom:g}" + (f" (upscaled x{CANVAS_W / (win_w / zoom):.2f}, may look soft)" if win_w / zoom < CANVAS_W else ""))
        elif lost > 0.05:
            notes.append(f"crop removed {lost:.0%}" + ("" if "anchor" in opts else " (centred; set \"anchor\" to move it)"))
        if kind == "background":
            key = "none"
        else:
            detected, kc = detect_key(im, kind)
            key = opts.get("key", detected)
            if key != detected:  # forced key: fall back to the pure colour
                kc = {"green": (0, 255, 0), "magenta": (255, 0, 255), "black": (0, 0, 0)}.get(key, kc)
            if key in ("green", "magenta"):
                im = chroma_key(im, kc, key)
            elif key == "black":
                im = luma_key(im, kc)
            else:
                notes.append(f"no backdrop detected (border {kc}); saved opaque")
                im = im.convert("RGBA")
            if key != "none":
                notes.append(f"{key} keyed")
        if "opacity" in opts and im.mode == "RGBA":
            alpha = im.getchannel("A").point(lambda v: round(v * opts["opacity"]))
            im.putalpha(alpha)
            notes.append(f"opacity x{opts['opacity']}")
        for region in opts.get("skin", []):
            adjust_skin(im, region)
        if opts.get("skin"):
            notes.append(f"{len(opts['skin'])} skin adjustment(s)")
        for stale in art.glob(stem + ".*"):
            if stale != dst:
                stale.unlink()
        if kind == "background":
            im.save(dst, quality=90, subsampling=0, optimize=True)
        else:
            im.save(dst, optimize=True)
        if a.show_regions and opts.get("skin"):
            prev = im.convert("RGB")
            d = _ID.Draw(prev)
            for region in opts["skin"]:
                cx, cy, rx, ry = region["ellipse"]
                d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=(255, 0, 255), width=2)
            (art / "_preview").mkdir(exist_ok=True)
            prev.save(art / "_preview" / f"{stem}.jpg", quality=85)
        manifest[stem] = sig
        manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        done += 1
        print(f"  {src.name} -> art/{dst.name} ({kind}" + (f"; {'; '.join(notes)}" if notes else "") + ")")
    print(f"Finalized {done}, up to date {skipped}" + (f", of {len(sources)} raw image(s)" if not only else ""))
    unused = sorted(set(sources) - set(kinds))
    if unused:
        print("Not referenced by the story (finalized anyway): " + ", ".join(unused))
    return 0


def best_image(base, stem):
    """First existing file for `stem` in art/, originals/, placeholders/, then the story folder itself."""
    for folder in LINK_ORDER:
        d = base / folder
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if (d / (stem + ext)).exists():
                return folder, d / (stem + ext)
    return None, None


def cmd_relink(a):
    sp = Path(a.story)
    text = sp.read_text(encoding="utf-8")
    story = json.loads(text)
    base = story_image_dir(sp, a.image_dir)
    root = Path(a.root) if a.root else REPO_ROOT
    changes, missing, where = [], set(), {}

    def link(path):
        if not isinstance(path, str) or is_remote(path):
            return path
        stem = Path(path).stem
        folder, found = best_image(base, stem)
        if not found:
            missing.add(stem)
            return path
        where[stem] = folder
        try:
            new = "./" + found.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            new = str(found)
        if new != path:
            changes.append((path, new))
        return new

    td = story.get("title_data") or {}
    if td.get("title_screen"):
        td["title_screen"] = link(td["title_screen"])
    for ch in (story.get("chapters") or {}).values():
        for ev in (ch.get("events") or {}).values():
            if isinstance(ev, dict):
                for f in IMAGE_KEYS:
                    if ev.get(f):
                        ev[f] = link(ev[f])

    uniq = sorted(set(changes))
    for old, new in uniq:
        print(f"  {old}\n    -> {new}")
    counts = Counter(where.values())
    total = len(where) + len(missing)
    print(f"\n{total} images: {counts.get('art', 0)} final art, {counts.get('originals', 0)} old originals, "
          f"{counts.get('placeholders', 0)} placeholders, {counts.get('.', 0)} loose in the story folder, "
          f"{len(missing)} missing")
    if a.verbose:
        for folder in LINK_ORDER:
            names = sorted(s for s, f in where.items() if f == folder)
            if names:
                print(f"  {folder}: {', '.join(names)}")
    if missing:
        print("  missing: " + ", ".join(sorted(missing)))
    if a.dry_run or not uniq:
        print("Dry run; story not changed." if a.dry_run else "Story already up to date.")
        return 0
    backup = sp.with_name(sp.name + ".bak")
    backup.write_text(text, encoding="utf-8")
    one_line = text.count("\n") <= 2
    sp.write_text(json.dumps(story, ensure_ascii=False, separators=(",", ":")) if one_line else dump_story(story),
                  encoding="utf-8")
    print(f"Updated {len(changes)} reference(s) in {sp} (previous version saved as {backup.name})")
    return 0


# =====================================================================
# CLI
# =====================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="bass story tools", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="manuscript (.odt/.txt/.md) -> story .json draft")
    c.add_argument("source")
    c.add_argument("--out", help="output path (default: stories/<name>.json)")
    c.add_argument("--title", help="story name (default: derived from the output file name)")
    c.add_argument("--title-screen", help="title screen image (bare file names resolve under --image-dir)")
    c.add_argument("--image-dir", help="where bare image names in [bg: ...] directives live (default ./stories/<name>/)")
    c.add_argument("--speakers", nargs="+", metavar="NAME[=alias|alias]",
                   help="speaker names, e.g. Julia 'Doctor Harris=Dr. Harris|the optometrist' (default: auto-detect)")
    c.add_argument("--max-lines", type=int, default=3, help="max wrapped lines per dialog event (default 3)")
    c.add_argument("--max-sentences", type=int, default=2, help="max sentences packed into one event (default 2)")
    c.add_argument("--margin", type=float, default=0.95, help="fraction of the box width to use when measuring (default 0.95)")
    c.add_argument("--keep-attributions", action="store_true", help="keep 'Julia said,' narration instead of dropping it")
    c.add_argument("--force", action="store_true", help="overwrite an existing output file")
    c.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    c.set_defaults(fn=cmd_convert)

    k = sub.add_parser("check", help="validate a story .json")
    k.add_argument("story")
    k.add_argument("--root", help="project root that ./paths resolve against (default: repo root)")
    k.add_argument("--max-lines", type=int, default=3)
    k.add_argument("--margin", type=float, default=0.95)
    k.set_defaults(fn=cmd_check)

    p = sub.add_parser("placeholders", help="create stand-in images for every missing image a story references")
    p.add_argument("story")
    p.add_argument("--root", help="project root that ./paths resolve against (default: repo root)")
    p.add_argument("--descriptions", help="JSON file mapping image file name -> description to print on the card")
    p.add_argument("--list", help="also write a Markdown list of what was created")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_placeholders)

    f = sub.add_parser("finalize", help="crop/resize/key raw generator output into game-ready art/")
    f.add_argument("story", help="story .json (its art lives in stories/<name>/)")
    f.add_argument("--image-dir", help="story art folder (default stories/<story name>/)")
    f.add_argument("--only", nargs="+", metavar="NAME", help="only these images (file stems, e.g. bg_exam_room_intake fg_julia_display_case)")
    f.add_argument("--force", action="store_true", help="rebuild even if art/ is newer than raw/")
    f.add_argument("--show-regions", action="store_true", help="write art/_preview/ copies with skin ellipses drawn")
    f.set_defaults(fn=cmd_finalize)

    r = sub.add_parser("relink", help="point every image in the story at art/, else originals/, else placeholders/")
    r.add_argument("story")
    r.add_argument("--image-dir", help="story art folder (default stories/<story name>/)")
    r.add_argument("--root", help="project root that ./paths resolve against (default: repo root)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("-v", "--verbose", action="store_true", help="list which images come from where")
    r.set_defaults(fn=cmd_relink)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
