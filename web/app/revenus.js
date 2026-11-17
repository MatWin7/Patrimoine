// --- Revenus et frais (D-057, D-060) -------------------------------------------------------

// Quantité de jetons lisible : 2 décimales dès 1 jeton, 6 en dessous (« 19,93 », « 0,012 »).
const qtyText = (v) => formatDecimal(roundDecimal(v, Math.abs(Number(v)) >= 1 ? 2 : 6));
const pctText = (v) => (v === null || v === undefined ? "–" : `${formatDecimal(v)} %`);

function tableRows(tbodyId, rows, empty) {
  $(tbodyId).replaceChildren(...(rows.length ? rows : [emptyRow(empty)]));
}

function rowOf(...cells) {
  const tr = document.createElement("tr");
  tr.append(...cells);
  return tr;
}

// Couleurs des lignes du graphique des dividendes : celle de la ligne dans le reste de l'app
// (D-051, D-052) si elle est libre ici, sinon une des 7 couleurs encore libres ; au-delà, gris.
function dividendColors(names) {
  const own = lineColors();
  const out = new Map();
  for (const name of names) {
    const c = own.get(name);
    if (c && ![...out.values()].includes(c)) out.set(name, c);
  }
  const free = SERIES.filter((c) => ![...out.values()].includes(c));
  for (const name of names) if (!out.has(name)) out.set(name, free.shift() ?? null);
  return out;
}

const monthLabel = (m) => `${MONTHS[Number(m.slice(5)) - 1]} ${m.slice(2, 4)}`;

async function refreshIncome() {
  const data = await api("/api/income");
  const d = data.dividends;
  const change = d.change_pct === null ? "pas de dividende les 12 mois d'avant" : `${formatPct(d.change_pct)} par rapport aux 12 mois d'avant`;
  $("income-kpis").replaceChildren(
    kpi("12 derniers mois", formatDecimal(d.last_12m, "EUR"), change),
    kpi("Revenu mensuel moyen", formatDecimal(d.monthly, "EUR"), "12 derniers mois ÷ 12"),
    kpi("Rendement du portefeuille", pctText(d.yield_value_pct), `sur la valeur actuelle des titres ; ${pctText(d.yield_cost_pct)} sur l'argent investi`),
    kpi("Total reçu", formatDecimal(d.total, "EUR"), d.withheld !== "0.00" ? `après ${formatDecimal(d.withheld, "EUR")} d'impôt retenu à la source` : "depuis le début"),
  );
  $("income-fees-total").textContent = `Frais payés : ${formatDecimal(data.fees_total, "EUR")} au total (courtage, droits de garde et frais saisis)`;

  // Barres empilées : une tranche par ligne (les 7 qui ont le plus versé sur la période),
  // le reste dans « Autres » ; l'infobulle détaille toutes les lignes du mois.
  const totals = new Map();
  for (const m of data.months) for (const l of m.lines) totals.set(l.asset, (totals.get(l.asset) ?? 0) + Number(l.amount));
  const ranked = [...totals.keys()].sort((a, b) => totals.get(b) - totals.get(a));
  const shown = ranked.length > SERIES.length + 1 ? ranked.slice(0, SERIES.length) : ranked;
  const colors = dividendColors(shown);
  const amountOf = (m, name) => Number(m.lines.find((l) => l.asset === name)?.amount ?? 0);
  const others = (m) => m.lines.filter((l) => !shown.includes(l.asset));
  const datasets = shown.map((name) => ({
    label: name,
    data: data.months.map((m) => amountOf(m, name)),
    backgroundColor: colors.get(name) ? css(colors.get(name)) : otherColor(),
    maxBarThickness: 28,
  }));
  if (shown.length < ranked.length) {
    datasets.push({
      label: "Autres",
      data: data.months.map((m) => others(m).reduce((t, l) => t + Number(l.amount), 0)),
      backgroundColor: otherColor(),
      maxBarThickness: 28,
    });
  }
  drawChart("chart-income", {
    type: "bar",
    data: { labels: data.months.map((m) => monthLabel(m.month)), datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (item) => item.raw > 0,
          itemSort: (a, b) => (a.dataset.label === "Autres") - (b.dataset.label === "Autres") || b.raw - a.raw,
          callbacks: {
            // Mois sans dividende : aucune tranche retenue par `filter`, donc pas de titre.
            // (Lire items[0] ici levait une erreur qui figeait les graphiques de la page.)
            title: (items) => {
              const m = items.length ? data.months[items[0].dataIndex] : null;
              return m ? `${monthLabel(m.month)} · ${formatDecimal(m.amount, "EUR")}` : "";
            },
            label: (ctx) => {
              const m = data.months[ctx.dataIndex];
              if (ctx.dataset.label !== "Autres") {
                const line = m.lines.find((l) => l.asset === ctx.dataset.label);
                return line ? ` ${line.asset} : ${formatDecimal(line.amount, "EUR")}` : "";
              }
              return [" Autres :", ...others(m).map((l) => `   ${l.asset} : ${formatDecimal(l.amount, "EUR")}`)];
            },
          },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
      },
    },
  });
  fillSortable("income-lines-table", data.lines, (l) => rowOf(
    clipCell(l.asset, "clip"),
    cell(formatDecimal(l.total, "EUR"), "num"),
    cell(formatDecimal(l.last_12m, "EUR"), "num"),
    cell(pctText(l.yield_pct), "num"),
    cell(l.withheld === "0.00" ? "–" : formatDecimal(l.withheld, "EUR"), "num"),
  ), "Aucun dividende saisi.");
  fillSortable("income-years-table", data.years, (y) => rowOf(
    cell(String(y.year)),
    cell(formatDecimal(y.dividends, "EUR"), "num"),
    cell(formatDecimal(y.fees, "EUR"), "num"),
  ), "Rien pour l'instant.");
  renderDividendCalendar(data.calendar);
  renderTer(data.ter);
}
