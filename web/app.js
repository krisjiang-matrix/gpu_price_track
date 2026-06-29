const palette = [
  "#a8ff60",
  "#57d6ff",
  "#ffcf5a",
  "#ff8bd1",
  "#b8a3ff",
  "#6cffc4",
  "#ff9f66",
  "#f5f1e8",
];

const state = {
  rows: [],
  series: [],
  provider: "All",
  gpu: "All",
};

function parseCsv(text) {
  const rows = [];
  let row = [];
  let value = "";
  let insideQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const next = text[i + 1];
    if (char === '"' && insideQuotes && next === '"') {
      value += '"';
      i += 1;
    } else if (char === '"') {
      insideQuotes = !insideQuotes;
    } else if (char === "," && !insideQuotes) {
      row.push(value);
      value = "";
    } else if ((char === "\n" || char === "\r") && !insideQuotes) {
      if (char === "\r" && next === "\n") i += 1;
      row.push(value);
      if (row.some((cell) => cell.length)) rows.push(row);
      row = [];
      value = "";
    } else {
      value += char;
    }
  }
  if (value || row.length) {
    row.push(value);
    rows.push(row);
  }

  const headers = rows.shift() || [];
  return rows.map((cells) =>
    Object.fromEntries(headers.map((header, index) => [header, cells[index] || ""])),
  );
}

function median(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return 0;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function formatUsd(value) {
  return `$${Number(value).toFixed(2)}`;
}

function buildSeries(rows) {
  const grouped = new Map();
  for (const row of rows) {
    const date = row.observed_at_utc.slice(0, 10);
    const key = `${row.provider}||${row.gpu_model}||${date}`;
    if (!grouped.has(key)) {
      grouped.set(key, {
        provider: row.provider,
        gpu: row.gpu_model,
        date,
        values: [],
      });
    }
    grouped.get(key).values.push(Number(row.price_usd_per_gpu_hour));
  }

  const daily = [...grouped.values()].map((entry) => ({
    provider: entry.provider,
    gpu: entry.gpu,
    date: entry.date,
    price: median(entry.values),
  }));

  const bySeries = new Map();
  for (const point of daily) {
    const key = `${point.provider}||${point.gpu}`;
    if (!bySeries.has(key)) {
      bySeries.set(key, {
        key,
        provider: point.provider,
        gpu: point.gpu,
        label: `${point.provider} ${point.gpu}`,
        points: [],
      });
    }
    bySeries.get(key).points.push(point);
  }

  return [...bySeries.values()].map((entry) => ({
    ...entry,
    points: entry.points.sort((a, b) => a.date.localeCompare(b.date)),
  }));
}

function classify(points) {
  if (points.length < 2) return ["baseline", "Need another day to read direction."];
  const latest = points.at(-1).price;
  const previous = points.at(-2).price;
  const change = previous ? (latest - previous) / previous : 0;
  const avg = median(points.slice(-7).map((point) => point.price));
  const vsAverage = avg ? (latest - avg) / avg : 0;
  if (change >= 0.05 && vsAverage >= 0.03) return ["accelerating", `Up ${(change * 100).toFixed(1)}% day over day.`];
  if (change >= 0.02) return ["expanding", `Up ${(change * 100).toFixed(1)}% day over day.`];
  if (change <= -0.02) return ["decreasing", `Down ${Math.abs(change * 100).toFixed(1)}% day over day.`];
  return ["stable", `Moved ${(change * 100).toFixed(1)}% day over day.`];
}

function filteredSeries() {
  return state.series.filter((entry) => {
    const providerMatch = state.provider === "All" || entry.provider === state.provider;
    const gpuMatch = state.gpu === "All" || entry.gpu === state.gpu;
    return providerMatch && gpuMatch;
  });
}

function updateFilters() {
  const providers = ["All", ...new Set(state.series.map((entry) => entry.provider).sort())];
  const gpus = ["All", ...new Set(state.series.map((entry) => entry.gpu).sort())];
  const providerSelect = document.querySelector("#providerFilter");
  const gpuSelect = document.querySelector("#gpuFilter");

  providerSelect.innerHTML = providers.map((value) => `<option>${value}</option>`).join("");
  gpuSelect.innerHTML = gpus.map((value) => `<option>${value}</option>`).join("");

  providerSelect.addEventListener("change", (event) => {
    state.provider = event.target.value;
    render();
  });
  gpuSelect.addEventListener("change", (event) => {
    state.gpu = event.target.value;
    render();
  });
}

function renderMetrics(series) {
  const latestPoints = series.map((entry) => entry.points.at(-1)).filter(Boolean);
  const latestPrices = latestPoints.map((point) => point.price);
  const highest = latestPoints.toSorted((a, b) => b.price - a.price)[0];
  const allDates = state.rows.map((row) => row.observed_at_utc).sort();

  document.querySelector("#seriesCount").textContent = state.series.length;
  document.querySelector("#medianPrice").textContent = formatUsd(median(latestPrices));
  document.querySelector("#highestGpu").textContent = highest
    ? `${highest.gpu} ${formatUsd(highest.price)}`
    : "-";
  document.querySelector("#pointCount").textContent = state.rows.length;
  document.querySelector("#lastUpdated").textContent = allDates.length
    ? `Latest observation ${allDates.at(-1).replace("T", " ").replace("Z", " UTC")}`
    : "No observations yet";

  const signals = state.series.map((entry) => classify(entry.points)[0]);
  const accelerating = signals.filter((signal) => signal === "accelerating").length;
  const expanding = signals.filter((signal) => signal === "expanding").length;
  const decreasing = signals.filter((signal) => signal === "decreasing").length;
  const pulse = document.querySelector("#marketPulse");
  const detail = document.querySelector("#marketPulseDetail");
  if (accelerating || expanding) {
    pulse.textContent = accelerating ? "Accelerating" : "Expanding";
    detail.textContent = `${accelerating + expanding} tracked series show upward pressure.`;
  } else if (decreasing) {
    pulse.textContent = "Cooling";
    detail.textContent = `${decreasing} tracked series show falling prices.`;
  } else {
    pulse.textContent = "Baseline forming";
    detail.textContent = "Collect at least two days to unlock trend direction.";
  }
}

function renderChart(series) {
  const chart = document.querySelector("#chart");
  if (!series.length) {
    chart.innerHTML = `<div class="empty">No series match the selected filters.</div>`;
    return;
  }

  const dates = [...new Set(series.flatMap((entry) => entry.points.map((point) => point.date)))].sort();
  const prices = series.flatMap((entry) => entry.points.map((point) => point.price));
  const minPrice = Math.min(...prices, 0);
  const maxPrice = Math.max(...prices, 1);
  const width = 1040;
  const height = 360;
  const pad = { top: 24, right: 28, bottom: 52, left: 64 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const xFor = (date) => {
    if (dates.length === 1) return pad.left + innerWidth / 2;
    return pad.left + (dates.indexOf(date) / (dates.length - 1)) * innerWidth;
  };
  const yFor = (price) => pad.top + innerHeight - ((price - minPrice) / (maxPrice - minPrice || 1)) * innerHeight;

  const yTicks = Array.from({ length: 5 }, (_, index) => minPrice + ((maxPrice - minPrice) * index) / 4);
  const paths = series
    .slice(0, 8)
    .map((entry, index) => {
      const color = palette[index % palette.length];
      const points = entry.points.length === 1
        ? [
            { ...entry.points[0], date: dates[0] },
            { ...entry.points[0], date: dates.at(-1) || dates[0] },
          ]
        : entry.points;
      const d = points
        .map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"} ${xFor(point.date)} ${yFor(point.price)}`)
        .join(" ");
      const circles = entry.points
        .map((point) => `<circle cx="${xFor(point.date)}" cy="${yFor(point.price)}" r="4.5" fill="${color}"><title>${entry.label}: ${formatUsd(point.price)} on ${point.date}</title></circle>`)
        .join("");
      return `<path d="${d}" fill="none" stroke="${color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" />${circles}`;
    })
    .join("");

  const grid = yTicks
    .map((tick) => {
      const y = yFor(tick);
      return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" stroke="rgba(245,241,232,.12)" /><text class="tick" x="12" y="${y + 4}">${formatUsd(tick)}</text>`;
    })
    .join("");

  const xLabels = dates
    .map((date) => `<text class="axis" x="${xFor(date)}" y="${height - 16}" text-anchor="middle">${date.slice(5)}</text>`)
    .join("");

  chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grid}${paths}${xLabels}</svg>`;

  document.querySelector("#legend").innerHTML = series
    .slice(0, 8)
    .map((entry, index) => `<span><i style="background:${palette[index % palette.length]}"></i>${entry.label}</span>`)
    .join("");
}

function renderSignals(series) {
  const list = document.querySelector("#signalList");
  list.innerHTML = series
    .map((entry) => {
      const latest = entry.points.at(-1);
      const [signal, detail] = classify(entry.points);
      return `
        <div class="signal-item">
          <div class="signal-top">
            <span class="signal-name">${entry.provider} ${entry.gpu}</span>
            <span class="signal-pill ${signal}">${signal}</span>
          </div>
          <span class="signal-meta">${formatUsd(latest.price)} on ${latest.date}. ${detail}</span>
        </div>
      `;
    })
    .join("");
}

function markdownToHtml(markdown) {
  const lines = markdown.split(/\r?\n/);
  let html = "";
  let tableRows = [];

  const flushTable = () => {
    if (!tableRows.length) return;
    const rows = tableRows
      .filter((line) => !/^\|\s*-/.test(line))
      .map((line, index) => {
        const cells = line.split("|").slice(1, -1).map((cell) => cell.trim());
        const tag = index === 0 ? "th" : "td";
        return `<tr>${cells.map((cell) => `<${tag}>${cell}</${tag}>`).join("")}</tr>`;
      })
      .join("");
    html += `<table>${rows}</table>`;
    tableRows = [];
  };

  for (const line of lines) {
    if (line.startsWith("|")) {
      tableRows.push(line);
      continue;
    }
    flushTable();
    if (line.startsWith("# ")) html += `<h3>${line.slice(2)}</h3>`;
    else if (line.trim()) html += `<p>${line}</p>`;
  }
  flushTable();
  return html;
}

async function loadReport() {
  try {
    const response = await fetch("data/latest_report.md", { cache: "no-store" });
    document.querySelector("#report").innerHTML = markdownToHtml(await response.text());
  } catch {
    document.querySelector("#report").innerHTML = `<p>Run <code>python gpu_price_tracker.py collect-and-report</code> to generate a report.</p>`;
  }
}

function render() {
  const visibleSeries = filteredSeries();
  renderMetrics(visibleSeries);
  renderChart(visibleSeries);
  renderSignals(visibleSeries);
}

async function init() {
  try {
    const response = await fetch("data/gpu_prices.csv", { cache: "no-store" });
    state.rows = parseCsv(await response.text());
    state.series = buildSeries(state.rows);
    updateFilters();
    render();
  } catch (error) {
    document.querySelector("#chart").innerHTML = `<div class="empty">Could not load <code>data/gpu_prices.csv</code>. Run collection first.</div>`;
    console.error(error);
  }
  await loadReport();
}

init();
