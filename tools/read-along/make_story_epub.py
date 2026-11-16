"""Build the source EPUB for the read-along test: a plain EPUB 3 whose XHTML carries no
element ids, so the read-along builder has to find each spoken piece in the markup by its
text rather than lean on ids a book may not have."""
import sys
import zipfile
from html import escape
from pathlib import Path

TITLE = 'Aidan and Declan and the Star That Fell'
STORY = Path(__file__).with_name('star_that_fell.txt')
COVER = Path(__file__).with_name('star_that_fell_cover.jpg')


def build(out_path):
    paragraphs = [p.strip() for p in STORY.read_text(encoding='utf-8').split('\n\n') if p.strip()]
    body = []
    for p in paragraphs:
        if p == 'The End.':
            body.append(f'<p class="end"><b>{escape(p, quote=False)}</b></p>')
        else:
            # *word* in the text file is emphasis in the book
            parts = p.split('*')
            html = ''.join(f'<i>{escape(s, quote=False)}</i>' if i % 2 else escape(s, quote=False) for i, s in enumerate(parts))
            body.append(f'<p>{html}</p>')
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        f'<head><meta charset="utf-8"/><title>{TITLE}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body>\n<section epub:type="chapter">\n<h1>{TITLE}</h1>\n' + '\n'.join(body) + '\n</section>\n</body>\n</html>\n'
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        '<head><meta charset="utf-8"/><title>Contents</title></head>\n'
        f'<body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol><li><a href="chapter1.xhtml">{TITLE}</a></li></ol></nav></body>\n</html>\n'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '<dc:identifier id="uid">urn:uuid:5f0c2a4e-27a1-4b8e-9d27-000000000027</dc:identifier>\n'
        f'<dc:title>{TITLE}</dc:title>\n<dc:creator>ebook2audiobook-expressive</dc:creator>\n<dc:language>en</dc:language>\n'
        '<meta property="dcterms:modified">2026-11-15T00:00:00Z</meta>\n'
        # Calibre, which the app runs on every input, finds the cover by this EPUB 2 meta.
        '<meta name="cover" content="cover"/>\n'
        '</metadata>\n<manifest>\n'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        '<item id="css" href="style.css" media-type="text/css"/>\n'
        '<item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>\n'
        '<item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>\n'
        '</manifest>\n<spine>\n<itemref idref="chapter1"/>\n</spine>\n</package>\n'
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
        '</container>\n'
    )
    css = 'body { font-family: serif; line-height: 1.5; } h1 { text-align: center; } p.end { text-align: center; }\n'
    with zipfile.ZipFile(out_path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/content.opf', opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/nav.xhtml', nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/style.css', css, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/chapter1.xhtml', chapter, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/cover.jpg', COVER.read_bytes(), compress_type=zipfile.ZIP_STORED)


if __name__ == '__main__':
    build(sys.argv[1] if len(sys.argv) > 1 else 'star_that_fell.epub')
