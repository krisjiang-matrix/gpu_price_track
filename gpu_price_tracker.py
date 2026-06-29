from __future__ import annotations

import argparse
import csv
import http.server
import json
import os
import re
import socketserver
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup


DATA_DIR = Path("data")
SNAPSHOT_PATH = DATA_DIR / "gpu_prices.csv"
REPORT_PATH = DATA_DIR / "latest_report.md"
WEB_DIR = Path("web")

USER_AGENT = (
    "gpu-price-following/0.1 "
    "(daily AI infrastructure demand research; contact: local-user)"
)

TRACKED_GPU_PATTERNS = {
    "B300": re.compile(r"\bB300\b", re.I),
    "B200": re.compile(r"\bB200\b", re.I),
    "GH200": re.compile(r"\bGH200\b", re.I),
    "H200": re.compile(r"\bH200\b", re.I),
    "H100": re.compile(r"\bH100\b", re.I),
    "A100": re.compile(r"\bA100\b", re.I),
    "L40S": re.compile(r"\bL40S\b", re.I),
    "L4": re.compile(r"\bL4\b", re.I),
    "RTX 6000": re.compile(r"\bRTX\s*(?:Pro\s*)?6000\b", re.I),
    "RTX 5090": re.compile(r"\b(?:RTX\s*)?5090\b", re.I),
    "RTX 4090": re.compile(r"\b(?:RTX\s*)?4090\b", re.I),
    "A6000": re.compile(r"\bA6000\b", re.I),
    "A10": re.compile(r"\bA10\b", re.I),
    "V100": re.compile(r"\bV100\b", re.I),
}


@dataclass(frozen=True)
class PriceRecord:
    observed_at_utc: str
    provider: str
    market: str
    gpu_model: str
    price_usd_per_gpu_hour: float
    source_url: str
    availability_count: int | None = None
    raw_label: str | None = None
    notes: str | None = None


def fetch_text(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def normalize_gpu_model(label: str) -> str | None:
    for model, pattern in TRACKED_GPU_PATTERNS.items():
        if pattern.search(label):
            return model
    return None


def parse_price_blocks(
    *,
    provider: str,
    market: str,
    source_url: str,
    text: str,
    observed_at_utc: str,
) -> list[PriceRecord]:
    records: list[PriceRecord] = []

    # Public pricing pages usually render GPU name and price close together.
    # We intentionally keep this broad so minor marketing-copy changes do not
    # break daily collection.
    pattern = re.compile(
        r"(?P<label>(?:NVIDIA|AMD|RTX|Tesla|GeForce)?\s*"
        r"(?:B300|B200|GH200|H200|H100|A100|L40S|L4|RTX\s*(?:Pro\s*)?6000|"
        r"RTX\s*5090|5090|RTX\s*4090|4090|A6000|A10|V100)"
        r"[^$]{0,160}?)\$\s*(?P<price>\d+(?:\.\d+)?)",
        re.I,
    )

    seen: set[tuple[str, float, str]] = set()
    for match in pattern.finditer(text):
        raw_label = " ".join(match.group("label").split())
        model = normalize_gpu_model(raw_label)
        if not model:
            continue

        price = float(match.group("price"))
        if not 0.05 <= price <= 50:
            continue

        key = (model, price, raw_label[:80])
        if key in seen:
            continue
        seen.add(key)

        records.append(
            PriceRecord(
                observed_at_utc=observed_at_utc,
                provider=provider,
                market=market,
                gpu_model=model,
                price_usd_per_gpu_hour=price,
                source_url=source_url,
                raw_label=raw_label,
            )
        )

    return records


def collect_lambda(observed_at_utc: str) -> list[PriceRecord]:
    url = "https://lambda.ai/pricing"
    text = fetch_text(url)
    start = text.find("Instances pricing")
    if start >= 0:
        text = text[start:]
    return parse_price_blocks(
        provider="Lambda",
        market="on-demand",
        source_url=url,
        text=text,
        observed_at_utc=observed_at_utc,
    )


def collect_runpod(observed_at_utc: str) -> list[PriceRecord]:
    url = "https://www.runpod.io/pricing"
    text = fetch_text(url)
    start = text.find("GPU Community Cloud Secure Cloud")
    end = text.find("Thank you!", start)
    pods_text = text[start:end] if start >= 0 and end > start else text

    row_pattern = re.compile(
        r"(?P<label>B300|H200|B200|RTX\s+Pro\s+6000|H100\s+NVL|H100\s+PCIe|"
        r"H100\s+SXM|A100\s+PCIe|A100\s+SXM|L40S|RTX\s+6000\s+Ada|"
        r"RTX\s+A6000|RTX\s+5090|L4|RTX\s+4090)"
        r"\s+(?P<vram>\d+)\s+GB(?:\s+(?:HBM3e|VRAM))*\s+"
        r"(?P<ram>\d+)\s+GB\s+RAM\s+(?P<vcpus>\d+)\s+vCPUs\s+\$\s*"
        r"(?P<price>\d+(?:\.\d+)?)\s*/hr",
        re.I,
    )

    records: list[PriceRecord] = []
    seen: set[tuple[str, float, str]] = set()
    for match in row_pattern.finditer(pods_text):
        raw_label = " ".join(match.group("label").split())
        model = normalize_gpu_model(raw_label)
        if not model:
            continue
        price = float(match.group("price"))
        key = (model, price, raw_label)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            PriceRecord(
                observed_at_utc=observed_at_utc,
                provider="Runpod",
                market="pods-community-secure-cloud",
                gpu_model=model,
                price_usd_per_gpu_hour=price,
                source_url=url,
                raw_label=raw_label,
                notes=f"{match.group('vram')} GB VRAM, {match.group('ram')} GB RAM, {match.group('vcpus')} vCPUs",
            )
        )

    return records


def collect_vast(observed_at_utc: str) -> list[PriceRecord]:
    api_key = os.getenv("VAST_API_KEY")
    if not api_key:
        return []

    url = "https://console.vast.ai/api/v0/bundles/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    payload = {
        "limit": 500,
        "type": "ondemand",
        "verified": {"eq": True},
        "rentable": {"eq": True},
        "rented": {"eq": False},
        "gpu_arch": {"eq": "nvidia"},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=45)
    response.raise_for_status()
    data = response.json()
    offers = data.get("offers", [])
    if isinstance(offers, dict):
        offers = [offers]

    grouped: dict[str, list[float]] = defaultdict(list)
    for offer in offers:
        gpu_name = str(offer.get("gpu_name") or "")
        model = normalize_gpu_model(gpu_name)
        if not model:
            continue

        total_hour = (
            offer.get("dph_total")
            or offer.get("dph_total_adj")
            or (offer.get("search") or {}).get("totalHour")
        )
        num_gpus = offer.get("num_gpus") or 1
        try:
            per_gpu_hour = float(total_hour) / max(float(num_gpus), 1.0)
        except (TypeError, ValueError):
            continue
        if 0.05 <= per_gpu_hour <= 50:
            grouped[model].append(per_gpu_hour)

    records: list[PriceRecord] = []
    for model, prices in sorted(grouped.items()):
        records.append(
            PriceRecord(
                observed_at_utc=observed_at_utc,
                provider="Vast.ai",
                market="verified-on-demand-marketplace",
                gpu_model=model,
                price_usd_per_gpu_hour=round(statistics.median(prices), 4),
                source_url=url,
                availability_count=len(prices),
                notes="Median of verified rentable marketplace offers.",
            )
        )
    return records


def replace_rows_for_observed_date(
    *,
    path: Path,
    observed_date: str,
    date_column: str,
    fieldnames: list[str],
) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if not row.get(date_column, "").startswith(observed_date)
        ]


def write_records(records: Iterable[PriceRecord], path: Path = SNAPSHOT_PATH) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    rows = [asdict(record) for record in records]
    if not rows:
        return

    fieldnames = list(rows[0].keys())
    observed_date = rows[0]["observed_at_utc"][:10]
    existing_rows = replace_rows_for_observed_date(
        path=path,
        observed_date=observed_date,
        date_column="observed_at_utc",
        fieldnames=fieldnames,
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerows(rows)


def read_history(path: Path = SNAPSHOT_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def daily_medians(rows: Iterable[dict[str, str]]) -> dict[tuple[str, str], list[tuple[date, float, int | None]]]:
    values: dict[tuple[str, str, date], list[float]] = defaultdict(list)
    availability: dict[tuple[str, str, date], list[int]] = defaultdict(list)

    for row in rows:
        try:
            observed_date = datetime.fromisoformat(
                row["observed_at_utc"].replace("Z", "+00:00")
            ).date()
            price = float(row["price_usd_per_gpu_hour"])
        except (KeyError, ValueError):
            continue

        key = (row["provider"], row["gpu_model"], observed_date)
        values[key].append(price)
        if row.get("availability_count"):
            try:
                availability[key].append(int(row["availability_count"]))
            except ValueError:
                pass

    series: dict[tuple[str, str], list[tuple[date, float, int | None]]] = defaultdict(list)
    for (provider, gpu_model, observed_date), prices in values.items():
        counts = availability.get((provider, gpu_model, observed_date), [])
        supply = int(statistics.median(counts)) if counts else None
        series[(provider, gpu_model)].append(
            (observed_date, round(statistics.median(prices), 4), supply)
        )

    return {key: sorted(points) for key, points in series.items()}


def classify_signal(points: list[tuple[date, float, int | None]]) -> tuple[str, str]:
    if len(points) < 2:
        return "baseline", "Need at least two observations before reading direction."

    latest_date, latest_price, latest_supply = points[-1]
    previous_date, previous_price, previous_supply = points[-2]
    if previous_price == 0:
        return "baseline", "Previous price is zero, cannot calculate change."

    day_change = (latest_price - previous_price) / previous_price
    latest_7 = points[-7:]
    average_7 = statistics.mean(price for _, price, _ in latest_7)
    vs_7 = (latest_price - average_7) / average_7 if average_7 else 0

    supply_phrase = ""
    if latest_supply is not None and previous_supply is not None:
        supply_change = latest_supply - previous_supply
        if supply_change < 0:
            supply_phrase = " Supply also tightened, which strengthens the demand signal."
        elif supply_change > 0:
            supply_phrase = " Supply expanded, so the price move may reflect mix or new capacity."

    if day_change >= 0.05 and vs_7 >= 0.03:
        return (
            "accelerating",
            f"Price rose {day_change:.1%} day-over-day and is {vs_7:.1%} above its 7-observation average.{supply_phrase}",
        )
    if day_change >= 0.02:
        return (
            "expanding",
            f"Price rose {day_change:.1%} day-over-day, suggesting stronger demand or tighter supply.{supply_phrase}",
        )
    if day_change <= -0.02:
        return (
            "decreasing",
            f"Price fell {abs(day_change):.1%} day-over-day, suggesting softer demand, more supply, or both.{supply_phrase}",
        )
    return (
        "stable",
        f"Price moved {day_change:.1%} day-over-day, which is inside the noise band.",
    )


def build_report(rows: list[dict[str, str]]) -> str:
    series = daily_medians(rows)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "# GPU Rental Price Demand Signal",
        "",
        f"Generated: {generated}",
        "",
        "Interpretation: rising GPU rental prices can suggest expanding AI compute demand, especially when marketplace availability is flat or falling. Falling prices usually point to softer demand, more supply, or a shift in GPU mix.",
        "",
        "| Provider | GPU | Latest date | Latest $/GPU/hr | Signal | Evidence |",
        "|---|---:|---:|---:|---|---|",
    ]

    for (provider, gpu_model), points in sorted(series.items()):
        latest_date, latest_price, _ = points[-1]
        signal, evidence = classify_signal(points)
        lines.append(
            f"| {provider} | {gpu_model} | {latest_date.isoformat()} | "
            f"${latest_price:.4f} | {signal} | {evidence} |"
        )

    if not series:
        lines.append("| n/a | n/a | n/a | n/a | baseline | No data collected yet. |")

    return "\n".join(lines) + "\n"


def collect() -> list[PriceRecord]:
    observed_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records: list[PriceRecord] = []
    errors: list[str] = []
    statuses: list[dict[str, str | int]] = []

    for collector in (collect_lambda, collect_runpod, collect_vast):
        try:
            source_records = collector(observed_at_utc)
            records.extend(source_records)
            statuses.append(
                {
                    "observed_at_utc": observed_at_utc,
                    "collector": collector.__name__,
                    "records_collected": len(source_records),
                    "status": "ok" if source_records else "no_rows",
                }
            )
        except requests.RequestException as exc:
            errors.append(f"{collector.__name__}: {exc}")
            statuses.append(
                {
                    "observed_at_utc": observed_at_utc,
                    "collector": collector.__name__,
                    "records_collected": 0,
                    "status": f"error: {exc}",
                }
            )

    write_records(records)
    write_source_statuses(statuses)

    if errors:
        DATA_DIR.mkdir(exist_ok=True)
        (DATA_DIR / "collection_errors.log").open("a", encoding="utf-8").write(
            "\n".join(f"{observed_at_utc} {error}" for error in errors) + "\n"
        )

    return records


def write_source_statuses(statuses: list[dict[str, str | int]]) -> None:
    if not statuses:
        return
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / "source_status.csv"
    fieldnames = ["observed_at_utc", "collector", "records_collected", "status"]
    observed_date = str(statuses[0]["observed_at_utc"])[:10]
    existing_rows = replace_rows_for_observed_date(
        path=path,
        observed_date=observed_date,
        date_column="observed_at_utc",
        fieldnames=fieldnames,
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerows(statuses)


def serve_dashboard(port: int = 8000) -> None:
    class DashboardHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def translate_path(self, path: str) -> str:
            if path in {"/", "/index.html"}:
                return str((WEB_DIR / "index.html").resolve())
            return super().translate_path(path)

    class DashboardServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    with DashboardServer(("", port), DashboardHandler) as httpd:
        print(f"Serving GPU dashboard at http://localhost:{port}")
        print("Press Ctrl+C to stop.")
        httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Track GPU rental prices daily.")
    parser.add_argument(
        "command",
        choices=("collect", "report", "collect-and-report", "serve"),
        help="Action to run.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the local dashboard server.",
    )
    args = parser.parse_args()

    if args.command == "serve":
        serve_dashboard(args.port)
        return

    if args.command in {"collect", "collect-and-report"}:
        records = collect()
        print(json.dumps({"records_collected": len(records)}, indent=2))

    if args.command in {"report", "collect-and-report"}:
        report = build_report(read_history())
        DATA_DIR.mkdir(exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(report)


if __name__ == "__main__":
    main()
