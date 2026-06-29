# GPU Rental Price Following

This project tracks daily GPU rental prices as a proxy for AI compute demand.

The core idea is simple:

- If rental prices rise, AI compute demand may be expanding or supply may be tightening.
- If rental prices rise quickly versus the recent average, demand may be accelerating.
- If rental prices fall, demand may be cooling, supply may be expanding, or both.
- Marketplace availability, where available, helps separate demand pressure from supply changes.

## Data Sources

The tracker currently collects:

- Lambda public on-demand GPU pricing from `https://lambda.ai/pricing`
- Runpod public GPU pricing from `https://www.runpod.io/pricing`
- Vast.ai verified on-demand marketplace medians if `VAST_API_KEY` is set

Vast.ai is optional because its documented marketplace search endpoint requires an API key.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python gpu_price_tracker.py collect-and-report
```

Outputs are written to:

- `data/gpu_prices.csv`
- `data/latest_report.md`
- `data/collection_errors.log`, only when a source fails

## Web Dashboard

Start the local dashboard:

```powershell
python gpu_price_tracker.py serve
```

Then open:

```text
http://localhost:8000
```

The dashboard reads the generated CSV and Markdown report directly, so run `collect-and-report` first whenever you want fresh data.

## Optional Vast.ai Marketplace Data

Set a Vast.ai API key before running:

```powershell
$env:VAST_API_KEY = "your-vast-api-key"
python gpu_price_tracker.py collect-and-report
```

The Vast.ai signal is especially useful because it includes an availability count for matching rentable offers.

## Daily Tracking Locally

Use Windows Task Scheduler to run this daily:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "cd C:\Users\kris\Documents\gpu_price_following; .\.venv\Scripts\python.exe gpu_price_tracker.py collect-and-report"
```

Pick a consistent time each day. Consistency matters because GPU marketplaces can move intraday.

## Reading the Signal

The report classifies each provider/GPU pair:

- `baseline`: not enough observations yet
- `stable`: daily price movement is small
- `expanding`: price rose meaningfully day over day
- `accelerating`: price rose and is above the recent average
- `decreasing`: price fell meaningfully day over day

Treat this as a demand indicator, not a perfect demand measurement. GPU rental prices also move because of promotions, new capacity, regional mix, and provider pricing strategy.
