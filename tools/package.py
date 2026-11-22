# SPDX-License-Identifier: GPL-3.0-only
"""Create production-only binary and source ZIPs, with checksums."""
import hashlib
import json
import re
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'^#define NC_VERSION "([^"]+)"', (ROOT/"fan-control"/"src"/"nuc.h").read_text(encoding="utf-8"), re.MULTILINE).group(1)


def main():
    destination = ROOT/"dist"
    destination.mkdir(exist_ok=True)
    common = [(ROOT/"fan-control"/"build"/"nuc-engine.exe","nuc-engine.exe")]
    common += [(ROOT/name,name) for name in ("README.md","README.fr.md","API.md","PROTOCOL.md","LICENSE","Install-Service.ps1","Uninstall-Service.ps1")]
    common += [(ROOT/"python"/"nuc_client.py","python/nuc_client.py")]
    common += [(path,"examples/"+path.name) for path in sorted((ROOT/"examples").glob("*.py"))]
    common += [(ROOT/"fan-control"/"README.md","fan-control/README.md"),(ROOT/"ui"/"README.md","ui/README.md")]
    sources = [(ROOT/"build.ps1","build.ps1"),(ROOT/"fan-control"/"build.ps1","fan-control/build.ps1")]
    for directory in ("fan-control/src","tests","tools"):
        sources += [(path,path.relative_to(ROOT).as_posix()) for path in (ROOT/directory).rglob("*")
                    if path.is_file() and path.suffix in (".c",".h",".py") and "__pycache__" not in path.parts]
    engine_sources = [(path,name) for path,name in sources if name=="build.ps1" or name.startswith("fan-control/")]
    results = {}
    for label,files in (("windows-x64",common+engine_sources),("source",common+sources)):
        path = destination/f"nuc-engine-{VERSION}-{label}.zip"
        hashes = {name:hashlib.sha256(file.read_bytes()).hexdigest() for file,name in files}
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as archive:
            for file,name in sorted(files,key=lambda pair:pair[1]):
                archive.write(file,Path("nuc-engine")/name)
            archive.writestr("nuc-engine/SHA256SUMS.json",json.dumps(hashes,indent=2))
        results[path.name] = dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (destination/"archives.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(json.dumps(results,indent=2))


if __name__=="__main__":
    main()
