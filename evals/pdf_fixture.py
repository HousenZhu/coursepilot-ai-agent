"""Small original text-only PDF fixtures, without an external document dependency."""
import textwrap


def make_pdf(pages: list[str]) -> bytes:
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for page in pages:
        page_id, stream_id = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_id} 0 R")
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>").encode())
        lines = textwrap.wrap(page, width=78)
        escaped = [line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') for line in lines]
        stream = ("BT /F1 11 Tf 50 740 Td 15 TL " + " T* ".join(f"({line}) Tj" for line in escaped) + " ET").encode("ascii")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode())
    return bytes(data)
