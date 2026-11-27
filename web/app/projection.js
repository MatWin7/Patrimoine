// --- Comparaison à un indice ou à un livret --------------------------------------------

const BENCH_KEY = PREF + "benchmark";
const bench = loadPref(BENCH_KEY, { choice: null, custom: "", rate: "", start: null, end: null });

function syncBenchControls() {
  const select = $("bench-select");
  // Ancien réglage (ticker seul) : retrouvé dans la liste s'il y est.
  if (!bench.choice && bench.ticker) {
    const known = benchList.find((b) => b.ticker === bench.ticker);
    bench.choice = known ? `id:${known.id}` : null;
  }
  select.value = bench.choice ?? "";
  if (!select.value) select.selectedIndex = 0;
  bench.choice = select.value;
  $("bench-custom-label").hidden = bench.choice !== "custom";
  $("bench-rate-label").hidden = bench.choice !== "livret";
  $("bench-save").hidden = !["custom", "livret"].includes(bench.choice) || !referenceParams(bench.choice, benchCustom());
  $("bench-custom").value = bench.custom;
  $("bench-rate").value = bench.rate;
}

let lastBenchDates = null; // dates de la dernière comparaison affichée, donc valides (D-148)
const benchCustom = () => ({ ticker: bench.custom, rate: bench.rate });

async function refreshBenchmark() {
  if (!benchList.length) await loadBenchList();
  syncBenchControls();
  const ref = referenceParams(bench.choice, benchCustom());
  if (!ref) {
    $("bench-status").textContent = bench.choice === "livret" ? "Saisis le taux annuel du livret (ex. 1,7)." : "Saisis un ticker Yahoo (ex. ^N225 pour le Nikkei).";
    return;
  }
  const params = addExclude(new URLSearchParams(ref));
  renderFilterButton();
  if (bench.start && bench.end && bench.start > bench.end) Object.assign(bench, { start: null, end: null }); // ancien réglage à l'envers
  if (bench.start) params.set("start", bench.start);
  if (bench.end) params.set("end", bench.end);
  $("bench-status").textContent = "Chargement…";
  let data;
  try {
    data = await api(`/api/benchmark?${params}`);
  } catch (err) {
    $("bench-status").textContent = `Comparaison indisponible : ${err.message}`;
    $("bench-kpis").replaceChildren();
    return;
  }
  lastBenchDates = { start: data.start, end: data.end };
  // Date choisie ramenée par le serveur dans la période qui a des données : on garde la sienne.
  if (data.start && ((bench.start && bench.start !== data.start) || (bench.end && bench.end !== data.end))) {
    if (bench.start) bench.start = data.start;
    if (bench.end) bench.end = data.end;
    savePref(BENCH_KEY, bench);
  }
  boundDates($("bench-start"), $("bench-end"), data.bounds);
  $("bench-start").value = data.start ?? "";
  $("bench-end").value = data.end ?? "";
  const s = data.summary;
  const label = referenceName(bench.choice, benchCustom());
  const isLivret = data.rate !== null && data.rate !== undefined;
  const tone = (v) => (v && v.startsWith("-") ? "ko" : "ok");
  const tiles = [];
  if (s) {
    tiles.push(kpi("Ton portefeuille", formatPct(s.portfolio_performance), "performance sur la période", tone(s.portfolio_performance)));
    tiles.push(kpi(label, formatPct(s.benchmark_performance), isLivret ? "livret, intérêts capitalisés" : `indice (${data.ticker})`, tone(s.benchmark_performance)));
    if (s.value_difference) tiles.push(kpi("Écart", formatPct(s.value_difference), benchVerdict(s, label), tone(s.value_difference)));
    if (s.portfolio_value) tiles.push(kpi("Valeur réelle", formatDecimal(s.portfolio_value, "EUR")));
    if (s.benchmark_value) tiles.push(kpi(isLivret ? "Si placé sur le livret" : "Si placé dans l'indice", formatDecimal(s.benchmark_value, "EUR"), "mêmes versements, mêmes dates"));
  }
  $("bench-kpis").replaceChildren(...tiles);
  const [benchLine, benchDetail] = isLivret
    ? ["Livret à taux fixe, calculé sur ta machine.", "Intérêts capitalisés chaque jour (très proche du calcul par quinzaine). Un livret réglementé est net d'impôt, ton PEA ne l'est qu'après 5 ans."]
    : data.currency !== "EUR"
      ? [`Indice coté en ${data.currency}, converti au taux du jour : effet de change passé non compté.`, ""]
      : ["Un indice « prix » ne compte pas les dividendes, ta performance si : l'écart te favorise un peu.", ""];
  $("bench-status").replaceChildren(benchLine, ...(benchDetail ? [" ", makeTip(benchDetail)] : []));
  const points = data.points;
  drawChart("chart-benchmark", {
    type: "line",
    data: {
      labels: points.map((p) => frDate(p.date)),
      datasets: [
        { label: "Ton portefeuille", data: points.map((p) => Number(p.portfolio)), borderColor: css("--series-1"), backgroundColor: css("--series-1") },
        { label, data: points.map((p) => Number(p.benchmark)), borderColor: css("--series-2"), backgroundColor: css("--series-2") },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2 } },
      plugins: {
        legend: lineLegend,
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const p = points[ctx.dataIndex];
              return ` ${ctx.dataset.label} : ${formatDecimal(ctx.datasetIndex ? p.benchmark : p.portfolio, "EUR")}`;
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: isNarrow() ? 3 : 8, maxRotation: 0 } },
        y: { border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
      },
    },
  });
}

// Phrase sous « Écart » : qui fait mieux, et de combien de points de performance.
function benchVerdict(s, label) {
  const pts = s.difference ? ` (${formatDecimal(signed(s.difference))} pts de perf.)` : "";
  if (/^-?0(\.0+)?$/.test(s.value_difference)) return `à égalité avec ${label}`;
  return s.value_difference.startsWith("-")
    ? `${label} fait mieux que toi${pts}`
    : `tu fais mieux que ${label}${pts}`;
}

function changeBench(update) {
  Object.assign(bench, update);
  savePref(BENCH_KEY, bench);
  refreshBenchmark();
}

$("bench-select").addEventListener("change", (e) => changeBench({ choice: e.target.value }));
$("bench-custom").addEventListener("change", (e) => changeBench({ custom: e.target.value }));
$("bench-rate").addEventListener("change", (e) => changeBench({ rate: e.target.value }));
watchDates($("bench-start"), $("bench-end"), () => lastBenchDates, changeBench);
// « Ajouter à la liste » : le ticker ou le livret saisi rejoint la liste, puis y est choisi.
$("bench-save").addEventListener("click", async () => {
  const livret = bench.choice === "livret";
  const label = await askText("Nom dans la liste", { value: livret ? "Livret A" : bench.custom.trim().toUpperCase(), ok: "Ajouter" });
  if (!label?.trim()) return;
  const body = livret
    ? { kind: "livret", label: label.trim(), grp: "Livrets", rate: cleanDecimal(bench.rate) }
    : { kind: "indice", label: label.trim(), grp: "Mes ajouts", ticker: bench.custom.trim() };
  try {
    const saved = await saveBench(body);
    await loadBenchList();
    showMessage(`« ${label.trim()} » ajouté à la liste.`);
    changeBench({ choice: `id:${saved.id}` });
  } catch (err) {
    showMessage(err.message, true);
  }
});

// --- Projection vers un objectif ----------------------------------------------------

const PROJ_KEY = PREF + "projection";
const projForm = $("form-projection");
const SCENARIOS = [
  { name: "Prudent", color: "--series-2" },
  { name: "Médian", color: "--series-1" },
  { name: "Optimiste", color: "--series-3" },
];
for (const [key, value] of Object.entries(loadPref(PROJ_KEY, {}))) {
  if (projForm.elements[key]) projForm.elements[key].value = value;
}

async function refreshProjection() {
  const f = Object.fromEntries(new FormData(projForm));
  savePref(PROJ_KEY, f);
  const rates = [f.r1, f.r2, f.r3].map((r) => cleanDecimal(r) ?? "0");
  const params = new URLSearchParams({ monthly: cleanDecimal(f.monthly) ?? "0", years: f.years, rates: rates.join(";") });
  addExclude(params);
  renderFilterButton();
  if (cleanDecimal(f.target)) params.set("target", cleanDecimal(f.target));
  let data;
  try {
    data = await api(`/api/projection?${params}`);
  } catch (err) {
    showMessage(`Projection : ${err.message}`, true);
    return;
  }
  const monthLabel = (m) => m.split("-").reverse().join("/"); // 2031-03 -> 03/2031
  const tiles = [kpi("Point de départ", formatDecimal(data.start_value, "EUR"), "titres et espèces aujourd'hui")];
  data.scenarios.forEach((sc, i) => {
    const reach = data.target ? (sc.reach ? `objectif atteint en ${monthLabel(sc.reach)}` : "objectif non atteint") : `à ${formatDecimal(sc.rate)} % par an`;
    tiles.push(kpi(`${SCENARIOS[i].name} (${formatDecimal(sc.rate)} %)`, formatDecimal(sc.final, "EUR"), reach));
  });
  tiles.push(kpi("Total versé", formatDecimal(data.invested.at(-1), "EUR"), "départ + versements"));
  $("proj-kpis").replaceChildren(...tiles);

  const datasets = data.scenarios.map((sc, i) => ({
    label: `${SCENARIOS[i].name} (${formatDecimal(sc.rate)} %)`,
    data: sc.values.map(Number),
    borderColor: css(SCENARIOS[i].color),
    backgroundColor: css(SCENARIOS[i].color),
  }));
  datasets.push({ label: "Versé", data: data.invested.map(Number), borderColor: css("--neutral"), backgroundColor: css("--neutral"), borderDash: [6, 4] });
  if (data.target) {
    // Objectif : ambre, tirets longs et 2 px pour ressortir sur les trois scénarios.
    datasets.push({ label: "Objectif", data: data.months.map(() => Number(data.target)), borderColor: css("--warn"), backgroundColor: css("--warn"), borderDash: [10, 5], borderWidth: 2 });
  }
  const values = [...data.scenarios.map((sc) => sc.values), data.invested];
  drawChart("chart-projection", {
    type: "line",
    data: { labels: data.months.map(monthLabel), datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2 } },
      plugins: {
        legend: lineLegend,
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const exact = values[ctx.datasetIndex]?.[ctx.dataIndex] ?? data.target;
              return ` ${ctx.dataset.label} : ${formatDecimal(exact, "EUR")}`;
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: isNarrow() ? 3 : 10, maxRotation: 0 } },
        y: { border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
      },
    },
  });
}

let projTimer = null;
projForm.addEventListener("input", () => {
  clearTimeout(projTimer); // on attend la fin de la frappe avant de recalculer
  projTimer = setTimeout(refreshProjection, 400);
});
projForm.addEventListener("submit", (e) => e.preventDefault());
