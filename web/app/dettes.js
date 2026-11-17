// --- Dettes (D-107) ------------------------------------------------------------------------
// Capital restant dû calculé par le serveur (mensualités constantes), recalé par un capital
// saisi ; Patrimoine net = patrimoine total − dettes, en tuile de la Vue d'ensemble.
let debtList = [];
let debtBalanceTarget = null;

let aaveDebts = [];
let healthAlerted = false;

async function refreshDebts() {
  const data = await api("/api/debts");
  debtList = data.debts;
  aaveDebts = data.aave;
  lists.debts = debtList;
  renderDebts();
  // Alerte une fois par session : un emprunt Aave proche de la liquidation (D-108).
  const weak = aaveDebts.filter((b) => b.health_factor !== null && Number(b.health_factor) < HEALTH_WARNING);
  if (weak.length && !healthAlerted) {
    healthAlerted = true;
    showMessage(`Aave : facteur de santé ${formatDecimal(weak[0].health_factor)} sur ${weak[0].chain}, sous ${formatDecimal("1.5")}. Voir Rendements crypto.`, true);
  }
}

function renderDebts() {
  const movable = debtList.length > 1;
  const rows = debtList.map((d) => {
    const tr = document.createElement("tr");
    tr.dataset.id = d.id;
    if (movable) tr.append(gripCell(d.name));
    const name = cell(d.name);
    const detail = document.createElement("span");
    detail.className = "muted small block";
    detail.textContent = [d.kind_label, d.institution].filter(Boolean).join(" · ");
    name.append(detail);
    const money = cell(formatDecimal(roundDecimal(d.balance), d.currency), "num");
    const sub = document.createElement("span");
    sub.className = "muted small block";
    sub.textContent = d.payment
      ? `${formatDecimal(roundDecimal(d.payment), d.currency)}/mois · fin ${monthLabel(d.end_date.slice(0, 7))}`
      : "sans échéancier";
    money.append(sub);
    const parts = [`Emprunté ${formatDecimal(roundDecimal(d.principal), d.currency)}`];
    if (d.repaid_pct !== null) parts.push(`remboursé à ${pctText(d.repaid_pct)}`);
    if (d.interest_left !== null) parts.push(`intérêts restants ≈ ${formatDecimal(roundDecimal(d.interest_left), d.currency)}`);
    money.title = parts.join(" · ");
    const actions = actionButtons(() => openDebt(d), () => deleteDebt(d));
    actions.prepend(smallButton("Capital", "Saisir le capital restant dû lu sur un relevé", () => openDebtBalance(d), "link edit verify"));
    tr.append(name, money, actions);
    return tr;
  });
  // Emprunts Aave (D-108) : rappelés en lecture, gérés dans Rendements crypto.
  for (const b of aaveDebts) {
    const tr = document.createElement("tr");
    if (movable) tr.append(document.createElement("td"));
    const name = cell(`Aave · ${b.symbol}`);
    const detail = document.createElement("span");
    detail.className = "muted small block";
    detail.textContent = `Emprunt Aave · ${b.chain}`;
    name.append(detail);
    const money = cell(b.value_eur === null ? `${qtyText(b.balance)} ${b.symbol}` : formatDecimal(roundDecimal(b.value_eur), "EUR"), "num");
    const sub = document.createElement("span");
    sub.className = "muted small block";
    sub.textContent = `taux ${pctText(b.apy_today_pct)}`;
    money.append(sub);
    const actions = document.createElement("td");
    actions.className = "actions";
    actions.append(smallButton("Voir", "Géré dans Rendements crypto", () => { location.hash = "#crypto"; }));
    tr.append(name, money, actions);
    rows.push(tr);
  }
  $("debts-list").replaceChildren(...(rows.length ? rows : [emptyRow("Aucune dette.")]));
}

function openDebt(debt = null) {
  const form = $("form-debt");
  form.dataset.id = debt?.id ?? "";
  fillForm(form, debt, { kind: "immobilier", currency: "EUR" });
  for (const name of ["principal", "rate_pct"]) form[name].value = form[name].value.replace(".", ",");
  $("dlg-debt-title").textContent = debt ? `Modifier « ${debt.name} »` : "Ajouter une dette";
  $("dlg-debt").showModal();
}

$("form-debt").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = {
    name: form.name.value,
    kind: form.kind.value,
    institution: form.institution.value.trim() || null,
    currency: form.currency.value,
    principal: cleanDecimal(form.principal.value),
    rate_pct: cleanDecimal(form.rate_pct.value),
    start_date: form.start_date.value,
    months: form.months.value ? Number(form.months.value) : null,
  };
  const id = form.dataset.id;
  try {
    await api(id ? `/api/debts/${id}` : "/api/debts", { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    $("dlg-debt").close();
    showMessage(id ? "Dette modifiée." : "Dette ajoutée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
});

async function deleteDebt(d) {
  if (!await askConfirm(`Supprimer « ${d.name} » ?`, { detail: "Les capitaux saisis partent avec elle (sauvegarde de la base avant).", ok: "Supprimer" })) return;
  try {
    await api(`/api/debts/${d.id}`, { method: "DELETE" });
    showMessage("Dette supprimée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

function openDebtBalance(d) {
  debtBalanceTarget = d;
  const form = $("form-debt-balance");
  form.balance.value = "";
  form.balance_date.value = new Date().toISOString().slice(0, 10);
  form.balance_date.min = d.start_date;
  $("dlg-debt-balance-title").textContent = `Capital restant dû · ${d.name}`;
  $("debt-balances").replaceChildren(...d.balances.map((b) => {
    const tr = document.createElement("tr");
    const del = smallButton("✕", "Retirer ce capital saisi", async () => {
      try {
        await api(`/api/debts/${d.id}/balances/${b.date}`, { method: "DELETE" });
        $("dlg-debt-balance").close();
        showMessage("Capital saisi retiré.");
        await refresh();
      } catch (err) {
        showMessage(err.message, true);
      }
    }, "link");
    const actions = document.createElement("td");
    actions.className = "actions";
    actions.append(del);
    tr.append(cell(frDate(b.date)), cell(formatDecimal(b.balance, d.currency), "num"), actions);
    return tr;
  }));
  $("dlg-debt-balance").showModal();
  form.balance.focus();
}

$("form-debt-balance").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = { balance_date: form.balance_date.value, balance: cleanDecimal(form.balance.value) };
  try {
    await api(`/api/debts/${debtBalanceTarget.id}/balances`, { method: "POST", body: JSON.stringify(body) });
    $("dlg-debt-balance").close();
    showMessage("Capital enregistré, calcul recalé.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
});

document.querySelector('[data-add="debt"]').addEventListener("click", () => openDebt());
makeReorderable($("debts-list"), "tr[data-id]", ".drag-handle", (rows, moved) => saveOrder("debts", rows, moved));

// Tuile « Patrimoine net » : patrimoine complet (hors filtre de lignes) − capital restant dû.
function netWorthTile(portfolio) {
  if (portfolio.debts_eur === null || portfolio.debts_eur === undefined) return null;
  const eur = portfolio.totals.EUR;
  if (!eur) return null;
  const net = Number(eur.total ?? eur.value) - Number(portfolio.debts_eur);
  const tile = kpi("Patrimoine net", formatDecimal(net.toFixed(2), "EUR"), `dettes ${formatDecimal(roundDecimal(portfolio.debts_eur), "EUR")}`);
  tile.title = "Patrimoine total, toutes lignes comprises, moins le capital restant dû des dettes (Gérer > Dettes)";
  return tile;
}

// --- Emprunts Aave et facteur de santé (D-108) ---------------------------------------------
// Dette estimée par le serveur au taux d'emprunt public, recalée par « Vérifier » ; facteur de
// santé par marché, rouge sous 1,5 (alerte au chargement, une fois par session).
const HEALTH_WARNING = 1.5;
let borrowData = { borrows: [], markets: [] };
let borrowReserves = [];
let borrowTarget = null;

async function refreshBorrows() {
  try {
    borrowData = await api("/api/aave/borrows");
  } catch (err) {
    showMessage(`Emprunts Aave indisponibles : ${err.message}`, true);
    return;
  }
  renderBorrows();
}

function healthCell(value) {
  const td = cell(value === null || value === undefined ? "–" : formatDecimal(value), "num");
  if (value !== null && value !== undefined && Number(value) < HEALTH_WARNING) td.classList.add("ko");
  return td;
}

function renderBorrows() {
  const rows = borrowData.borrows.map((b) => ({ ...b, label: `${b.chain} · ${b.symbol}` }));
  fillSortable("borrows-table", rows, borrowRow, "Aucun emprunt suivi.");
  const weak = borrowData.markets.filter((m) => m.warning);
  const partial = borrowData.markets.filter((m) => !m.complete);
  const notes = weak.map((m) => `Facteur de santé ${formatDecimal(m.health_factor)} sur ${m.chain} · ${m.market_name} : sous ${formatDecimal("1.5")}, rembourse ou dépose pour t'éloigner de la liquidation (sous 1).`);
  if (partial.length) notes.push("Cours ou seuil manquant sur un marché : facteur de santé incomplet.");
  $("borrow-warning").textContent = notes.join(" ");
  $("borrow-warning").hidden = !notes.length;
}

function borrowRow(b) {
  const name = clipCell(b.label, "clip");
  const sub = document.createElement("div");
  sub.className = "muted small";
  sub.textContent = b.market_name;
  name.append(sub);
  const debt = cell(b.value_eur === null ? `${qtyText(b.balance)} ${b.symbol}` : formatDecimal(b.value_eur, "EUR"), "num");
  debt.title = `≈ ${qtyText(b.balance)} ${b.symbol}, estimé`;
  const cost = cell(b.cost_year_eur === null ? "–" : `${formatDecimal(b.cost_year_eur, "EUR")}/an`, "num");
  const actions = document.createElement("td");
  actions.className = "actions";
  const checked = b.checks[0] ? `Vérifié le ${frDate(b.checks[0].date)} : ${qtyText(b.checks[0].quantity)} ${b.symbol}` : "Jamais vérifié";
  actions.append(
    smallButton("Vérifier", checked, () => openBorrowCheck(b), "link edit verify"),
    smallButton("Emprunter", "Nouvel emprunt du même jeton", () => openBorrowMove(b, "emprunt")),
    smallButton("Rembourser", "Remboursement", () => openBorrowMove(b, "remboursement"), "link"),
    smallButton("✕", "Arrêter le suivi", () => stopBorrow(b), "link"),
  );
  return rowOf(name, debt, cell(pctText(b.apy_today_pct), "num"), cost, healthCell(b.health_factor), actions);
}

async function borrowAction(promise, done) {
  try {
    const out = await promise;
    if (done) showMessage(typeof done === "function" ? done(out) : done);
    await refreshBorrows();
    await refreshRewards();
    refreshDebts().catch(() => {});
  } catch (err) {
    showMessage(err.message, true);
  }
}

function hideBorrowForms() {
  for (const id of ["form-borrow", "form-borrow-move", "form-borrow-check"]) $(id).hidden = true;
}

$("borrow-new").addEventListener("click", async () => {
  const form = $("form-borrow");
  const open = form.hidden;
  hideBorrowForms();
  if (!open) return;
  form.hidden = false;
  try {
    const [assets, chains] = await Promise.all([api("/api/assets"), api("/api/aave/chains")]);
    const crypto = assets.filter((a) => a.asset_class === "crypto");
    if (!crypto.length) throw new Error("Crée d'abord l'actif crypto emprunté (ex. USDC) dans Gérer.");
    fillSelect(form.elements.chain_id, chains.map((c) => ({ id: c.chain_id, name: c.name })), (c) => c.name);
    fillSelect(form.elements.asset_id, crypto, (a) => a.name);
    form.elements.op_date.value = aaveToday();
    form.elements.quantity.value = "";
    form.dataset.assets = JSON.stringify(crypto.map((a) => ({ id: a.id, name: a.name })));
    await loadBorrowReserves();
  } catch (err) {
    form.hidden = true;
    showMessage(err.message, true);
  }
});

async function loadBorrowReserves() {
  const form = $("form-borrow");
  borrowReserves = (await api(`/api/aave/reserves?chain_id=${form.elements.chain_id.value}`)).filter((r) => r.borrowable);
  const items = borrowReserves.map((r, i) => ({ ...r, id: i })).sort((a, b) => a.symbol.localeCompare(b.symbol));
  fillSelect(form.elements.reserve, items, (r) => `${r.symbol} · ${r.market_name} · ${pctText(r.borrow_apy_pct)}`);
  matchBorrowAsset();
}

function matchBorrowAsset() {
  const form = $("form-borrow");
  const r = borrowReserves[Number(form.elements.reserve.value)];
  const assets = JSON.parse(form.dataset.assets || "[]");
  const same = r && assets.find((a) => a.name.toUpperCase().split(/[\s(]/)[0] === r.symbol.toUpperCase());
  if (same) form.elements.asset_id.value = same.id;
}

$("form-borrow").elements.chain_id.addEventListener("change", () => loadBorrowReserves().catch((err) => showMessage(err.message, true)));
$("form-borrow").elements.reserve.addEventListener("change", matchBorrowAsset);
for (const button of document.querySelectorAll("#form-borrow [data-cancel], #form-borrow-move [data-cancel], #form-borrow-check [data-cancel]")) {
  button.addEventListener("click", () => { button.closest("form").hidden = true; });
}

$("form-borrow").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const r = borrowReserves[Number(form.elements.reserve.value)];
  if (!r) return;
  const body = {
    asset_id: Number(form.elements.asset_id.value),
    chain_id: Number(form.elements.chain_id.value),
    market: r.market,
    token: r.token,
    quantity: decimalInput(form.elements.quantity.value),
    op_date: form.elements.op_date.value,
  };
  form.hidden = true;
  borrowAction(api("/api/aave/borrows", { method: "POST", body: JSON.stringify(body) }), "Emprunt suivi.");
});

function openBorrowMove(b, direction) {
  hideBorrowForms();
  borrowTarget = { ...b, direction };
  const form = $("form-borrow-move");
  $("borrow-move-label").textContent = `${b.chain} · ${b.symbol}`;
  form.querySelector("[data-move-word]").textContent = direction === "emprunt" ? "Emprunt" : "Remboursement";
  form.elements.quantity.value = "";
  form.elements.op_date.value = aaveToday();
  form.hidden = false;
  form.elements.quantity.focus();
}

$("form-borrow-move").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const body = { direction: borrowTarget.direction, quantity: decimalInput(form.elements.quantity.value), op_date: form.elements.op_date.value };
  form.hidden = true;
  borrowAction(
    api(`/api/aave/borrows/${borrowTarget.id}/moves`, { method: "POST", body: JSON.stringify(body) }),
    body.direction === "emprunt" ? "Emprunt enregistré." : "Remboursement enregistré.",
  );
});

function openBorrowCheck(b) {
  hideBorrowForms();
  borrowTarget = b;
  const form = $("form-borrow-check");
  $("borrow-check-label").textContent = `${b.chain} · ${b.symbol}`;
  form.elements.check_date.value = aaveToday();
  form.elements.quantity.value = "";
  form.hidden = false;
  form.elements.quantity.focus();
}

$("form-borrow-check").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const body = { check_date: form.elements.check_date.value, quantity: decimalInput(form.elements.quantity.value) };
  const symbol = borrowTarget.symbol;
  form.hidden = true;
  borrowAction(
    api(`/api/aave/borrows/${borrowTarget.id}/checks`, { method: "POST", body: JSON.stringify(body) }),
    (out) => (out.gap === null ? "Dette enregistrée." : `Écart avec l'estimation : ${formatDecimal(out.gap)} ${symbol}. Dette recalée.`),
  );
});

async function stopBorrow(b) {
  if (!await askConfirm(`Arrêter le suivi de l'emprunt ${b.chain} · ${b.symbol} ?`, { detail: "Ses mouvements et dettes vérifiées seront retirés (sauvegarde de la base avant).", ok: "Arrêter" })) return;
  borrowAction(api(`/api/aave/borrows/${b.id}`, { method: "DELETE" }), "Suivi arrêté.");
}

bindSortable("borrows-table", ["value_eur", "apy_today_pct", "cost_year_eur", "health_factor"]);
