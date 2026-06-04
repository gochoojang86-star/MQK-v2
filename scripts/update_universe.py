"""
Download KIS domestic stock master files and build data/universe.csv.

The KIS API guide points domestic stock universe users to the KIS master files.
This script keeps the scanner universe concrete without relying on a manual
KIS_UNIVERSE env var.
"""
from __future__ import annotations

import csv
import io
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "universe.csv"

MASTERS = [
    {
        "market": "KOSPI",
        "url": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
        "member": "kospi_code.mst",
        "meta_len": 228,
    },
    {
        "market": "KOSDAQ",
        "url": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
        "member": "kosdaq_code.mst",
        "meta_len": 222,
    },
]


def download_zip(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MQK-v2/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def normalize_ticker(raw: str) -> str:
    token = raw.strip()
    if len(token) >= 6 and token[-6:].isdigit():
        return token[-6:]
    return token


def parse_master(content: bytes, market: str, meta_len: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    text = content.decode("cp949", errors="ignore")
    for line in text.splitlines():
        if len(line) <= 21:
            continue
        base = line[: max(len(line) - meta_len, 21)]
        ticker = normalize_ticker(base[:9])
        standard_code = base[9:21].strip()
        name = base[21:].strip()
        if not (len(ticker) == 6 and ticker.isdigit() and name):
            continue
        rows.append({
            "ticker": ticker,
            "name": name,
            "market": market,
            "standard_code": standard_code,
        })
    return rows


def load_master(spec: dict[str, object]) -> list[dict[str, str]]:
    payload = download_zip(str(spec["url"]))
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        member = str(spec["member"])
        if member not in zf.namelist():
            raise RuntimeError(f"{member} not found in {spec['url']}")
        return parse_master(
            zf.read(member),
            market=str(spec["market"]),
            meta_len=int(spec["meta_len"]),
        )


def write_universe(rows: list[dict[str, str]], output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["market"], r["ticker"]))
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=output.parent,
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=["ticker", "name", "market", "standard_code"])
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(tmp.name)
    tmp_path.replace(output)


def main() -> None:
    rows: list[dict[str, str]] = []
    for spec in MASTERS:
        rows.extend(load_master(spec))
    if not rows:
        raise RuntimeError("No universe rows parsed from KIS master files.")
    write_universe(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
