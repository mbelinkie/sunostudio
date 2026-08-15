# Suno Studio — how lyrics are parsed and turned into subtitles

Version 4.11. This describes the current lyrics path. `suno_studio.py` is the
authoritative implementation; longer code excerpts below are retained to make
the design and historical failure modes reviewable.

---

## The pipeline in one view

```
  Gmail (IMAP, read-only)
        │
        ▼
  body_text()            pick text/plain, else de-tag text/html
        │
        ▼
  undo_quoted_printable()  repair "=3D" if the markers are broken
        │
        ▼
  split_sections()       ===TITLE=== / ===STYLE=== / ===LYRICS=== …
        │
        ▼
  parse_request()        → {title, style, tagline, recipient, lyrics, …}
        │
        ├── lyrics text ─────────────────────────────┐
        ▼                                            │
  Suno generates audio                               │
        │                                            │
        ▼                                            │
  get-timestamped-lyrics → alignedWords[]            │
        │                                            │
        ▼                                            ▼
  _clean_items()                          the authored line breaks
        │                                            │
        └──────────────► align_lyrics() ◄────────────┘
                                  │
                     section DP + local character alignment
                                │
                                ▼
                        build_karaoke_ass()  →  .ass  →  ffmpeg
```

**The core idea:** Suno's `alignedWords` gives accurate *timing* but unreliable
*line breaks*. The submitted lyrics give perfect line breaks but no timing.
So the two are aligned against each other, and the email's line breaks win.

---

## Stage 1 — Email body to fields

### Section markers

`===TITLE===`, `## TITLE`, `**TITLE**` and `--- TITLE ---` are all accepted.
The value is on the **following** lines, not the same line.

```python
SECTION_RE = re.compile(
    r"^\s*(?:={2,}\s*([A-Za-z ]{3,20}?)\s*={2,}"
    r"|-{2,}\s*([A-Za-z ]{3,20}?)\s*-{2,}"
    r"|#{1,4}\s*([A-Za-z ]{3,20}?)\s*#*"
    r"|\*{2}\s*([A-Za-z ]{3,20}?)\s*:?\s*\*{2})\s*:?\s*$")
```

An unrecognised block (like `===END===`) *terminates* the current section
rather than being absorbed into it.

### Field names and aliases

```python
FIELD_ALIASES = {
    "style": "style", "genre": "style", "tags": "style",
    "title": "title", "song": "title", "name": "title",
    "model": "model", "version": "model",
    "instrumental": "instrumental",
    "vocal": "vocalGender", "vocals": "vocalGender", "voice": "vocalGender",
    "exclude": "negativeTags", "avoid": "negativeTags", "negative": "negativeTags",
    "lyrics": "lyrics", "words": "lyrics", "lyric": "lyrics",
    "sprint": "tagline", "tagline": "tagline", "subtitle": "tagline",
    "email": "recipient", "notify": "recipient", "requester": "recipient",
    "to": "recipient", "reply": "recipient", "replyto": "recipient",
    "team": "tagline", "project": "tagline",
}
```

### Splitting the body into sections

```python
def split_sections(text):
    """{field: value} if the body uses section blocks, else None."""
    hits, cur, buf = {}, None, []
    found_any = False
    for line in text.split("\n"):
        m = SECTION_RE.match(line)
        name = next((g for g in (m.groups() if m else ()) if g), None) if m else None
        key = FIELD_ALIASES.get((name or "").strip().lower()) if name else None
        if name and key:
            if cur:
                hits[cur] = "\n".join(buf).strip()
            cur, buf, found_any = key, [], True
            continue
        if name and not key and cur:
            # An unrecognised block ends the current one rather than absorbing it.
            hits[cur] = "\n".join(buf).strip()
            cur, buf = None, []
            continue
        if cur:
            buf.append(line)
    if cur:
        hits[cur] = "\n".join(buf).strip()
    if not found_any:
        return None
    # No explicit lyrics block? Then the lyrics were probably left dangling at
    # the end of another section. Split it at the first [Verse]-style tag.
    if not hits.get("lyrics"):
        for k, v in list(hits.items()):
            if k == "lyrics":
                continue
            rows = v.split("\n")
            at = next((i for i, r in enumerate(rows) if re.match(r"^\s*\[[^\]]+\]\s*$", r)), None)
            if at is not None:
                hits[k] = "\n".join(rows[:at]).strip()
                hits["lyrics"] = "\n".join(rows[at:]).strip()
                break
    return hits
```

### Quoted-printable repair

Some senders deliver quoted-printable without a matching
`Content-Transfer-Encoding`, so `=` arrives as `=3D` and every `===MARKER===`
is destroyed. This only intervenes when the markers are already broken —
running it on a healthy body is destructive, because `quopri` eats `===` as
escape sequences.

```python
def undo_quoted_printable(text):
    """Some senders deliver quoted-printable without a matching
    Content-Transfer-Encoding header, so '=' arrives as '=3D' and every
    ===SECTION=== marker is destroyed."""
    # Only step in when the markers are actually broken. Running quopri over a
    # normal body is destructive: it eats "===" as escape sequences.
    if "=3D" not in text or "===" in text:
        return text
    try:
        import quopri
        fixed = quopri.decodestring(text.encode("utf-8", "replace")).decode("utf-8", "replace")
        # "=3D=3D=3D" and "===" contain the same number of "=" characters,
        # so compare on markers actually appearing, not on counts.
        if "===" in fixed or fixed.count("=3D") < text.count("=3D"):
            return fixed
        return text
    except Exception:
        return text
```

### The whole request parser

Falls back to an inline `Key: value` format when no section blocks are found.

```python
def parse_request(subject, body, default_style=""):
    """
    Turn an email into a generation form. Two layouts are understood:

      Style: dreamy synthpop        |   ===STYLE===
      ---                           |   dreamy synthpop
      [Verse]                       |   ===LYRICS===
      ...                           |   [Verse] ...

    Headers are optional and order-free.
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = undo_quoted_printable(text)
    # Drop quoted replies and common signature delimiters.
    text = re.split(r"\n-- \n|\n_{5,}\n|\nOn .{0,80} wrote:\n", text)[0]

    # Section-block layout wins if present - it's unambiguous.
    sect = split_sections(text)
    if sect:
        instrumental = (sect.get("instrumental", "").strip().lower() in TRUEISH)
        gender = sect.get("vocalGender", "").strip().lower()
        title = (sect.get("title") or "").strip() or clean_subject(subject)
        if not title:
            title = (sect.get("tagline") or "").strip()
        return {
            "title": title or "Untitled",
            "style": scrub_scaffolding(sect.get("style", "")).replace("\n", ", ").strip(", ")
                     or default_style,
            "lyrics": "" if instrumental else scrub_scaffolding(sect.get("lyrics", "")),
            "model": (sect.get("model") or "").strip(),
            "instrumental": instrumental,
            "negativeTags": (sect.get("negativeTags") or "").strip(),
            "tagline": (sect.get("tagline") or "").strip(),
            "recipient": first_email(sect.get("recipient") or ""),
            "vocalGender": "f" if gender.startswith("f") else "m" if gender.startswith("m") else "",
            "styleWeight": None,
            "weirdnessConstraint": None,
        }

    fields, lyric_lines, in_lyrics = {}, [], False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not in_lyrics:
            if re.match(r"^\s*-{3,}\s*$", line):
                in_lyrics = True
                continue
            m = re.match(r"^\s*([A-Za-z]{3,14})\s*:\s*(.*)$", line)
            # A structure tag like "[Verse]" means the lyrics have begun.
            if m and not line.lstrip().startswith("["):
                key = FIELD_ALIASES.get(m.group(1).strip().lower())
                if key:
                    fields[key] = m.group(2).strip()
                    continue
            if not line.strip():
                continue
            in_lyrics = True
        lyric_lines.append(line)

    lyrics = scrub_scaffolding("\n".join(lyric_lines))

    title = fields.get("title") or clean_subject(subject)
    instrumental = (fields.get("instrumental", "").strip().lower() in TRUEISH)
    gender = fields.get("vocalGender", "").strip().lower()
    gender = "f" if gender.startswith("f") else "m" if gender.startswith("m") else ""

    return {
        "title": title or "Untitled",
        "style": fields.get("style") or default_style,
        "lyrics": "" if instrumental else lyrics,
        "model": fields.get("model", "").strip(),
        "instrumental": instrumental,
        "negativeTags": fields.get("negativeTags", ""),
        "tagline": fields.get("tagline", "").strip(),
        "recipient": first_email(fields.get("recipient", "")),
        "vocalGender": gender,
        "styleWeight": None,
        "weirdnessConstraint": None,
    }
```

### Supporting bits

```python
def scrub_scaffolding(s):
    """Drop stray ===HEADER=== lines so they never get sung."""
    return "\n".join(l for l in (s or "").split("\n") if not SECTION_RE.match(l)).strip()

def clean_subject(subject):
    """Strip routing tags like [SPRINT SONG] / [SUNO] from an email subject,
    and collapse 'Name - Name' duplication."""
    s = re.sub(r"^\s*(?:\[[^\]]{1,30}\]\s*)+", "", subject or "").strip()
    s = re.sub(r"^\s*(?:re|fwd)\s*:\s*", "", s, flags=re.I).strip()
    # "Learning Path - Learning Path 26.3.2": collapse when one half repeats
    # or merely prefixes the other, not only on an exact match.
    m = re.match(r"^(.{3,}?)\s*[-–—]\s*(.+)$", s)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        na, nb = _norm(a), _norm(b)
        if na and nb and (na == nb or nb.startswith(na) or na.startswith(nb)):
            s = b if len(nb) >= len(na) else a
    return s.strip(" -–—")

def compose_basename(sprint, title):
    """
    "<sprint> - <title>", but only when that actually adds information.
    A subject line like "Learning Path - Learning Path 26.3.2" plus a sprint of
    "Learning Path 26.3.2" used to produce a filename that said it three times.
    """
    sprint = (sprint or "").strip(" -")
    title = (title or "").strip(" -")
    if not sprint:
        return title or "Untitled"
    if not title or title.lower() in ("untitled", "song"):
        return sprint
    a, b = _norm(sprint), _norm(title)
    if a == b or a in b or b in a:
        return title if len(b) >= len(a) else sprint
    return f"{sprint} - {title}"

def first_email(text):
    """Pull one address out of a field that might be 'Matt <m@x.com>' or a list."""
    m = EMAIL_RE.search(text or "")
    return m.group(0).lower() if m else ""

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

TRUEISH = {"yes", "y", "true", "1", "on", "instrumental"}
```

---

## Stage 2 — Timed words to subtitle lines

### Section-aware aligner (default)

`align_lyrics(..., method="section")` is the default. It keeps the renderer's
historical `[[timed_word, ...]]` group shape but also returns structured
diagnostics.

1. `parse_authored_lyrics()` retains ordered `[Verse]`, `[Chorus]`, `[Bridge]`,
   `[Outro]`, and related section tags, plus every authored non-empty line.
2. `segment_audio_chunks()` retains tags and embedded newlines from Suno and
   uses timestamp pauses as additional candidate boundaries. Long gaps mark a
   section boundary; they do not create fake lyric events.
3. `_match_sections()` uses dynamic programming to partition audio units among
   authored sections in strict chronological order. Repeated choruses are
   distinct ordered sections. Skipping one weak section is legal, so a bad
   early region cannot move all later sections.
4. `_local_section_alignment()` runs character matching only inside the audio
   range assigned to one section. This handles punctuation, split contractions,
   merged words, omissions, and insertions without a whole-song character
   alignment. Unmatched ad-libs remain attached to their local audio unit.
5. A section below 34% confidence uses Suno's local audio structure. A line
   below 42% is marked as a local audio fallback. Strong surrounding sections
   continue using authored line breaks.
6. Leading entries that do not reliably match the first authored words (for
   example, a long un-authored "oooooh") stay in diagnostics but do not start
   the first subtitle event. An authored vocalization still displays normally.

The structured result includes `groups`, `overall_confidence`, ordered section
and line diagnostics, `matched_text`, `skipped_lyric_text`,
`unmatched_audio_words`, `confidence`, `method`, timestamps, and warnings.
`lines_from_lyrics()` remains the compatibility wrapper used by ASS and
drawtext rendering. Set `lyric_aligner` to `legacy`, pass `method="legacy"`, or
run `subs_doctor.py --legacy` to compare against the preserved baseline.

Authored parenthetical spans—both whole response lines and inline phrases—
receive warm gold/copper ASS karaoke colours for call-and-response vocals.
This changes only colour; font, position, event timing, and fades remain
unchanged.

### Legacy whole-song aligner (selectable baseline)

The implementation below is retained as `legacy_lines_from_lyrics()`. It is no
longer the default because a single global character alignment can let an early
error affect every later line.

### Cleaning Suno's words

`alignedWords` entries can carry `[Verse]` tags and embedded newlines.

```python
def _clean_items(aligned):
    items = []
    for w in aligned:
        raw = w.get("word") or ""
        clean = re.sub(r"\[[^\]]+\]", " ", raw)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue
        try:
            it = {"w": clean, "s": float(w["startS"]), "e": float(w["endS"])}
        except (KeyError, TypeError, ValueError):
            continue
        if it["e"] < it["s"]:
            it["e"] = it["s"] + 0.2
        items.append(it)
    return items

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())
```

### The alignment — this is the heart of it

Word-level matching failed because Suno splits contractions: the lyrics have
`we're` as one token, the audio has `We'` + `re`. Those never match, and the
line loses its opening words. Aligning on **characters** handles splits,
merges, dropped words and ad-libs in one mechanism.

Each timed word votes for a lyric line based on which line its characters
matched; the majority wins. Words that matched nothing (ad-libs, punctuation)
inherit the line in progress.

```python
def legacy_lines_from_lyrics(aligned, lyrics_text, max_chars=46):
    """
    Split the timed words at the line breaks the EMAIL used.

    Uses difflib to align the sung words against the submitted lyrics. That
    handles ad-libs, dropped words and contraction splits for free - the
    hand-rolled prefix matcher this replaces desynced on the first surprise
    and dragged every later line with it.
    """
    import difflib

    items = _clean_items(aligned)
    if not items or not (lyrics_text or "").strip():
        return None

    # flatten the authored lyrics into (line number, normalised token)
    authored = []
    for li, raw_line in enumerate(lyrics_text.split("\n")):
        line = re.sub(r"\[[^\]]+\]", " ", raw_line)
        for t in re.split(r"\s+", line):
            n = _norm(t)
            if n:
                authored.append((li, n))
    if not authored:
        return None

    # Align on CHARACTERS, not words. Suno splits contractions ("we'" + "re")
    # where the lyrics have one token, so token-level matching drops them.
    sung_chars, sung_owner = [], []
    for i, it in enumerate(items):
        for ch in _norm(it["w"]):
            sung_chars.append(ch)
            sung_owner.append(i)
    auth_chars, auth_line = [], []
    for li, tok in authored:
        for ch in tok:
            auth_chars.append(ch)
            auth_line.append(li)
    if not sung_chars or not auth_chars:
        return None

    sm = difflib.SequenceMatcher(None, sung_chars, auth_chars, autojunk=False)
    votes = [{} for _ in items]
    matched = 0
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            owner = sung_owner[a + k]
            line = auth_line[b + k]
            votes[owner][line] = votes[owner].get(line, 0) + 1
        matched += size

    if matched < 0.4 * len(sung_chars):
        return None            # the audio isn't singing these lyrics

    line_of = [max(v, key=v.get) if v else None for v in votes]
    first = next((v for v in line_of if v is not None), 0)
    last = first
    for i in range(len(items)):
        if line_of[i] is None:
            line_of[i] = last
        else:
            last = line_of[i]

    groups, cur, cur_line = [], [], None
    for i, it in enumerate(items):
        if cur and line_of[i] != cur_line:
            groups.append([unorphan(cur)])
            cur = []
        cur_line = line_of[i]
        cur.append(it)
    if cur:
        groups.append([unorphan(cur)])
    return groups or None
```

### Legacy whole-song fallback

In legacy mode, if under 40% of characters align, this heuristic runs for the
whole song. The section-aware default instead invokes it only for unmatched or
low-confidence local regions.

```python
def group_lyric_lines(aligned, max_chars=46, gap=1.1):
    """alignedWords -> list of lines.

    Suno marks its own line breaks with a newline *before* the word. The
    break must be detected on the raw string: .strip() eats a leading \\n,
    which silently merged every line into a rolling window."""
    lines, cur = [], []
    for w in aligned:
        raw = w.get("word") or ""
        # NB: check raw, not raw.strip()
        forced = ("\n" in raw) or bool(re.search(r"\[[^\]]+\]", raw))
        clean = re.sub(r"\[[^\]]+\]", " ", raw)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue
        try:
            item = {"w": clean, "s": float(w["startS"]), "e": float(w["endS"])}
        except (KeyError, TypeError, ValueError):
            continue
        if item["e"] < item["s"]:
            item["e"] = item["s"] + 0.2
        if cur:
            # Suno's own breaks lead; length is only a safety net for
            # unusually long lines, and silence catches missing markers.
            too_long = len(join_words([x["w"] for x in cur] + [clean])) > max_chars
            silence = item["s"] - cur[-1]["e"] > gap
            if forced or too_long or silence:
                lines.append(cur)
                cur = []
        cur.append(item)
    if cur:
        lines.append(cur)
    return [[unorphan(ln)] for ln in lines if ln]

def unorphan(chunk):
    """Glue a lone opening bracket onto the word after it, so the karaoke fill
    treats "(Ooh" as one unit rather than two."""
    out = []
    for it in chunk or []:
        if out and out[-1]["w"] in OPENERS:
            prev = out.pop()
            it = {"w": prev["w"] + it["w"], "s": prev["s"], "e": it["e"]}
        out.append(it)
    return out
```

---

## Stage 3 — Rendering to ASS

### Joining words back into a line

Handles contraction splits, brackets, and the straight-quote-is-both-an-
opener-and-a-closer problem (decided by parity).

```python
OPENERS = ("(", "[", "{", "\u201c", '"', "\u2018")

CLOSERS = (")", "]", "}", "\u201d", "\u2019")

def needs_space(prev, cur):
    """Suno splits contractions across entries ("We'" + "re"). Joining those
    with a space produces "We' re"."""
    if not prev:
        return False
    # A straight quote is both an opener and a closer, so decide by parity:
    # an odd count means the last one opened a quote (no space after it),
    # an even count means it closed one (space needed).
    if prev.endswith('"'):
        return prev.count('"') % 2 == 0
    # ...and a quote that closes an open one hugs the word before it
    if cur.startswith('"') and prev.count('"') % 2 == 1:
        return False
    if prev.endswith(("'", "\u2019", "-", "\u2011", "\u2013", "(", "[", "{",
                      "\u201c", "\u2018")):
        return False
    if cur.startswith((",", ".", "!", "?", ":", ";") + CLOSERS):
        return False
    return True

def join_words(words):
    out = ""
    for w in words:
        out += (" " if needs_space(out, w) else "") + w
    return out
```

### Type sizing

There is no wrapping. One email line = one subtitle line, and the type shrinks
to fit the longest line in the song.

```python
def fit_fontsize(groups, width=1920, margin=56, lo=28, hi=64):
    """
    Pick the largest size at which the LONGEST lyric line still fits on one
    row. Wrapping is gone, so the type adapts to the song instead.
    0.52em is a decent average advance for a bold sans.
    """
    longest = 0
    for g in groups:
        for row in g:
            longest = max(longest, len(join_words([x["w"] for x in row])))
    if not longest:
        return hi
    return max(lo, min(hi, int((width - 2 * margin) / longest / 0.52)))
```

### Building the subtitle file

Notable constraints encoded here:

- Events must **never overlap**. Two simultaneous events make libass stack them
  vertically, which looks exactly like duplicated lyrics.
- A timed lyric block shorter than 1.0s is retained in diagnostics but omitted
  from the rendered ASS; longer blocks receive at least 1.0s on screen when
  neighboring timestamps leave enough room.
- Alignment 8 (top-centre) with a fixed margin, not centre — centring re-flows
  the block so any height change moves the line.

The following is the legacy event loop retained for comparison. The 4.11 rules
under **Real-song timing corrections** supersede its pause and minimum-duration
behavior.

```python
ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Now,{font},{size},{hi},{lo},&H00000000,&HC8000000,-1,0,0,0,100,100,0.4,0,1,4,2.5,8,{margin},{margin},{vmargin},1
Style: Banner,{font},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,1,4,3,8,80,80,70,1
Style: BannerSub,{font},36,&H99FFFFFF,&H99FFFFFF,&H00000000,&HB4000000,0,0,0,0,100,100,0,0,1,3,2,8,80,80,165,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

def build_karaoke_ass(aligned, font="Helvetica", hi="&H00A6D322", lo="&H00C8C8C8",
                      lead=0.18, tail=0.45, banner=None, lyrics_text=""):
    """ASS with \\kf karaoke fills. Colours are &HAABBGGRR - BGR, not RGB.

    banner=(title, subtitle) draws the title through libass instead of
    drawtext, for ffmpeg builds without freetype."""
    groups = lines_from_lyrics(aligned, lyrics_text) or group_lyric_lines(aligned)
    margin = 56
    size = fit_fontsize(groups, margin=margin)
    # Alignment 8 (top-centre) + a margin puts the baseline at a known
    # height. Dead centre sat above the calm band the artwork leaves.
    vmargin = int(1080 * float(CONFIG.get("lyric_y", 0.680)))
    out = [ASS_HEAD.format(font=font, hi=hi, lo=lo, size=size,
                           margin=margin, vmargin=vmargin)]
    if banner:
        span = (groups[-1][-1][-1]["e"] + 10) if groups else 600
        btitle, bsub = banner
        out.append(f"Dialogue: 0,{ass_time(0)},{ass_time(span)},Banner,,0,0,0,,"
                   f"{ass_escape(btitle)}")
        if (bsub or "").strip():
            out.append(f"Dialogue: 0,{ass_time(0)},{ass_time(span)},BannerSub,,0,0,0,,"
                       f"{ass_escape(bsub)}")

    MIN_ON_SCREEN = 0.85
    for i, rows in enumerate(groups):
        flat = [it for r in rows for it in r]
        start = max(0.0, flat[0]["s"] - lead)
        end = flat[-1]["e"] + tail
        nxt = groups[i + 1][0][0]["s"] if i + 1 < len(groups) else None
        prev_end = (groups[i - 1][-1][-1]["e"] + tail) if i else 0.0
        if nxt is not None:
            # must be gone before the NEXT event fades in, or libass stacks them
            end = min(end, nxt - lead - 0.06)
        if end - start < MIN_ON_SCREEN:
            # buy time from the silence before it rather than from the next line
            start = max(prev_end + 0.06, 0.0, end - MIN_ON_SCREEN)
        if end <= start:
            end = start + 0.35

        parts, prev, plain = [], start, ""
        for ri, row in enumerate(rows):
            if ri:
                parts.append("\\N")
            first_in_row = True
            for it in row:
                hold = max(0, int(round((it["s"] - prev) * 100)))
                if hold:
                    parts.append("{\\kf%d}" % hold)
                if not first_in_row and needs_space(plain, it["w"]):
                    parts.append(" ")
                    plain += " "
                first_in_row = False
                parts.append("{\\kf%d}%s" % (max(1, int(round((it["e"] - it["s"]) * 100))),
                                             ass_escape(it["w"])))
                plain += it["w"]
                prev = it["e"]
        out.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Now,,0,0,0,,"
                   f"{{\\fad(140,140)}}{''.join(parts)}")
    return "\n".join(out) + "\n", len(groups)

def ass_time(t):
    t = max(0.0, float(t))
    return f"{int(t//3600):d}:{int(t%3600//60):02d}:{t%60:05.2f}"

def ass_escape(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")
```

---

## Real-song timing corrections (4.11)

The Med Launch Magic fixture exposed three renderer/timestamp problems that
synthetic alignment tests did not:

1. A `\kf` delay tag with no following glyph is replaced by the next karaoke
   tag. Inter-word pauses therefore vanished and the sweep ran increasingly
   early. Timing-only tags now own either the real inter-word space or a
   zero-width glyph, preserving every absolute Suno word start.
2. Suno sometimes includes section-marker or instrumental time in a boundary
   word (`[Verse 2]\nVidyashree` was 4.73 seconds; `last\n\n(` was 16.39
   seconds). Only obvious structure-boundary outliers are locally trimmed;
   ordinary held notes are retained. Diagnostics report every adjustment.
3. The minimum-duration rule could move a short response *after* its own raw
   timing and overlap the next event. It may now borrow only available earlier
   time and never moves a response into the following line.

The exact cached input is `alignment samples/med_launch_magic.words.json`,
with its pre-fix and corrected ASS files beside it.

## Historical problems (resolved)

**1. The `===TITLE===` block was not read from some real emails.**
The same text pasted by hand parses correctly, both as plain text and as HTML,
so the delivered body differs from what it looks like in the mail client.
Consequence chain: title falls back to the subject
`[SPRINT SONG] Learning Path — Learning Path 26.3.2` → `clean_subject`
collapses the repeat to `Learning Path 26.3.2` → `compose_basename` sees title
== sprint and emits one copy → files are named `Learning Path 26.3.2.mp4`.

*To diagnose:* `~/Library/Logs/SunoStudio.log` contains, for every parsed
request:

```
[watch] parsed: title=... style=... sprint=... to=... lyric_chars=N
[watch] body began: '...first 200 characters...'
```

The delivered marker had a leading U+200C zero-width character. Version 4.9
normalizes common Unicode format controls before matching section headers.

**2. `unique_path` mangled names containing dots.** It used `Path.suffix`,
which reads `.2` in `Learning Path 26.3.2` as a file extension, producing
`Learning Path 26.3 (2).2`. It should split on the *known* extension instead.

```python
# current, wrong for dotted names:
stem, suffix = p.stem, p.suffix          # "Learning Path 26.3", ".2"
cand = p.with_name(f"{stem} ({n}){suffix}")
```

The atomic output allocators now append counters without interpreting dotted
song names as extensions.

**3. Timing accuracy was previously tested only with synthetic inputs.** The
suite now includes the exact Med Launch Magic cached timing response and checks
word onsets, boundary silence, event overlap, and parenthetical styling.

---

## Testing without rendering video

`subs_doctor.py` rebuilds the `.ass` from a song's cached `.words.json` and
renders one frame per lyric line — seconds rather than minutes. It reports
overlaps, sub-1.0s lines, big gaps, and whether the line breaks came from the
lyrics or from the fallback heuristic.

```
python3 subs_doctor.py "/path/to/song.mp3"
python3 subs_doctor.py song.mp3 --no-frames     # timeline only, instant
```

## Local stable-ts hybrid (4.14)

`lyric_aligner = "stable-ts-hybrid"` replaces only the timing source. The ASS
renderer and its appearance remain unchanged. The implementation runs in the
isolated `~/.suno_studio/stable-ts-venv` Python 3.11 environment so PyTorch and
Whisper do not become dependencies of the app's web server process.

1. `stable_ts_hybrid.py` force-aligns the complete authored lyric sheet with
   stable-ts `base.en`, retaining original line splits.
2. A line is weak when its mean acoustic probability is below 0.25, it contains
   an internal gap above two seconds, or it has no timed words.
3. The nearest strong lines establish hard chronological bounds.
4. Only the weak window is locally transcribed and force-realigned. The helper
   also compares any original Suno words inside those bounds.
5. A repair must satisfy similarity/acoustic evidence, maximum-gap, maximum-word-
   duration, and boundary checks. Otherwise the line is hidden; later lines are
   never shifted.
6. Results and diagnostics are cached beside the song as
   `SONG.stable-ts-hybrid.json`, keyed by the audio, lyrics, Suno timing stream,
   helper version, and model.

The existing `section` aligner remains the default/selectable baseline and
`legacy` remains available. Reinstall the local runtime with
`Install Local Lyric Alignment.command`. Diagnose a particular song with:

```
python3 subs_doctor.py song.mp3 --stable-ts --no-frames
```

### Best-quality bounded repair

When `hybrid_repair = "cloud"`, each weak window is additionally transcribed
with hosted `whisper-1`. The API key is passed to the isolated helper only in
the child-process environment; it is absent from command arguments, request
JSON, caches, and diagnostics. Only the bounded weak-line audio clip leaves the
machine. An independently heard cloud candidate receives a small preference
over forced authored text, but it must still pass lexical similarity, maximum
internal-gap, maximum-word-duration, and anchor-boundary checks.

Candidate priority is evidence-based: cloud Whisper, original Suno timing,
local open transcription, then bounded local forced alignment. If the network
or API is unavailable, the same run continues through those local fallbacks.
A failed cloud call marks the cache retryable so a temporary outage is not
silently made permanent. Settings exposes both hosted best-quality and fully
local repair modes.
