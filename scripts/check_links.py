"""Check Markdown file targets and optionally public HTTP links without credentials."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(remote: bool = False) -> dict:
    local_errors, urls = [], set()
    files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
             *sorted((ROOT / "examples").rglob("*.md"))]
    for file in files:
        for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", file.read_text()):
            if target.startswith("https://"):
                urls.add(target)
            elif not target.startswith(("http://", "#", "mailto:")):
                path = urllib.parse.unquote(target.split("#", 1)[0])
                if not (file.parent / path).exists():
                    local_errors.append({"file": str(file.relative_to(ROOT)), "target": target})

    def fetch(url):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AHEADDB documentation verification"}, method="HEAD")
            with urllib.request.urlopen(request, timeout=15) as response:
                return {"url": url, "status": response.status, "resolved": response.url}
        except (urllib.error.URLError, TimeoutError) as exc:
            return {"url": url, "error": str(exc)}
    if remote:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            checked = list(pool.map(fetch, sorted(urls)))
    else:
        checked = []
    return {"markdown_files": len(files), "local_errors": local_errors, "remote": checked}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--remote", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = check(args.remote)
    serialized = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)
    raise SystemExit(bool(result["local_errors"] or any("error" in r for r in result["remote"])))
