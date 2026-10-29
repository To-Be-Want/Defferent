"""Vendor pinned official KaTeX assets with npm integrity verification."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

VERSION = "0.19.1"
ROOT = Path(__file__).resolve().parents[1] / "cosight_server" / "web" / "libs" / "katex"

if __name__ == "__main__":
    with urlopen(f"https://registry.npmjs.org/katex/{VERSION}", timeout=60) as response:
        meta = json.load(response)
    url = meta["dist"]["tarball"]
    if not url.startswith("https://registry.npmjs.org/katex/-/"):
        raise ValueError("Unexpected package source")
    with urlopen(url, timeout=90) as response:
        payload = response.read(20 * 1024 * 1024)
    algorithm, expected = meta["dist"]["integrity"].split("-", 1)
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(payload).digest()).decode() != expected:
        raise ValueError("Package integrity mismatch")
    count = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            relative = None
            if member.name in ("package/dist/katex.min.js", "package/dist/katex.min.css"):
                relative = Path(member.name).name
            elif member.name.startswith("package/dist/fonts/") and member.name.endswith((".woff2", ".woff", ".ttf")):
                relative = "fonts/" + Path(member.name).name
            elif member.name == "package/LICENSE":
                relative = "LICENSE"
            if relative and member.isfile():
                target = ROOT / relative
                if target.exists():
                    raise FileExistsError("Refusing to overwrite vendored asset: " + str(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                count += 1
    for name in ("katex.min.js", "katex.min.css", "LICENSE"):
        if not (ROOT/name).is_file():
            raise RuntimeError("Missing KaTeX asset: " + name)
    (ROOT/"SOURCE.json").write_text(json.dumps({"version": VERSION, "url":url,"integrity":meta["dist"]["integrity"],
        "sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ("katex.min.js","katex.min.css")}},indent=2),encoding="utf-8")
    print(f"KaTeX {VERSION}: vendored {count} files; npm sha512 verified")
