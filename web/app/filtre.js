// --- Filtre de lignes (D-069) : réglé dans Vue d'ensemble, groupé par compte puis par actif ;
// Vue d'ensemble, Performance, Calendrier, Comparaison et Projection le suivent, avec un
// bandeau qui le rappelle. Positions, Gérer, Revenus, Allocation cible et Fiscalité restent
// complets : le patrimoine réel n'est jamais amputé sans le dire.
// Exclure plutôt que choisir : une ligne achetée plus tard est comptée d'office.
const FILTER_KEY = PREF + "filtre";
const lineFilter = { lines: [], accounts: [], ...loadPref(FILTER_KEY, {}) };
const lineKey = (accountId, assetId) => `${accountId}-${assetId}`;

function saveFilter() {
  savePref(FILTER_KEY, lineFilter);
}

// Lignes connues : chaque couple (compte, actif) qui a au moins une opération, par compte.
// Un transfert vers un compte de l'app (D-085) compte aussi pour le compte d'arrivée : sans
// cela, un wallet qui n'a reçu que des titres importés n'apparaissait pas dans le filtre.
function filterGroups() {
  const pairs = new Map();
  for (const op of lists.operations) {
    if (!op.asset_id) continue;
    pairs.set(lineKey(op.account_id, op.asset_id), op);
    if (op.to_account_id) pairs.set(lineKey(op.to_account_id, op.asset_id), op);
  }
  // Comptes et actifs dans l'ordre choisi dans « Gérer » (D-070), déjà celui de l'API.
  return lists.accounts.map((account) => ({
    account,
    lines: lists.assets
      .filter((asset) => pairs.has(lineKey(account.id, asset.id)))
      .map((asset) => ({ key: lineKey(account.id, asset.id), asset })),
  })).filter((g) => g.lines.length || Number(g.account.cash));
}

// Oublie les comptes et lignes supprimés depuis, et reprend l'ancien filtre par actif.
function cleanFilter() {
  const groups = filterGroups();
  const keys = new Set(groups.flatMap((g) => g.lines.map((l) => l.key)));
  const ids = new Set(groups.map((g) => g.account.id));
  if (perf.hidden.length) {
    for (const g of groups) for (const l of g.lines) if (perf.hidden.includes(l.asset.id)) lineFilter.lines.push(l.key);
    perf.hidden = [];
    savePerf();
  }
  const lines = [...new Set(lineFilter.lines.filter((k) => keys.has(k)))];
  const accounts = lineFilter.accounts.filter((id) => ids.has(id));
  if (lines.length !== lineFilter.lines.length || accounts.length !== lineFilter.accounts.length) {
    Object.assign(lineFilter, { lines, accounts });
    saveFilter();
  }
  return groups;
}

const filterActive = () => lineFilter.lines.length > 0 || lineFilter.accounts.length > 0;

function addExclude(params) {
  for (const key of lineFilter.lines) params.append("exclude_line", key);
  for (const id of lineFilter.accounts) params.append("exclude_account", id);
  return params;
}

// Ce qui est laissé de côté, en clair : « PEA (compte entier), ETF Monde (CTO) ».
function filterSummary() {
  const accounts = new Map(lists.accounts.map((a) => [a.id, a.name]));
  const assets = new Map(lists.assets.map((a) => [a.id, a.name]));
  return [
    ...lineFilter.accounts.map((id) => `${accounts.get(id)} (compte entier)`),
    ...lineFilter.lines.map((k) => {
      const [acc, asset] = k.split("-").map(Number);
      return `${assets.get(asset)} (${accounts.get(acc)})`;
    }),
  ].join(", ");
}

function setFilter(update) {
  Object.assign(lineFilter, update);
  saveFilter();
  // Vue d'ensemble se recharge (titres, courbe), puis l'onglet ouvert.
  refresh().catch((err) => showMessage(err.message, true));
}

// Onglets qui suivent le filtre : le bouton « Lignes » n'apparaît que sur eux.
const FILTERED_PAGES = new Set(["apercu", "performance", "visualisations", "projection"]);
let filterUseful = false; // au moins deux lignes ou deux comptes à choisir

function renderLineFilter() {
  const groups = cleanFilter();
  const total = groups.reduce((n, g) => n + g.lines.length, 0);
  filterUseful = total >= 2 || groups.length >= 2;
  renderFilterButton();
  const chip = (text, active, onClick, title) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `chip${active ? " active" : " off"}`;
    b.textContent = text;
    b.title = title;
    b.setAttribute("aria-pressed", String(active));
    b.addEventListener("click", onClick);
    return b;
  };
  const kept = (g) => !lineFilter.accounts.includes(g.account.id);
  // Il reste toujours au moins une ligne ou un compte compté.
  const wouldEmpty = (accounts, lines) => !groups.some((g) => !accounts.includes(g.account.id) && g.lines.some((l) => !lines.includes(l.key)));
  const apply = (accounts, lines) => {
    if (wouldEmpty(accounts, lines)) showMessage("Garde au moins une ligne.", true);
    else setFilter({ accounts, lines });
  };
  const rows = groups.map((g) => {
    const row = document.createElement("div");
    row.className = "filter-row";
    const accountOn = kept(g);
    const own = new Set(g.lines.map((l) => l.key));
    const account = chip(g.account.name, accountOn, () => {
      if (accountOn) {
        apply([...lineFilter.accounts, g.account.id], lineFilter.lines.filter((k) => !own.has(k)));
      } else {
        apply(lineFilter.accounts.filter((id) => id !== g.account.id), lineFilter.lines.filter((k) => !own.has(k)));
      }
    }, accountOn ? "Retirer tout ce compte (lignes et espèces)" : "Remettre tout ce compte");
    account.classList.add("filter-account");
    row.append(account);
    for (const l of g.lines) {
      const on = accountOn && !lineFilter.lines.includes(l.key);
      row.append(chip(l.asset.name, on, () => {
        if (!accountOn) {
          // Compte retiré : on le remet avec cette seule ligne.
          const others = g.lines.filter((x) => x.key !== l.key).map((x) => x.key);
          apply(lineFilter.accounts.filter((id) => id !== g.account.id), [...lineFilter.lines.filter((k) => !own.has(k)), ...others]);
        } else if (on) apply(lineFilter.accounts, [...lineFilter.lines, l.key]);
        else apply(lineFilter.accounts, lineFilter.lines.filter((k) => k !== l.key));
      }, on ? `Retirer ${l.asset.name} de ${g.account.name}` : `Remettre ${l.asset.name} de ${g.account.name}`));
    }
    return row;
  });
  const reset = chip("Tout compter", !filterActive(), () => setFilter({ accounts: [], lines: [] }), "Remettre toutes les lignes");
  reset.classList.remove("off");
  reset.classList.add("filter-reset");
  $("line-filter").replaceChildren(...rows, reset);
}

// Bouton « Lignes » de la barre du haut : remplace le bandeau de chaque onglet (D-094). En
// accent quand des lignes sont retirées, le détail dans son infobulle native.
function renderFilterButton() {
  const button = $("filter-toggle");
  button.hidden = !filterUseful || !FILTERED_PAGES.has(currentPage);
  const n = lineFilter.accounts.length + lineFilter.lines.length;
  button.classList.toggle("active", filterActive());
  // Icône seule ; une pastille compte les lignes retirées quand le filtre agit.
  $("line-filter-summary").textContent = String(n);
  $("line-filter-summary").hidden = !filterActive();
  button.title = filterActive() ? `Calcul sans : ${filterSummary()}` : "Choisir les lignes comptées";
}

function changePerf(update) {
  Object.assign(perf, update);
  savePerf();
  refreshPerformance().catch((err) => showMessage(err.message, true));
}

for (const b of document.querySelectorAll("[data-period]")) {
  b.addEventListener("click", () => changePerf({ period: b.dataset.period }));
}
for (const b of document.querySelectorAll("[data-preset]")) {
  b.addEventListener("click", () => changePerf({ preset: b.dataset.preset, start: null, end: null }));
}
for (const th of document.querySelectorAll("#perf-table th[data-sort]")) {
  th.querySelector("button").addEventListener("click", () => {
    const key = th.dataset.sort;
    // Nouvelle colonne : les montants du plus grand au plus petit, les périodes de la plus ancienne.
    const first = key === "period" ? "asc" : "desc";
    const same = perf.sort.key === key;
    perf.sort = { key, dir: same ? (perf.sort.dir === "asc" ? "desc" : "asc") : first };
    savePerf();
    if (lastPerformance) renderPerfTable(lastPerformance.periods);
  });
}
for (const b of document.querySelectorAll("[data-perf-chart]")) {
  b.addEventListener("click", () => {
    perf.chart = b.dataset.perfChart;
    savePerf();
    // Autre type de graphique : on repart d'un graphique neuf plutôt que de le transformer.
    charts["chart-performance"]?.destroy();
    delete charts["chart-performance"];
    if (lastPerformance) renderPerformance(lastPerformance);
  });
}
for (const id of ["perf-start", "perf-end"]) {
  $(id).addEventListener("change", () =>
    changePerf({ preset: null, start: $("perf-start").value || null, end: $("perf-end").value || null }),
  );
}
syncPerfControls(null);

// --- Alerte de variation du jour ------------------------------------------------------
// Tout se passe dans le navigateur : seuil gardé en local, notification affichée par le
// système (aucun service tiers). La notification ne contient que des pourcentages.

const ALERT_KEY = PREF + "alerte";
const ALERT_SEEN_KEY = PREF + "alerte.vue";
const alertPref = loadPref(ALERT_KEY, { threshold: "2", notify: false });

function alertThreshold() {
  const value = Number(cleanDecimal(alertPref.threshold ?? ""));
  return value > 0 ? value : null; // vide, 0 ou invalide : alerte coupée
}

// Variations du jour (total et positions) dont la valeur absolue atteint le seuil.
function alertItems(portfolio, threshold) {
  const items = [];
  for (const [cur, t] of Object.entries(portfolio.totals)) {
    if (t.day_change_pct) items.push({ key: `total-${cur}`, label: "Total", pct: t.day_change_pct });
  }
  for (const p of portfolio.positions) {
    if (p.day_change_pct) items.push({ key: `${p.account}-${p.asset}`, label: p.asset, pct: p.day_change_pct });
  }
  // Number() ne sert qu'à comparer au seuil : aucun montant n'est recalculé ici.
  return items.filter((i) => Math.abs(Number(i.pct)) >= threshold);
}

// Alerte fermée avec ✕ : lignes concernées retenues pour la journée. Elle revient le
// lendemain, ou plus tôt si une autre ligne franchit le seuil.
const ALERT_CLOSED_KEY = PREF + "alerte.fermee";
let alertKeys = [];

function checkAlerts(portfolio) {
  const threshold = alertThreshold();
  const items = threshold ? alertItems(portfolio, threshold) : [];
  const el = $("alert");
  alertKeys = items.map((i) => i.key);
  const closed = loadPref(ALERT_CLOSED_KEY, { day: "", keys: [] });
  const dismissed = closed.day === isoDate(new Date()) && alertKeys.every((k) => closed.keys.includes(k));
  el.hidden = items.length === 0 || dismissed;
  syncToasts();
  if (!items.length) return;
  const text = items.map((i) => `${i.label} ${formatPct(i.pct)}`).join(", ");
  el.querySelector("[data-text]").textContent = `Variation du jour au-delà de ${formatDecimal(String(threshold))} % : ${text}.`;
  notifyOnce(items, text);
}

$("alert").querySelector("[data-close-toast]").addEventListener("click", () => {
  savePref(ALERT_CLOSED_KEY, { day: isoDate(new Date()), keys: alertKeys });
  $("alert").hidden = true;
  syncToasts();
});

// Une notification par jour au plus pour chaque ligne, même si la page se recharge.
function notifyOnce(items, text) {
  if (!alertPref.notify || !("Notification" in window) || Notification.permission !== "granted") return;
  const today = new Date().toISOString().slice(0, 10);
  const seen = loadPref(ALERT_SEEN_KEY, { day: today, keys: [] });
  if (seen.day !== today) Object.assign(seen, { day: today, keys: [] });
  if (items.every((i) => seen.keys.includes(i.key))) return;
  new Notification("Patrimoine : forte variation", { body: text, tag: "patrimoine-variation" });
  seen.keys = [...new Set([...seen.keys, ...items.map((i) => i.key)])];
  savePref(ALERT_SEEN_KEY, seen);
}

const alertForm = $("form-alert");
alertForm.threshold.value = alertPref.threshold ?? "";
alertForm.notify.checked = Boolean(alertPref.notify) && window.Notification?.permission === "granted";
alertForm.addEventListener("submit", (event) => event.preventDefault());
alertForm.threshold.addEventListener("change", () => {
  alertPref.threshold = alertForm.threshold.value.trim();
  savePref(ALERT_KEY, alertPref);
  if (lastPortfolio) checkAlerts(lastPortfolio);
});
alertForm.notify.addEventListener("change", async () => {
  if (alertForm.notify.checked) {
    // L'autorisation se demande sur un clic de l'utilisateur, une seule fois par navigateur.
    const permission = window.Notification ? await Notification.requestPermission() : "denied";
    if (permission !== "granted") {
      alertForm.notify.checked = false;
      showMessage("Notifications refusées par le navigateur : seul le bandeau s'affichera.", true);
    }
  }
  alertPref.notify = alertForm.notify.checked;
  savePref(ALERT_KEY, alertPref);
  if (lastPortfolio) checkAlerts(lastPortfolio);
});
