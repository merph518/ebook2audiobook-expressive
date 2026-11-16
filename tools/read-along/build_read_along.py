"""Prototype: turn a source EPUB plus the app's audiobook and sidecar WebVTT into an EPUB 3
with Media Overlays (read-along), so a reading system highlights the printed text as the
audio plays.

The app keeps no link from a spoken sentence back to the XHTML element it came from, so each
WebVTT cue is found in the book by aligning its words with the book's words. Each matched
range is wrapped in a <span id="raN">, and one SMIL file per XHTML document points each span
at its clip of the audio. The whole audiobook goes in as one audio file; the clips index into
it, so the interlude offsets the WebVTT already carries line up with no extra work.

Usage: build_read_along.py SOURCE.epub AUDIOBOOK.m4b AUDIOBOOK.vtt OUT.epub
"""
import difflib
import posixpath
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.dom import minidom

XHTML_NS = 'http://www.w3.org/1999/xhtml'
OPF_NS = 'http://www.idpf.org/2007/opf'
WORD = re.compile(r"[^\W_]+")
# Punctuation that belongs to a sentence but sits outside its first or last word.
OPENERS = '"\'\u201c\u2018(['
CLOSERS = '.,;:!?"\'\u201d\u2019)]*-\u2014\u2013\u2026'
BLOCKS = {'p', 'div', 'section', 'article', 'body', 'blockquote', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'tr', 'td', 'th', 'figure', 'header', 'footer', 'aside', 'nav'}
ACTIVE_CLASS = '-epub-media-overlay-active'
AUDIO_HREF = 'audio/audiobook.m4a'


def parse_ts(ts):
    h, m, s = ts.split(':') if ts.count(':') == 2 else ('0', *ts.split(':'))
    return int(h) * 3600 + int(m) * 60 + float(s)


def clock(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f'{int(h)}:{int(m):02d}:{s:06.3f}'


def read_vtt(path):
    cues = []
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    i = 0
    while i < len(lines):
        if '-->' in lines[i]:
            start, end = (p.strip().split(' ')[0] for p in lines[i].split('-->'))
            text = []
            i += 1
            while i < len(lines) and lines[i].strip():
                text.append(lines[i].strip())
                i += 1
            cues.append({'start': parse_ts(start), 'end': parse_ts(end), 'text': ' '.join(text)})
        i += 1
    return cues


def text_nodes(node, out):
    for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
            out.append(child)
        elif child.nodeType == child.ELEMENT_NODE:
            text_nodes(child, out)
    return out


def book_text(body):
    nodes = text_nodes(body, [])
    spans, pos = [], 0
    for n in nodes:
        spans.append((pos, pos + len(n.data), n))
        pos += len(n.data)
    return ''.join(n.data for n in nodes), spans


def node_at(spans, offset, prefer_end):
    for start, end, n in spans:
        if start < offset < end or (offset == start and not prefer_end and end > start) or (offset == end and prefer_end and end > start):
            return n, offset - start
    return None, None


def cut_text(body, offset):
    """Split the text node at offset so a node boundary falls there; return the node that starts at offset."""
    _, spans = book_text(body)
    for start, end, n in spans:
        if start == offset and end > start:
            return n
        if start < offset < end:
            return n.splitText(offset - start)
    return None


def local(el):
    return (el.localName or el.tagName).lower()


def split_before(top, node):
    """Split the ancestors of node up to top so node starts its own copy of top; return that copy."""
    cur = node
    while cur is not top:
        parent = cur.parentNode
        if cur.previousSibling is None:
            if parent is top.parentNode:
                return cur
            cur = parent
            continue
        clone = parent.cloneNode(False)
        if clone.hasAttribute('id'):
            clone.removeAttribute('id')
        moving = []
        sib = cur
        while sib is not None:
            moving.append(sib)
            sib = sib.nextSibling
        for m in moving:
            clone.appendChild(m)
        parent.parentNode.insertBefore(clone, parent.nextSibling)
        if parent is top:
            return clone
        cur = clone
    return cur


def split_after(top, node):
    """Split the ancestors of node up to top so node ends its copy of top; return that copy."""
    cur = node
    while cur is not top:
        parent = cur.parentNode
        if cur.nextSibling is not None:
            clone = parent.cloneNode(False)
            if clone.hasAttribute('id'):
                clone.removeAttribute('id')
            sib = cur.nextSibling
            while sib is not None:
                nxt = sib.nextSibling
                clone.appendChild(sib)
                sib = nxt
            parent.parentNode.insertBefore(clone, parent.nextSibling)
        if parent is top.parentNode:
            return cur
        cur = parent
    return cur


def ancestors(node):
    out = []
    while node is not None:
        out.append(node)
        node = node.parentNode
    return out


def wrap(doc, body, a, b, span_id):
    """Wrap book text [a, b) in <span id=span_id>. Return False when the range crosses a block."""
    last = cut_text(body, b)  # node starting at b, or None at the end of the text
    first = cut_text(body, a)
    _, spans = book_text(body)
    inside = [n for s, e, n in spans if s >= a and e <= b and e > s]
    if not inside:
        return False
    first, last = inside[0], inside[-1]
    up_last = ancestors(last)
    lca = next(n for n in ancestors(first) if n in up_last)
    if lca.nodeType != lca.ELEMENT_NODE:
        lca = first.parentNode
    for n in inside:
        for p in ancestors(n)[1:]:
            if p is lca:
                break
            if local(p) in BLOCKS:
                return False
    def child_of(top, n):
        while n.parentNode is not top:
            n = n.parentNode
        return n
    s = child_of(lca, first)
    if s is not first:
        s = split_before(s, first)
    e = child_of(lca, last)
    if e is not last:
        e = split_after(e, last)
    span = doc.createElementNS(XHTML_NS, 'span')
    span.setAttribute('id', span_id)
    lca.insertBefore(span, s)
    cur = s
    while True:
        nxt = cur.nextSibling
        span.appendChild(cur)
        if cur is e:
            break
        cur = nxt
    return True


def tokens(text):
    return [(m.group().lower(), m.start(), m.end()) for m in WORD.finditer(text)]


def align(cues, docs):
    """Give each cue a (doc index, start, end) range in that document's book text, by word alignment."""
    book, owner = [], []
    for di, d in enumerate(docs):
        for t in tokens(d['text']):
            book.append(t)
            owner.append(di)
    cue_toks, cue_of = [], []
    for ci, c in enumerate(cues):
        for t in tokens(c['text']):
            cue_toks.append(t[0])
            cue_of.append(ci)
    sm = difflib.SequenceMatcher(None, cue_toks, [t[0] for t in book], autojunk=False)
    hits = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            ci = cue_of[blk.a + k]
            hits.setdefault(ci, []).append(blk.b + k)
    ranges = {}
    last = -1
    for ci in sorted(hits):
        idx = hits[ci]
        # A cue lives in one document; keep the document most of its words matched in.
        di = max(set(owner[i] for i in idx), key=lambda d: sum(owner[i] == d for i in idx))
        idx = [i for i in idx if owner[i] == di]
        # A short common phrase ("the Star of Rest") can match far from the rest of its cue;
        # keep the first substantial run of hits that sit close together after the previous cue.
        gap = max(5, len(idx))
        runs, run = [], [idx[0]]
        for i in idx[1:]:
            if i - run[-1] > gap:
                runs.append(run)
                run = []
            run.append(i)
        runs.append(run)
        after = [r for r in runs if r[0] > last] or runs
        longest = max(len(r) for r in after)
        idx = next(r for r in after if len(r) >= min(longest, max(2, longest // 2)))
        last = idx[-1]
        ranges[ci] = [di, book[idx[0]][1], book[idx[-1]][2], len(idx)]
    return ranges, len(cue_toks), sum(blk.size for blk in sm.get_matching_blocks())


def widen(text, a, b):
    while a > 0 and text[a - 1] in OPENERS:
        a -= 1
    while b < len(text) and text[b] in CLOSERS:
        b += 1
    return a, b


def block_of(node):
    node = node.parentNode
    while node is not None and node.nodeType == node.ELEMENT_NODE and local(node) not in BLOCKS:
        node = node.parentNode
    return node


def block_pieces(body, text, a, b):
    """Cut [a, b) at block element edges: a cue the app merged across paragraphs (or a heading
    joined to the first paragraph) becomes one piece per block, since a span cannot cross one."""
    _, spans = book_text(body)
    pieces = []
    for s, e, n in spans:
        if e <= a or s >= b or e == s:
            continue
        blk = block_of(n)
        lo, hi = max(a, s), min(b, e)
        if pieces and pieces[-1][0] is blk:
            pieces[-1][2] = hi
        else:
            pieces.append([blk, lo, hi])
    out = []
    for _, lo, hi in pieces:
        while lo < hi and text[lo].isspace():
            lo += 1
        while hi > lo and text[hi - 1].isspace():
            hi -= 1
        weight = sum(ch.isalnum() for ch in text[lo:hi])
        if weight:
            out.append((lo, hi, weight))
    return out


def main(src_epub, m4b, vtt, out_epub):
    cues = read_vtt(vtt)
    zin = zipfile.ZipFile(src_epub)
    container = minidom.parseString(zin.read('META-INF/container.xml'))
    opf_path = container.getElementsByTagName('rootfile')[0].getAttribute('full-path')
    opf_dir = posixpath.dirname(opf_path)
    opf = minidom.parseString(zin.read(opf_path))
    manifest = {i.getAttribute('id'): i for i in opf.getElementsByTagNameNS(OPF_NS, 'item')}
    spine = [manifest[r.getAttribute('idref')] for r in opf.getElementsByTagNameNS(OPF_NS, 'itemref')]
    docs = []
    for item in spine:
        if item.getAttribute('media-type') != 'application/xhtml+xml':
            continue
        href = posixpath.join(opf_dir, item.getAttribute('href'))
        dom = minidom.parseString(zin.read(href))
        body = dom.getElementsByTagNameNS(XHTML_NS, 'body')[0]
        text, _ = book_text(body)
        docs.append({'item': item, 'href': href, 'dom': dom, 'body': body, 'text': text, 'pars': []})

    ranges, cue_words, matched_words = align(cues, docs)
    unmatched = [c['text'] for i, c in enumerate(cues) if i not in ranges]
    # A cue whose placed words fall well short of its own words is likely in the wrong place or
    # cut short (number words, pronunciation substitutions, a phrase that repeats in the book).
    partial = []
    for ci, (_, _, _, placed_words) in ranges.items():
        ratio = placed_words / max(1, len(tokens(cues[ci]['text'])))
        if ratio < 0.8:
            partial.append((ratio, cues[ci]['text']))
    skipped = []
    # Wrap from the end of each document back, so earlier offsets stay where they were found.
    for ci in sorted(ranges, key=lambda c: (ranges[c][0], ranges[c][1]), reverse=True):
        di, a, b, _ = ranges[ci]
        d = docs[di]
        a, b = widen(d['text'], a, b)
        pieces = block_pieces(d['body'], d['text'], a, b)
        # The cue's audio is shared across its pieces by letter count; the app keeps no finer timing.
        c = cues[ci]
        total_weight = sum(w for _, _, w in pieces)
        t = c['start']
        placed = []
        for k, (lo, hi, w) in enumerate(pieces):
            span_id = f'ra{ci + 1}' if len(pieces) == 1 else f'ra{ci + 1}-{k + 1}'
            end = c['end'] if k == len(pieces) - 1 else t + (c['end'] - c['start']) * w / total_weight
            placed.append((ci, k, span_id, t, end, lo, hi))
            t = end
        for ci_, k, span_id, start, end, lo, hi in reversed(placed):
            if wrap(d['dom'], d['body'], lo, hi, span_id):
                d['pars'].append((ci_, k, span_id, start, end))
            else:
                skipped.append(d['text'][lo:hi])

    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / 'audiobook.m4a'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', m4b, '-map', '0:a:0', '-c:a', 'copy', '-map_chapters', '-1', '-map_metadata', '-1', str(audio)], check=True)
        dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(audio)], capture_output=True, text=True, check=True).stdout)

        metadata = opf.getElementsByTagNameNS(OPF_NS, 'metadata')[0]
        man_el = opf.getElementsByTagNameNS(OPF_NS, 'manifest')[0]
        package = opf.documentElement
        prefix = package.getAttribute('prefix')
        if 'media:' not in prefix:
            package.setAttribute('prefix', (prefix + ' ' if prefix else '') + 'media: http://www.idpf.org/epub/vocab/overlays/#')

        def meta(prop, value, refines=None):
            m = opf.createElementNS(OPF_NS, 'meta')
            m.setAttribute('property', prop)
            if refines:
                m.setAttribute('refines', refines)
            m.appendChild(opf.createTextNode(value))
            metadata.appendChild(m)

        def item(item_id, href, media_type):
            el = opf.createElementNS(OPF_NS, 'item')
            el.setAttribute('id', item_id)
            el.setAttribute('href', href)
            el.setAttribute('media-type', media_type)
            man_el.appendChild(el)

        smil_files = {}
        total = 0.0
        for n, d in enumerate(docs):
            if not d['pars']:
                continue
            smil_id = f'mo{n + 1}'
            doc_href = d['item'].getAttribute('href')
            smil_href = posixpath.splitext(doc_href)[0] + '.smil'
            rel_doc = posixpath.basename(doc_href)
            rel_audio = posixpath.relpath(AUDIO_HREF, posixpath.dirname(doc_href) or '.')
            pars = []
            doc_dur = 0.0
            for _, _, span_id, start, end in sorted(d['pars']):
                doc_dur += end - start
                pars.append(f'<par id="par-{span_id}"><text src="{rel_doc}#{span_id}"/>'
                            f'<audio src="{rel_audio}" clipBegin="{clock(start)}" clipEnd="{clock(end)}"/></par>')
            smil_files[posixpath.join(opf_dir, smil_href)] = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<smil xmlns="http://www.w3.org/ns/SMIL" xmlns:epub="http://www.idpf.org/2007/ops" version="3.0">\n'
                f'<body><seq id="seq-{smil_id}" epub:textref="{rel_doc}">\n' + '\n'.join(pars) + '\n</seq></body>\n</smil>\n')
            item(smil_id, smil_href, 'application/smil+xml')
            d['item'].setAttribute('media-overlay', smil_id)
            meta('media:duration', clock(doc_dur), f'#{smil_id}')
            total += doc_dur
            # The highlight style lives in the document, so the source stylesheet stays untouched.
            head = d['dom'].getElementsByTagNameNS(XHTML_NS, 'head')[0]
            style = d['dom'].createElementNS(XHTML_NS, 'style')
            style.appendChild(d['dom'].createTextNode(f'.{ACTIVE_CLASS} {{ background-color: #ffe58a; color: #000; }}'))
            head.appendChild(style)
        meta('media:duration', clock(total))
        meta('media:active-class', ACTIVE_CLASS)
        item('audiobook', AUDIO_HREF, 'audio/mp4')

        written = {d['href']: d['dom'].documentElement.toxml() for d in docs}
        with zipfile.ZipFile(out_epub, 'w') as zout:
            zout.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            for info in zin.infolist():
                if info.filename == 'mimetype':
                    continue
                if info.filename == opf_path:
                    data = opf.toxml(encoding='utf-8')
                elif info.filename in written:
                    data = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n' + written[info.filename]).encode('utf-8')
                else:
                    data = zin.read(info.filename)
                zout.writestr(info.filename, data, compress_type=zipfile.ZIP_DEFLATED)
            for href, xml in smil_files.items():
                zout.writestr(href, xml, compress_type=zipfile.ZIP_DEFLATED)
            zout.write(audio, posixpath.join(opf_dir, AUDIO_HREF), compress_type=zipfile.ZIP_STORED)

    print(f'cues: {len(cues)}  pars: {sum(len(d["pars"]) for d in docs)}  unmatched cues: {len(unmatched)}  partly placed cues: {len(partial)}  unwrapped pieces: {len(skipped)}')
    print(f'cue words matched in the book: {matched_words}/{cue_words}  audio: {dur:.1f}s  overlay: {total:.1f}s')
    for t in unmatched:
        print(f'  unmatched: {t}')
    for ratio, t in partial:
        print(f'  partial: {ratio:.2f} {t}')
    for t in skipped:
        print(f'  not wrapped: {t}')


if __name__ == '__main__':
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
