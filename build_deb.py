#!/usr/bin/env python3
"""Construit cadeaux_<version>_all.deb sans dpkg-deb : fonctionne sous Linux comme sous macOS."""
import hashlib
import io
import os
import tarfile
import time

VERSION = os.environ.get("VERSION", "1.0.0")  # la CI passe la version du tag
ROOT = os.path.dirname(os.path.abspath(__file__))
NOW = int(time.time())


def read(*parts):
    with open(os.path.join(ROOT, *parts), "rb") as f:
        return f.read()


def tar_gz(entries):
    """entries : (chemin, contenu ou None pour un dossier, droits), tout appartient à root."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for path, data, mode in entries:
            info = tarfile.TarInfo(path)
            info.mode, info.mtime, info.uname, info.gname = mode, NOW, "root", "root"
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def main():
    files = [("opt/cadeaux/server.py", read("server.py"), 0o755)]
    for name in sorted(os.listdir(os.path.join(ROOT, "static"))):
        if not name.startswith("."):
            files.append(("opt/cadeaux/static/" + name, read("static", name), 0o644))
    files += [("usr/lib/systemd/system/cadeaux.service", read("packaging", "cadeaux.service"), 0o644),
              ("etc/default/cadeaux", read("packaging", "default"), 0o644)]

    dirs = sorted({"/".join(p.split("/")[:i]) for p, _, _ in files for i in range(1, p.count("/") + 1)})
    data = tar_gz([("./", None, 0o755)] + [("./%s/" % d, None, 0o755) for d in dirs]
                  + [("./" + p, d, m) for p, d, m in files])

    size_kb = sum(len(d) for _, d, _ in files) // 1024 + 1
    control = read("packaging", "control").decode().format(version=VERSION, size=size_kb)
    md5sums = "".join("%s  %s\n" % (hashlib.md5(d).hexdigest(), p) for p, d, _ in files)
    ctrl = tar_gz([("./", None, 0o755),
                   ("./control", control.encode(), 0o644),
                   ("./conffiles", b"/etc/default/cadeaux\n", 0o644),
                   ("./md5sums", md5sums.encode(), 0o644)]
                  + [("./" + s, read("packaging", s), 0o755) for s in ("postinst", "prerm", "postrm")])

    out = os.path.join(ROOT, "cadeaux_%s_all.deb" % VERSION)
    with open(out, "wb") as f:
        f.write(b"!<arch>\n")
        for name, content in (("debian-binary", b"2.0\n"), ("control.tar.gz", ctrl), ("data.tar.gz", data)):
            f.write(("%-16s%-12d%-6d%-6d%-8s%-10d`\n" % (name, NOW, 0, 0, "100644", len(content))).encode())
            f.write(content + (b"\n" if len(content) % 2 else b""))
    print(out)


if __name__ == "__main__":
    main()
