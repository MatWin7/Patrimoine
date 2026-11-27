// --- Performance par période ------------------------------------------------------
// Découpage et dates choisis gardés dans le navigateur (préférence locale, sans montant).

const PERF_KEY = PREF + "performance";
const perf = {
  period: "month", preset: "all", start: null, end: null, chart: "pct",
  sort: { key: "period", dir: "desc" }, // tableau : la période la plus récente en haut
  hidden: [], // ancien filtre par actif (D-046), repris une fois dans le filtre de lignes
};
try {
  Object.assign(perf, JSON.parse(localStorage.getItem(PERF_KEY)) ?? {});
} catch {
  // stockage indisponible ou illisible : réglages par défaut
}
let lastPerformance = null;

function savePerf() {
  try {
    localStorage.setItem(PERF_KEY, JSON.stringify(perf));
  } catch {
    // navigation privée : le réglage ne sera simplement pas retenu
  }
}

// Date locale -> "AAAA-MM-JJ" (toISOString passerait en UTC et pourrait reculer d'un jour).
const isoDate = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const frDate = (iso) => iso.split("-").reverse().join("/");

function presetStart(preset) {
  const d = new Date();
  if (preset === "ytd") return `${d.getFullYear()}-01-01`;
  if (preset === "12m") d.setFullYear(d.getFullYear() - 1);
  else if (preset === "6m") d.setMonth(d.getMonth() - 6);
  else if (preset === "3m") d.setMonth(d.getMonth() - 3);
  else if (preset === "1m") d.setMonth(d.getMonth() - 1);
  else return null; // « depuis le début »
  return isoDate(d);
}

// Libellé d'une période ; `short` pour l'axe du graphique, où la place manque.
function periodLabel(p, period, short = false) {
  if (period === "day") return short ? frDate(p.first).slice(0, 5) : frDate(p.first);
  if (period === "week" && short) return `S${p.label.split("-S")[1]}`;
  if (period === "month" && short) {
    const [y, m] = p.label.split("-");
    return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" });
  }
  if (period === "week") return `Sem. ${p.label.split("-S")[1]} · ${frDate(p.first).slice(0, 5)} → ${frDate(p.last)}`;
  if (period === "month") {
    const [y, m] = p.label.split("-");
    const name = new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
    return name.charAt(0).toUpperCase() + name.slice(1);
  }
  return p.label;
}

function syncPerfControls(data) {
  for (const b of document.querySelectorAll("[data-period]")) b.classList.toggle("active", b.dataset.period === perf.period);
  for (const b of document.querySelectorAll("[data-preset]")) b.classList.toggle("active", b.dataset.preset === perf.preset);
  for (const b of document.querySelectorAll("[data-perf-chart]")) b.classList.toggle("active", b.dataset.perfChart === perf.chart);
  if (data) boundDates($("perf-start"), $("perf-end"), data.bounds);
  $("perf-start").value = data?.start ?? perf.start ?? "";
  $("perf-end").value = data?.end ?? perf.end ?? "";
}

async function refreshPerformance() {
  // Période gardée à l'envers (ancien réglage) : on repart de « Depuis le début ».
  if (!perf.preset && perf.start && perf.end && perf.start > perf.end) Object.assign(perf, { preset: "all", start: null, end: null });
  const params = new URLSearchParams({ period: perf.period });
  const start = perf.preset ? presetStart(perf.preset) : perf.start;
  if (start) params.set("start", start);
  if (!perf.preset && perf.end) params.set("end", perf.end);
  addExclude(params);
  $("perf-csv").href = `/api/performance.csv?${params}`; // même découpage, mêmes dates
  const data = await api(`/api/performance?${params}`);
  // Dates choisies à la main : on garde celles que le serveur a retenues, ramenées dans la
  // période qui a des données (D-148), pour ne pas redemander une date sans données.
  if (!perf.preset && data.start && (data.start !== perf.start || data.end !== perf.end)) {
    Object.assign(perf, { start: data.start, end: data.end });
    savePerf();
  }
  renderPerformance(data);
}

// Légende des barres : les gains (vert) et les pertes (rouge) ont chacun leur pastille ;
// dans « Apports et gains/pertes », « Apports » s'y ajoute et un clic masque les barres du marché.
function perfLegend(withFlows) {
  return {
    position: "top",
    align: "end",
    onClick: withFlows
      ? (e, item, legend) => legend.chart.setDatasetVisibility(item.datasetIndex, !legend.chart.isDatasetVisible(item.datasetIndex)) || legend.chart.update()
      : () => {},
    labels: {
      padding: 14,
      boxWidth: 8,
      boxHeight: 8,
      generateLabels: (chart) => {
        const base = Chart.defaults.plugins.legend.labels.generateLabels(chart);
        const swatch = (text, token, datasetIndex) => {
          const l = { ...(base[datasetIndex] ?? base[0]), text, fillStyle: css(token), strokeStyle: css(token), datasetIndex };
          if (!withFlows) l.hidden = false;
          return l;
        };
        const market = withFlows ? 1 : 0;
        const entries = [swatch("Gains", "--ok", market), swatch("Pertes", "--ko", market)];
        return withFlows ? [base[0], ...entries] : entries;
      },
    },
  };
}

function renderPerformance(data) {
  lastPerformance = data;
  syncPerfControls(data);
  renderFilterButton();
  const s = data.summary;
  const tone = (v) => (v && v.startsWith("-") ? "ko" : "ok");
  const tiles = [];
  if (s) {
    tiles.push(kpi("Gain sur la période", formatDecimal(signed(s.gain), "EUR"), `de ${formatDecimal(s.start_value, "EUR")} à ${formatDecimal(s.end_value, "EUR")}`, tone(s.gain)));
    tiles.push(kpi("Performance", formatPct(s.performance), s.annualized ? `soit ${formatPct(s.annualized)} par an` : "pondérée par le temps", tone(s.performance)));
    if (s.irr) tiles.push(kpi("Rendement personnel", `${formatPct(s.irr)} / an`, "TRI : tient compte de tes dates de versement", tone(s.irr)));
    tiles.push(kpi("Apports nets", formatDecimal(s.flows, "EUR"), "achats moins ventes"));
    if (s.income !== "0.00") tiles.push(kpi("Dividendes − frais", formatDecimal(signed(s.income), "EUR"), "compris dans le gain et la performance", tone(s.income)));
    if (s.volatility) tiles.push(kpi("Volatilité", `${formatDecimal(s.volatility)} %`, "par an : ampleur des secousses"));
    if (s.risk_ratio) tiles.push(kpi("Rendement / risque", formatDecimal(s.risk_ratio), "performance annuelle ÷ volatilité"));
    tiles.push(kpi("Baisse max", formatPct(s.max_drawdown), "depuis un plus haut", s.max_drawdown.startsWith("-") ? "ko" : ""));
    tiles.push(kpi("Meilleure période", formatPct(s.best.performance), periodLabel(data.periods.find((p) => p.label === s.best.label), perf.period), "ok"));
    tiles.push(kpi("Pire période", formatPct(s.worst.performance), periodLabel(data.periods.find((p) => p.label === s.worst.label), perf.period), tone(s.worst.performance)));
    tiles.push(kpi("Périodes en hausse", `${s.positive_periods} / ${s.count}`));
  }
  $("perf-kpis").replaceChildren(...tiles);
  $("perf-status").textContent = s ? "" : "Aucune opération sur cette période.";

  const rows = data.periods;
  const labels = rows.map((p) => periodLabel(p, perf.period, true));
  const title = (items) => periodLabel(rows[items[0].dataIndex], perf.period);
  const axes = (unit) => ({
    x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 12, maxRotation: 0 } },
    y: { stacked: true, border: { display: false }, ticks: { callback: unit } },
  });
  if (perf.chart === "split") {
    // D'où vient la variation : l'argent versé (apports) ou le marché (gain).
    drawChart("chart-performance", {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Apports", data: rows.map((p) => Number(p.flows)), backgroundColor: css("--series-1"), borderRadius: 3, maxBarThickness: 36 },
          { label: "Gain du marché", data: rows.map((p) => Number(p.gain)), backgroundColor: rows.map((p) => css(p.gain.startsWith("-") ? "--ko" : "--ok")), borderRadius: 3, maxBarThickness: 36 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: perfLegend(true),
          tooltip: {
            callbacks: {
              title,
              label: (ctx) => {
                const p = rows[ctx.dataIndex];
                return ` ${ctx.dataset.label} : ${formatDecimal(signed(ctx.datasetIndex ? p.gain : p.flows), "EUR")}`;
              },
            },
          },
        },
        scales: axes((v) => euros.format(v)),
      },
    });
  } else {
    drawChart("chart-performance", {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Performance",
          data: rows.map((p) => Number(p.performance)),
          backgroundColor: rows.map((p) => css(p.performance.startsWith("-") ? "--ko" : "--ok")),
          borderRadius: 3,
          maxBarThickness: 36,
        }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: perfLegend(false),
          tooltip: {
            callbacks: {
              title,
              label: (ctx) => {
                const p = rows[ctx.dataIndex];
                return ` ${formatPct(p.performance)} · ${formatDecimal(signed(p.gain), "EUR")}`;
              },
              afterLabel: (ctx) => ` Cumul : ${formatPct(rows[ctx.dataIndex].cumulative_performance)}`,
            },
          },
        },
        scales: axes((v) => `${v.toLocaleString("fr-FR")} %`),
      },
    });
  }

  renderPerfTable(rows);
}

// Tri du tableau : par défaut la période la plus récente en haut, comme un relevé.
// Un clic sur un en-tête trie par cette colonne, un second clic inverse l'ordre.
function sortedPeriods(rows) {
  const { key, dir } = perf.sort;
  const value = (p) => (key === "period" ? p.first : Number(p[key])); // Number : pour comparer, pas pour calculer
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    const order = va < vb ? -1 : va > vb ? 1 : 0;
    return order * sign || a.first.localeCompare(b.first) * sign;
  });
}

function renderPerfTable(rows) {
  for (const th of document.querySelectorAll("#perf-table th[data-sort]")) {
    if (th.dataset.sort === perf.sort.key) th.setAttribute("aria-sort", perf.sort.dir === "asc" ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  }
  $("perf-rows").replaceChildren(
    ...sortedPeriods(rows).map((p) => {
      const tr = document.createElement("tr");
      tr.append(
        cell(periodLabel(p, perf.period)),
        cell(formatDecimal(p.start_value, "EUR"), "num"),
        cell(p.flows === "0.00" ? "–" : formatDecimal(p.flows, "EUR"), "num"),
        cell(formatDecimal(p.end_value, "EUR"), "num"),
        cell(formatDecimal(signed(p.gain), "EUR"), signClass(p.gain)),
        cell(formatPct(p.performance), signClass(p.performance)),
        cell(formatDecimal(signed(p.cumulative_gain), "EUR"), signClass(p.cumulative_gain)),
        cell(formatPct(p.cumulative_performance), signClass(p.cumulative_performance)),
      );
      return tr;
    }),
  );
}
