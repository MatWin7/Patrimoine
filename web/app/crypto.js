// --- Onglet Rendements crypto : dépôts Aave (D-095) ------------------------------------------------------
// Intérêts estimés par le serveur avec le taux public du marché ; « Vérifier » recale avec le
// solde lu dans Aave. Seuls chaîne, marché et jeton partent vers Aave (règle 3 élargie).
let aaveLines = [];
let aaveReserves = [];
let aaveCheckLine = null;

async function refreshAave() {
  try {
    aaveLines = await api("/api/aave/lines");
  } catch (err) {
    aaveLines = [];
    showMessage(`Taux Aave indisponibles : ${err.message}`, true);
  }
  renderAave();
}

// Notes des lignes Aave, puis le tableau des placements (il attend aussi /api/crypto/rewards).
function renderAave() {
  const notes = [];
  for (const l of aaveLines) {
    const name = `${l.account} · ${l.symbol}`;
    if (l.warning) notes.push(`${name} : ${l.warning}.`);
    if (l.extrapolated) notes.push(`${name} : avant le ${frDate(l.first_rate)}, taux le plus ancien connu appliqué.`);
    if (l.unexplained) notes.push(`${name} : solde vérifié inférieur aux dépôts de ${formatDecimal(l.unexplained)} ${l.symbol}, il manque sans doute un retrait.`);
  }
  $("aave-note").textContent = notes.join(" ");
  $("aave-note").hidden = !notes.length;
  renderPlacements();
}

// Mes placements (D-106) : un par (compte, jeton) ; une ligne Aave garde ses boutons. Une ligne
// Aave dont le taux n'est pas encore connu (réseau) apparaît quand même, sans chiffres.
function renderPlacements() {
  if (!rewardsData) return;
  const byLine = new Map(aaveLines.map((l) => [l.id, l]));
  const rows = rewardsData.placements.map((p) => {
    const line = byLine.get(p.aave_line_id);
    byLine.delete(p.aave_line_id);
    return { ...p, line, label: `${p.account} · ${p.asset}`, current_value: line?.current_value ?? null };
  });
  for (const line of byLine.values()) {
    rows.push({
      line, label: `${line.account} · ${line.symbol}`, kinds: [], held: line.held, asset: line.symbol,
      capital: line.value, rate_today_pct: null, realized_pct: null, income_12m: null, estimated: null, current_value: line.current_value,
    });
  }
  fillSortable("placements-table", rows, placementRow, "Aucun placement : suis une ligne Aave, ou importe un relevé avec des intérêts Earn ou du staking.");
}

function placementRow(p) {
  const eur = (v) => (v === null || v === undefined ? "–" : formatDecimal(v, "EUR"));
  const l = p.line;
  const name = clipCell(p.label, "clip");
  const sub = document.createElement("div");
  sub.className = "muted small";
  sub.textContent = l ? `${l.chain} · ${l.market_name}` : p.kinds.map((k) => REWARD_KINDS[k]).join(", ");
  name.append(sub);
  const capital = cell(eur(p.capital), "num");
  capital.title = `${qtyText(p.held)} ${p.asset}`;
  const realized = cell(p.realized_pct === null ? "–" : `${pctText(p.realized_pct)}/an`, "num");
  if (p.received_12m) realized.title = `${qtyText(p.received_12m)} ${p.asset} reçus en 12 mois`;
  const estimated = cell(p.estimated === null ? "–" : `${formatDecimal(p.estimated, "EUR")}/an`, "num");
  const current = cell(eur(p.current_value), "num");
  if (l?.current) current.title = `≈ ${qtyText(l.current)} ${l.symbol}, estimé`;
  const actions = document.createElement("td");
  actions.className = "actions";
  if (l) {
    const button = (text, className, onClick, title) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = className;
      b.textContent = text;
      if (title) {
        b.title = title;
        b.setAttribute("aria-label", title);
      }
      b.addEventListener("click", onClick);
      return b;
    };
    const checked = l.checks[0] ? `Vérifié le ${frDate(l.checks[0].date)} : ${qtyText(l.checks[0].quantity)} ${l.symbol}` : "Jamais vérifié";
    actions.append(
      button("Vérifier", "link edit verify", () => openAaveCheck(l), checked),
      button("Déposer", "link edit", () => openAaveMove(l, "depot")),
      button("Retirer", "link", () => openAaveMove(l, "retrait")),
      button("✕", "link", () => stopAave(l), "Arrêter le suivi"),
    );
  }
  return rowOf(name, capital, cell(p.rate_today_pct === null ? "–" : pctText(p.rate_today_pct), "num"), realized, cell(eur(p.income_12m), "num"), estimated, current, actions);
}

async function aaveAction(promise, done) {
  try {
    const out = await promise;
    if (done) showMessage(typeof done === "function" ? done(out) : done);
    await refreshAave();
    await refreshRewards();
  } catch (err) {
    showMessage(err.message, true);
  }
}

async function stopAave(l) {
  if (!await askConfirm(`Arrêter le suivi de ${l.account} · ${l.symbol} ?`, { detail: "Ses intérêts estimés seront retirés (sauvegarde de la base avant).", ok: "Arrêter" })) return;
  aaveAction(api(`/api/aave/lines/${l.id}`, { method: "DELETE" }), "Suivi arrêté.");
}

function openAaveCheck(l) {
  aaveCheckLine = l;
  const form = $("form-aave-check");
  $("form-aave-line").hidden = $("form-aave-move").hidden = true;
  $("aave-check-label").textContent = `${l.account} · ${l.symbol}`;
  form.elements.check_date.value = new Date().toISOString().slice(0, 10);
  form.elements.quantity.value = "";
  form.hidden = false;
  form.elements.quantity.focus();
}

$("form-aave-check").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const body = { check_date: form.elements.check_date.value, quantity: form.elements.quantity.value.replace(",", ".").replace(/\s/g, "") };
  const symbol = aaveCheckLine.symbol;
  form.hidden = true;
  aaveAction(
    api(`/api/aave/lines/${aaveCheckLine.id}/checks`, { method: "POST", body: JSON.stringify(body) }),
    (out) => (out.gap === null ? "Solde enregistré." : `Écart avec l'estimation : ${formatDecimal(out.gap)} ${symbol}. Intérêts recalés.`),
  );
});

// Comptes et actifs pour les formulaires Aave (lus à l'ouverture, sans réseau).
let aaveLists = { accounts: [], assets: [] };
const decimalInput = (v) => v.replace(",", ".").replace(/\s/g, "");
const aaveToday = () => new Date().toISOString().slice(0, 10);

function fillOtherAccounts(select, excludeId, outside) {
  const items = [{ id: "", name: outside }, ...aaveLists.accounts.filter((a) => a.id !== excludeId)];
  fillSelect(select, items, (a) => a.name);
}

$("aave-new").addEventListener("click", async () => {
  const form = $("form-aave-line");
  $("form-aave-check").hidden = $("form-aave-move").hidden = true;
  form.hidden = !form.hidden;
  if (form.hidden) return;
  try {
    const [accounts, assets, chains] = await Promise.all([api("/api/accounts"), api("/api/assets"), api("/api/aave/chains")]);
    aaveLists = { accounts, assets: assets.filter((a) => a.asset_class === "crypto") };
    if (!aaveLists.assets.length) throw new Error("Crée d'abord l'actif crypto (ex. USDC) dans Gérer.");
    fillSelect(form.elements.chain_id, chains.map((c) => ({ id: c.chain_id, name: c.name })), (c) => c.name);
    fillSelect(form.elements.asset_id, aaveLists.assets, (a) => a.name);
    form.elements.op_date.value = aaveToday();
    form.elements.quantity.value = "";
    await loadAaveReserves();
  } catch (err) {
    form.hidden = true;
    showMessage(err.message, true);
  }
});

// Comptes crypto proposés, plus « Nouveau compte Aave <chaîne> » (créé à l'envoi).
function fillAaveAccounts() {
  const form = $("form-aave-line");
  const chain = form.elements.chain_id.selectedOptions[0]?.textContent ?? "";
  const crypto = aaveLists.accounts.filter((a) => a.kind === "crypto");
  const existing = crypto.find((a) => a.name === `Aave ${chain}`);
  const items = existing ? crypto : [{ id: "new", name: `Nouveau compte « Aave ${chain} »` }, ...crypto];
  fillSelect(form.elements.account, items, (a) => a.name);
  if (existing && !form.elements.account.dataset.touched) form.elements.account.value = existing.id;
  fillOtherAccounts(form.elements.other, Number(form.elements.account.value), "Hors de l'app");
}

async function loadAaveReserves() {
  const form = $("form-aave-line");
  aaveReserves = await api(`/api/aave/reserves?chain_id=${form.elements.chain_id.value}`);
  const items = aaveReserves
    .map((r, i) => ({ ...r, id: i }))
    .sort((a, b) => a.frozen - b.frozen || a.symbol.localeCompare(b.symbol));
  fillSelect(form.elements.reserve, items, (r) => `${r.symbol} · ${r.market_name} · ${pctText(r.apy_pct)}${r.frozen ? " (gelé)" : ""}`);
  fillAaveAccounts();
  matchAaveAsset();
}

// Jeton de l'app du même nom que celui du marché (ex. « USDC »), s'il existe.
function matchAaveAsset() {
  const form = $("form-aave-line");
  const r = aaveReserves[Number(form.elements.reserve.value)];
  const same = r && aaveLists.assets.find((a) => a.name.toUpperCase().split(/[\s(]/)[0] === r.symbol.toUpperCase());
  if (same) form.elements.asset_id.value = same.id;
}

$("form-aave-line").elements.chain_id.addEventListener("change", () => loadAaveReserves().catch((err) => showMessage(err.message, true)));
$("form-aave-line").elements.reserve.addEventListener("change", matchAaveAsset);
$("form-aave-line").elements.account.addEventListener("change", (e) => {
  e.target.dataset.touched = "1";
  fillOtherAccounts($("form-aave-line").elements.other, Number(e.target.value), "Hors de l'app");
});
for (const button of document.querySelectorAll("#form-aave-line [data-cancel], #form-aave-check [data-cancel], #form-aave-move [data-cancel]")) {
  button.addEventListener("click", () => { button.closest("form").hidden = true; });
}

function moveBody(form, direction) {
  const quantity = decimalInput(form.elements.quantity.value);
  if (!quantity) return null;
  return {
    direction,
    quantity,
    op_date: form.elements.op_date.value || aaveToday(),
    other_account_id: form.elements.other.value ? Number(form.elements.other.value) : null,
  };
}

$("form-aave-line").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const r = aaveReserves[Number(form.elements.reserve.value)];
  const deposit = moveBody(form, "depot");
  const create = async () => {
    const accountId = form.elements.account.value;
    const body = { account_id: accountId === "new" ? null : Number(accountId), asset_id: Number(form.elements.asset_id.value), chain_id: Number(form.elements.chain_id.value), market: r.market, token: r.token, deposit };
    return api("/api/aave/lines", { method: "POST", body: JSON.stringify(body) });
  };
  form.hidden = true;
  delete form.elements.account.dataset.touched;
  aaveAction(create(), `${r.symbol} suivi sur ${r.market_name}${deposit ? ", dépôt enregistré" : ""}.`).then(() => refresh().catch(() => {}));
});

// Dépôt ou retrait sur une ligne déjà suivie.
let aaveMove = null;
async function openAaveMove(l, direction) {
  const form = $("form-aave-move");
  $("form-aave-line").hidden = $("form-aave-check").hidden = true;
  try {
    aaveLists.accounts = await api("/api/accounts");
  } catch (err) {
    showMessage(err.message, true);
    return;
  }
  aaveMove = { line: l, direction };
  $("aave-move-label").textContent = `${l.account} · ${l.symbol}`;
  form.querySelector("[data-move-word]").textContent = direction === "depot" ? "Dépôt" : "Retrait";
  form.querySelector("[data-move-from]").textContent = direction === "depot" ? "Venant de" : "Vers";
  fillOtherAccounts(form.elements.other, l.account_id, "Hors de l'app");
  form.elements.op_date.value = aaveToday();
  form.elements.quantity.value = "";
  form.hidden = false;
  form.elements.quantity.focus();
}

$("form-aave-move").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const { line, direction } = aaveMove;
  const body = moveBody(form, direction);
  form.hidden = true;
  aaveAction(
    api(`/api/aave/lines/${line.id}/moves`, { method: "POST", body: JSON.stringify(body) }),
    direction === "depot" ? "Dépôt enregistré, intérêts recalculés." : "Retrait enregistré, intérêts recalculés.",
  ).then(() => refresh().catch(() => {}));
});

// --- Onglet Rendements crypto : revenus (D-092, D-106) -------------------------------------
const REWARD_KINDS = { earn: "Intérêts Earn", staking: "Staking", airdrop: "Airdrop", cashback: "Cashback", parrainage: "Parrainage", autre: "Autre ou non précisé" };
let rewardsData = null;
let rewardsBy = "asset";

async function refreshRewards() {
  rewardsData = await api("/api/crypto/rewards");
  const d = rewardsData;
  const o = d.overview;
  const gifts = Number(o.gifts_12m) ? `dont ${formatDecimal(o.gifts_12m, "EUR")} de cadeaux` : `${formatDecimal(o.total, "EUR")} depuis le début`;
  $("yield-kpis").replaceChildren(
    kpi("Revenus 12 mois", formatDecimal(o.income_12m, "EUR"), gifts),
    kpi("Rendement moyen", o.average_rate_pct === null ? "–" : `${pctText(o.average_rate_pct)}/an`, "pondéré par le capital"),
    o.borrowed
      ? kpi("Revenu net estimé", `${formatDecimal(o.net_estimated, "EUR")}/an`, `après ${formatDecimal(o.borrow_cost, "EUR")} d'intérêts d'emprunt`)
      : kpi("Revenu estimé", `${formatDecimal(o.estimated, "EUR")}/an`, "au taux actuel"),
    kpi("Capital placé", formatDecimal(o.placed, "EUR"), o.placed_share_pct === null ? "" : `${pctText(o.placed_share_pct)} de tes cryptos`),
  );
  if (o.health_factor !== null) {
    const weak = Number(o.health_factor) < HEALTH_WARNING;
    $("yield-kpis").append(kpi("Facteur de santé", formatDecimal(o.health_factor), weak ? "proche de la liquidation (sous 1)" : "liquidation sous 1", weak ? "ko" : ""));
  }
  renderPlacements();
  renderRewardsChart();
  const all = d.placements.reduce((t, p) => t + Number(p.capital ?? 0), 0) + d.idle.reduce((t, i) => t + Number(i.value), 0);
  const idle = d.idle.map((i) => ({ ...i, share_pct: all ? (Number(i.value) / all) * 100 : null }));
  fillSortable("idle-table", idle, (i) => rowOf(
    clipCell(i.account, "clip"),
    clipCell(i.asset, "clip"),
    cell(qtyText(i.held), "num"),
    cell(formatDecimal(i.value, "EUR"), "num"),
    cell(i.share_pct === null ? "–" : pctText(i.share_pct.toFixed(2)), "num"),
  ), "Toutes tes cryptos rapportent.");
  const years = d.fees.years.map((y) => `${y.year} : ${formatDecimal(y.fees, "EUR")}`).join(" · ");
  const other = d.fees.other !== "0.00" ? ` · frais de compte : ${formatDecimal(d.fees.other, "EUR")}` : "";
  const total = $("crypto-fees-total");
  total.textContent = d.fees.total === "0.00" ? "" : `${formatDecimal(d.fees.total, "EUR")} de frais au total`;
  total.title = years + other;
  const recent = d.recent.map((r) => ({ ...r, label: REWARD_KINDS[r.kind] }));
  fillSortable("rewards-recent-table", recent, (r) => rowOf(
    cell(frDate(r.date)),
    clipCell(r.account, "clip"),
    clipCell(r.asset, "clip"),
    cell(r.label),
    cell(qtyText(r.quantity), "num"),
    cell(r.value === null ? "–" : formatDecimal(r.value, "EUR"), "num"),
  ), "Rien pour l'instant.");
}

// Barres empilées des 24 derniers mois, par jeton (les 7 plus gros + « Autres ») ou par type ;
// les mois vides avant la première récompense sont retirés (12 mois affichés au moins).
function renderRewardsChart() {
  const first = rewardsData.months.findIndex((m) => Object.keys(m.by_asset).length);
  const from = Math.min(first < 0 ? Infinity : first, rewardsData.months.length - 12);
  const d = { ...rewardsData, months: rewardsData.months.slice(Math.max(0, from)) };
  const byAsset = rewardsBy === "asset";
  const names = byAsset ? Object.fromEntries(d.assets.map((a) => [String(a.asset_id), a.asset])) : REWARD_KINDS;
  const valuesOf = (m) => (byAsset ? m.by_asset : m.by_kind);
  const totals = new Map();
  for (const m of d.months) for (const [k, v] of Object.entries(valuesOf(m))) totals.set(k, (totals.get(k) ?? 0) + Number(v));
  const ranked = [...totals.keys()].sort((a, b) => totals.get(b) - totals.get(a));
  const shown = ranked.length > SERIES.length + 1 ? ranked.slice(0, SERIES.length) : ranked;
  const colors = byAsset ? dividendColors(shown.map((k) => names[k])) : new Map(shown.map((k, i) => [names[k], SERIES[i]]));
  const datasets = shown.map((k) => ({
    label: names[k],
    data: d.months.map((m) => Number(valuesOf(m)[k] ?? 0)),
    backgroundColor: colors.get(names[k]) ? css(colors.get(names[k])) : otherColor(),
    maxBarThickness: 28,
  }));
  if (shown.length < ranked.length) {
    datasets.push({
      label: "Autres",
      data: d.months.map((m) => Object.entries(valuesOf(m)).filter(([k]) => !shown.includes(k)).reduce((t, [, v]) => t + Number(v), 0)),
      backgroundColor: otherColor(),
      maxBarThickness: 28,
    });
  }
  drawChart("chart-rewards", {
    type: "bar",
    data: { labels: d.months.map((m) => monthLabel(m.month)), datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (item) => item.raw > 0,
          itemSort: (a, b) => b.raw - a.raw,
          callbacks: { label: (ctx) => ` ${ctx.dataset.label} : ${euros.format(ctx.raw)}` },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
      },
    },
  });
}

for (const button of $("rewards-by").querySelectorAll("button")) {
  button.addEventListener("click", () => {
    rewardsBy = button.dataset.by;
    for (const b of $("rewards-by").querySelectorAll("button")) b.classList.toggle("active", b === button);
    if (rewardsData) renderRewardsChart();
  });
}

// Calendrier des dividendes : une ligne par actif, une case par mois des 12 derniers, d'autant
// plus foncée que le montant est élevé par rapport au plus gros versement du tableau.
function renderDividendCalendar(cal) {
  const heads = $("income-calendar-table").tHead.rows[0].cells;
  cal.months.forEach((m, i) => { heads[i + 1].querySelector("button").textContent = monthLabel(m); });
  const max = Math.max(0, ...cal.rows.flatMap((r) => r.cells.map((c) => Number(c ?? 0))));
  const rows = cal.rows.map((r) => ({ ...r, ...Object.fromEntries(r.cells.map((c, i) => [`m${i}`, c])) }));
  fillSortable("income-calendar-table", rows, (r) => rowOf(
    clipCell(r.asset, "clip"),
    ...r.cells.map((c) => {
      const td = cell(c === null ? "" : formatDecimal(c, "EUR"), "num heat");
      if (c !== null && max > 0) td.style.setProperty("--heat", `${Math.round(10 + 45 * Number(c) / max)}%`);
      return td;
    }),
    cell(formatDecimal(r.total, "EUR"), "num"),
  ), "Aucun dividende ces 12 derniers mois.");
}

// Frais courants : un bloc par compte (titre et sous-total), lignes triées par en-tête dans
// chaque bloc ; les comptes restent dans l'ordre choisi dans « Gérer » (D-070).
function renderTer(t) {
  $("ter-years-head").querySelector("button").textContent = `Manque à gagner sur ${t.years} ans`;
  $("ter-kpis").replaceChildren(
    kpi("Frais courants sur un an", formatDecimal(t.first_year, "EUR")),
    kpi(`Manque à gagner sur ${t.years} ans`, formatDecimal(t.over_years, "EUR")),
  );
  const tableId = "ter-lines-table";
  const toRow = (l) => rowOf(
    clipCell(l.asset, "clip"),
    cell(formatDecimal(l.value, "EUR"), "num"),
    cell(`${formatDecimal(l.ter)} %/an`, "num"),
    cell(formatDecimal(l.first_year, "EUR"), "num"),
    cell(formatDecimal(l.over_years, "EUR"), "num ko"),
  );
  const render = () => {
    const out = [];
    for (const a of t.accounts) {
      const head = rowOf(
        cell(a.account),
        cell(formatDecimal(a.value, "EUR"), "num"),
        cell(""),
        cell(formatDecimal(a.first_year, "EUR"), "num"),
        cell(formatDecimal(a.over_years, "EUR"), "num ko"),
      );
      head.className = "group-row";
      out.push(head, ...sortRows(tableId, a.lines, sortNumeric[tableId]).map(toRow));
    }
    $(tableId).tBodies[0].replaceChildren(...(out.length ? out : [emptyRow("Aucun TER saisi : ajoute-le dans « Gérer » > Actifs > Modifier (il figure dans le document d'information du fonds).")]));
  };
  render();
  rerenders[tableId] = render;
  const missing = t.without_ter.length ? ` Sans TER saisi : ${t.without_ter.join(", ")}.` : "";
  $("ter-note").replaceChildren(
    `Manque à gagner sur ${t.years} ans, si le fonds rapporte ${formatDecimal(t.growth_pct)} %/an avant frais.`,
    " ",
    makeTip(`Les frais courants sont prélevés chaque jour dans le prix du fonds : tu ne les vois jamais passer. Manque à gagner = valeur dans ${t.years} ans sans frais − avec frais, sans nouveau versement. Un fonds détenu sur deux comptes apparaît dans chacun.`),
    missing,
  );
}
