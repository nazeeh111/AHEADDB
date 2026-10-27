"""Fetch two UCI CC BY 4.0 datasets and normalize them into local DuckDB snapshots.

Raw archives and snapshots stay under ignored .data/. No API key is needed.
Source availability timestamps do not exist; these experiments ASSUME next-day
availability, recorded in provenance. This is not an ingestion-latency audit.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import duckdb
import pandas as pd

from aheaddb import Event, Schema, Snapshot

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "retail": {
        "url": "https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip",
        "page": "https://archive.ics.uci.edu/dataset/352/online+retail",
        "citation": "Chen, D. (2015). Online Retail. UCI. doi:10.24432/C5BW33",
    },
    "beijing": {
        "url": "https://archive.ics.uci.edu/static/public/501/beijing%2Bmulti%2Bsite%2Bair%2Bquality%2Bdata.zip",
        "page": "https://archive.ics.uci.edu/dataset/501/beijing+multi+site+air+quality+data",
        "citation": "Chen, S. (2017). Beijing Multi-Site Air Quality. UCI. doi:10.24432/C5RK5G",
    },
}


def download(dataset: str, dest: Path) -> tuple[bytes, dict]:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{dataset}.zip"
    if not path.exists():
        print(f"Downloading {dataset} from official UCI archive", flush=True)
        req = urllib.request.Request(SOURCES[dataset]["url"], headers={"User-Agent": "AHEADDB/0.1 dataset reproduction"})
        with urllib.request.urlopen(req, timeout=90) as response:
            data = response.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("Dataset exceeds bounded 64 MiB download")
        path.write_bytes(data)
    data = path.read_bytes()
    return data, {**SOURCES[dataset], "sha256": hashlib.sha256(data).hexdigest(),
                  "bytes": len(data), "license": "CC-BY-4.0", "retrieved_utc": str(pd.Timestamp.now(tz="UTC"))}


def read_xlsx(data: bytes) -> pd.DataFrame:
    """Read the simple UCI worksheet without installing a spreadsheet runtime."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        strings_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in item.iter(f"{ns}t")) for item in strings_root]
        records = []
        with archive.open("xl/worksheets/sheet1.xml") as sheet:
            for _, row in ET.iterparse(sheet, events=("end",)):
                if row.tag != f"{ns}row":
                    continue
                cells = {}
                for cell in row:
                    name = "".join(c for c in cell.attrib["r"] if c.isalpha())
                    value = cell.find(f"{ns}v")
                    value = value.text if value is not None else None
                    if cell.attrib.get("t") == "s" and value is not None:
                        value = strings[int(value)]
                    cells[name] = value
                records.append(cells)
                row.clear()
    frame = pd.DataFrame(records)
    frame.columns = frame.iloc[0]
    return frame.iloc[1:].reset_index(drop=True)


def retail(data: bytes) -> tuple[dict[str, pd.DataFrame], Schema, dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        frame = read_xlsx(archive.read(next(n for n in archive.namelist() if n.endswith(".xlsx"))))
    raw_rows = len(frame)
    for c in ["Quantity", "UnitPrice", "CustomerID", "InvoiceDate"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.loc[frame.CustomerID.notna() & (frame.Quantity > 0) & (frame.UnitPrice > 0) &
                      ~frame.InvoiceNo.astype(str).str.upper().str.startswith("C")].copy()
    frame["entity_id"] = frame.CustomerID.astype(int).astype(str)
    frame["occurred_at"] = pd.to_datetime(frame.InvoiceDate, unit="D", origin="1899-12-30", utc=True)
    frame["spend"] = frame.Quantity * frame.UnitPrice
    events = frame.groupby(["InvoiceNo", "entity_id", "occurred_at"], as_index=False).agg(spend=("spend", "sum"))
    events["id"] = [f"invoice-{i}" for i in range(len(events))]
    events["available_at"] = events.occurred_at + pd.Timedelta(days=1)
    entities = events.groupby("entity_id", as_index=False).available_at.min()
    schema = Schema(events=(Event(table="purchases", values=("spend",)),))
    return {"entities": entities, "purchases": events.drop(columns="InvoiceNo")}, schema, {
        "raw_rows": raw_rows, "retained_positive_line_items": len(frame), "invoices": len(events),
        "entities": len(entities), "start": str(events.occurred_at.min()), "end": str(events.occurred_at.max()),
        "transform": "Drop missing customer IDs, cancellations, quantity<=0 and price<=0; sum positive line spend per invoice/customer/time. Forecast gross positive spend, not net revenue.",
        "availability_assumption": "Each invoice is available exactly 24 hours after its event timestamp; customer exists at its first available invoice. Naive source timestamps interpreted UTC, not verified source timezone.",
    }


def beijing(data: bytes) -> tuple[dict[str, pd.DataFrame], Schema, dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as outer:
        nested = next(n for n in outer.namelist() if n.endswith(".zip"))
        with zipfile.ZipFile(io.BytesIO(outer.read(nested))) as archive:
            frames = [pd.read_csv(io.BytesIO(archive.read(n))) for n in archive.namelist()
                      if n.endswith(".csv") and "PRSA_Data_" in n and not n.startswith("__MACOSX")]
    frame = pd.concat(frames, ignore_index=True)
    frame["date"] = pd.to_datetime(frame[["year", "month", "day"]], utc=True)
    daily = frame.groupby(["station", "date"], as_index=False).agg(
        pm25=("PM2.5", "mean"), valid_hours=("PM2.5", "count"), temperature=("TEMP", "mean"))
    daily.loc[daily.valid_hours < 18, "pm25"] = float("nan")
    daily["entity_id"] = daily.station.astype(str)
    daily["occurred_at"] = daily.date + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    daily["available_at"] = daily.date + pd.Timedelta(days=1)
    daily["id"] = daily.entity_id + "-" + daily.date.dt.strftime("%Y-%m-%d")
    entities = pd.DataFrame({"entity_id": sorted(daily.entity_id.unique()), "available_at": daily.available_at.min()})
    events = daily[["id", "entity_id", "occurred_at", "available_at", "pm25", "temperature"]]
    schema = Schema(events=(Event(table="readings", values=("pm25", "temperature")),))
    return {"entities": entities, "readings": events}, schema, {
        "raw_rows": len(frame), "daily_rows": len(events), "entities": len(entities),
        "start": str(events.occurred_at.min()), "end": str(events.occurred_at.max()),
        "daily_missing_target_rows": int(events.pm25.isna().sum()),
        "transform": "Group hourly rows per station/day; daily PM2.5 is NaN unless >=18 hourly PM2.5 values. Temperature mean is an additional historical feature. Evaluation excludes every sample lacking an observed next-day PM2.5 label.",
        "availability_assumption": "Daily measurement is an event at 23:59:59 and is available at next midnight. Original data has no ingestion timestamps. Source clock interpreted UTC for an explicit experiment, not a claim about actual Beijing ingestion latency.",
    }


def fetch(dataset: str, dest: Path) -> Path:
    data, provenance = download(dataset, dest)
    path = dest / f"{dataset}.duckdb"
    schema_path = dest / f"{dataset}-schema.json"
    provenance_path = dest / f"{dataset}-provenance.json"
    if path.exists():
        if schema_path.exists() and provenance_path.exists():
            previous = json.loads(provenance_path.read_text())
            previous_schema = Schema.model_validate_json(schema_path.read_text())
            if (previous.get("sha256") == provenance["sha256"] and
                    previous.get("snapshot_version") == Snapshot.duckdb(path, previous_schema).version):
                print(f"Reusing verified generated {dataset} snapshot {path}", flush=True)
                return path
        raise FileExistsError(f"Refusing to replace modified or unrecognized snapshot {path}; use a fresh --dest")
    if schema_path.exists() or provenance_path.exists():
        raise FileExistsError("Existing metadata without its snapshot; use a fresh --dest")
    print(f"Normalizing {dataset} ({len(data):,} archive bytes)", flush=True)
    tables, schema, transform = globals()[dataset](data)
    with duckdb.connect(str(path)) as conn:
        for name, frame in tables.items():
            conn.register("source_frame", frame)
            conn.execute(f'CREATE TABLE "{name}" AS SELECT * FROM source_frame')
            conn.unregister("source_frame")
    schema_path.write_text(schema.model_dump_json(indent=2))
    receipt = {**provenance, **transform, "snapshot_version": Snapshot.duckdb(path, schema).version}
    provenance_path.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["retail", "beijing", "all"], default="all")
    parser.add_argument("--dest", type=Path, default=ROOT / ".data")
    args = parser.parse_args()
    for name in SOURCES if args.dataset == "all" else [args.dataset]:
        fetch(name, args.dest)
