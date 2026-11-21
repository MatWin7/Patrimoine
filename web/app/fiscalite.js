// --- Repères fiscaux (D-059) -----------------------------------------------------------

$("form-per").tmi.value = taxPref.tmi;
if (!$("form-per").tmi.value) $("form-per").tmi.value = "30";
$("form-per").ceiling.value = taxPref.ceiling.replace(".", ",");
$("form-av").couple.value = taxPref.couple ?? "";

// Cellule « 5 ans » / « 8 ans » : ✓ atteint ou ⏳ date, explication au survol.
function milestoneCell(item, what) {
  if (!item.milestone) return cell("—", "muted");
  const td = cell(`${item.milestone_reached ? "✓ depuis le" : "⏳"} ${frDate(item.milestone)}`, item.milestone_reached ? "ok" : "");
  td.title = what(item.milestone_reached);
  return td;
}

// Date d'ouverture : saisie, ou à défaut la première opération (marquée « ≈ »).
function openedCell(item) {
  if (!item.opened_on) return cell("—", "muted");
  const td = cell(item.opened_on_entered ? frDate(item.opened_on) : `≈ ${frDate(item.opened_on)}`);
  if (!item.opened_on_entered) td.title = "Première opération : saisis la vraie date d'ouverture dans « Gérer » > Comptes > Modifier";
  return td;
}

// Versements : total, avec le détail au survol (champ du compte, opérations, déduits des achats).
function depositsCell(item) {
  const td = cell(formatDecimal(item.deposits, "EUR"), "num");
  const parts = [`${formatDecimal(item.deposits_before, "EUR")} saisis dans le compte`, `${formatDecimal(item.deposits_entered, "EUR")} d'opérations « Versement »`];
  if (item.deposits_implied !== "0.00") parts.push(`${formatDecimal(item.deposits_implied, "EUR")} déduits d'achats payés sans versement`);
  td.title = parts.join(" + ");
  return td;
}

function gaugeCell(pct) {
  const td = document.createElement("td");
  const bar = document.createElement("div");
  bar.className = "gauge";
  const fill = document.createElement("span");
  fill.style.width = `${Math.min(100, Number(pct ?? 0))}%`;
  bar.append(fill);
  const txt = document.createElement("span");
  txt.className = "small";
  txt.textContent = pctText(pct);
  td.className = "gauge-cell";
  td.append(bar, txt);
  return td;
}

function sumOf(items, key) {
  return items.reduce((t, x) => t + Number(x[key]), 0).toFixed(2);
}

function nextMilestone(items) {
  const todo = items.filter((x) => x.milestone && !x.milestone_reached).map((x) => x.milestone).sort();
  if (todo.length) return frDate(todo[0]);
  return items.some((x) => x.milestone_reached) ? "✓ atteint" : "—";
}

const PEA_5 = (ok) => (ok ? "Un retrait ne clôture plus le plan ; gains exonérés d'impôt (prélèvements sociaux dus)" : "Un retrait avant cette date clôture le plan");
const AV_8 = (ok) => (ok ? "Abattement annuel sur les gains des rachats (4 600 €, 9 200 € pour un couple)" : "Avant cette date, les gains des rachats sont imposés sans abattement");

// Taux du prélèvement forfaitaire : celui de l'année choisie (30 %, 31,4 % dès 2026) sauf taux
// saisi. Un ancien réglage « pfu » ne compte comme saisi que s'il différait des 30 % d'alors.
// Lu à chaque fois : la page peut s'afficher avant la fin du chargement du script.
function customPfu() {
  const pref = loadPref(TAX_KEY, {});
  return pref.pfu_custom ?? (pref.pfu && pref.pfu !== "30" ? pref.pfu : "");
}

async function refreshTax() {
  const form = $("form-tax");
  const year = form.year.value;
  const perForm = $("form-per");
  const ceiling = cleanDecimal(perForm.ceiling.value);
  const params = new URLSearchParams({ tmi: perForm.tmi.value });
  const custom = customPfu();
  if (custom) params.set("pfu", custom);
  if (year) params.set("year", year);
  if (ceiling) params.set("per_ceiling", ceiling);
  if ($("form-av").couple.value) params.set("couple", "true");
  const data = await api(`/api/tax?${params}`);
  $("tax-rules").textContent = `règles de ${data.rules_date}`;
  form.pfu.value = data.pfu_pct.replace(".", ",");
  form.pfu.dataset.default = data.pfu_default_pct;

  const peas = data.pea;
  lastTax = data;
  renderTaxModes();
  fillSortable("tax-pea-table", peas, (p) => rowOf(
    cell(p.account),
    openedCell(p),
    depositsCell(p),
    gaugeCell(p.used_pct),
    cell(formatDecimal(p.remaining, "EUR"), "num"),
    milestoneCell(p, PEA_5),
  ), "Aucun compte de type PEA.");
  const exitNote = " Vendre dans le PEA n'est pas imposé : l'impôt ne vient qu'au retrait, sur sa part de gains (valeur − versements, qui compte aussi les ventes et dividendes passés). Avant 5 ans : prélèvement forfaitaire et clôture du plan ; après : prélèvements sociaux seuls (taux du prélèvement forfaitaire − 12,8 %). Après un retrait partiel, la part de versements déjà retirée n'est pas déduite : gain et impôt affichés sont alors un peu sous-estimés.";
  $("tax-pea-note").textContent = (peas.some((p) => p.deposits_implied !== "0.00")
    ? "Versements = champ « Versements » du compte + opérations « Versement » + achats payés sans versement saisi (l'argent a forcément été versé). Pour le chiffre exact, reporte dans le compte le « montant des versements » affiché par ta banque."
    : "Versements = champ « Versements » du compte + opérations « Versement ». Survole un montant pour le détail.") + exitNote;

  renderLifeInsurance(data.assurance_vie, data.life_insurance);

  const per = data.per;
  const perKpis = [kpi(`Versés en ${per.year}`, formatDecimal(per.deposits_year, "EUR"), `${per.rows.length} PER`)];
  if (per.ceiling !== null) {
    perKpis.push(per.over !== "0.00"
      ? kpi("Au-delà du plafond", formatDecimal(per.over, "EUR"), "non déductible cette année")
      : kpi("Reste déductible", formatDecimal(per.remaining, "EUR"), `plafond ${formatDecimal(per.ceiling, "EUR")}`));
  }
  perKpis.push(
    kpi("Impôt économisé", formatDecimal(per.saving, "EUR"), `à ${per.tmi_pct} % de tranche marginale`),
    kpi("Net si sortie", formatDecimal(sumOf(per.rows, "net"), "EUR"), `impôt ${formatDecimal(sumOf(per.rows, "tax"), "EUR")}`),
  );
  $("tax-per-kpis").replaceChildren(...perKpis);
  $("tax-per-year-head").querySelector("button").textContent = `Versés en ${per.year}`;
  const warning = $("tax-per-warning");
  warning.hidden = !(per.low_tmi && per.rows.length);
  warning.textContent = `À ${per.tmi_pct} % de tranche marginale, déduire un versement fait gagner peu d'impôt, et la sortie sera imposée quand même : une assurance-vie (argent disponible, gains allégés après 8 ans) est souvent plus intéressante.`;
  fillSortable("tax-per-table", per.rows, (r) => rowOf(
    cell(r.account),
    cell(formatDecimal(r.deposits, "EUR"), "num"),
    cell(formatDecimal(r.deposits_year, "EUR"), "num"),
    cell(formatDecimal(r.value, "EUR"), "num"),
    cell(formatDecimal(signed(r.gains), "EUR"), signClass(r.gains)),
    cell(formatDecimal(r.tax, "EUR"), "num"),
    cell(formatDecimal(r.net, "EUR"), "num"),
  ), "Aucun compte de type PER.");

  const c = data.cto;
  const current = form.year.value || String(c.year);
  form.year.replaceChildren(...[...c.years].reverse().map((y) => new Option(String(y), y)));
  form.year.value = current;
  fillSortable("tax-sales-table", c.sales, saleRow, `Aucune vente sur un compte-titres en ${c.year}.`);
  fillSortable("tax-pea-sales-table", data.pea_sales, saleRow, `Aucune vente dans un PEA en ${c.year}.`);
  renderCryptoTax(data.crypto);
  renderForeignAccounts(data);
}

// Plus-values réalisées (ventes de l'année) ou latentes (si tout était vendu aujourd'hui) des
// cartes PEA et Comptes-titres (D-076) ; choix gardé dans le navigateur, sans rappeler l'API.
const TAX_MODE_KEY = PREF + "fiscalite.plus-values";
const taxModes = loadPref(TAX_MODE_KEY, { pea: "latentes", cto: "latentes" });
let lastTax = null;

const saleRow = (v) => rowOf(
  cell(frDate(v.date)),
  cell(v.account),
  clipCell(v.asset, "clip"),
  cell(formatDecimal(v.proceeds, v.currency), "num"),
  cell(formatDecimal(v.cost, v.currency), "num"),
  cell(formatDecimal(signed(v.gain_eur), "EUR"), signClass(v.gain_eur)),
);

for (const group of document.querySelectorAll(".tax-mode[data-mode-for]")) {
  for (const b of group.querySelectorAll("button")) {
    b.addEventListener("click", () => {
      taxModes[group.dataset.modeFor] = b.dataset.mode;
      savePref(TAX_MODE_KEY, taxModes);
      renderTaxModes();
    });
  }
}

function renderTaxModes() {
  for (const group of document.querySelectorAll(".tax-mode[data-mode-for]")) {
    for (const b of group.querySelectorAll("button")) b.classList.toggle("active", b.dataset.mode === taxModes[group.dataset.modeFor]);
  }
  for (const el of document.querySelectorAll("[data-mode-show]")) {
    const [card, mode] = el.dataset.modeShow.split(" ");
    el.hidden = taxModes[card] !== mode;
  }
  const data = lastTax;
  if (!data) return;
  const year = data.cto.year;
  const peas = data.pea;
  if (taxModes.pea === "realisees") {
    const r = data.pea_realized;
    $("tax-pea-kpis").replaceChildren(
      kpi(`Plus-values réalisées ${year}`, formatDecimal(r.gains, "EUR"), "ventes en gain", "ok"),
      kpi(`Moins-values réalisées ${year}`, formatDecimal(r.losses, "EUR"), "ventes en perte", "ko"),
      kpi(`Dividendes ${year}`, formatDecimal(sumOf(peas, "dividends"), "EUR"), "reçus bruts, non imposés dans le plan"),
      kpi("Impôt sur ces ventes", formatDecimal("0.00", "EUR"), "rien tant que tu ne retires pas d'argent du PEA"),
    );
  } else {
    const rates = [...new Set(peas.map((p) => p.exit_rate_pct))];
    $("tax-pea-kpis").replaceChildren(
      kpi("Plus-values latentes", formatDecimal(sumOf(peas, "latent_gains"), "EUR"), "lignes en gain", "ok"),
      kpi("Moins-values latentes", formatDecimal(sumOf(peas, "latent_losses"), "EUR"), "lignes en perte", "ko"),
      kpi(`Dividendes ${year}`, formatDecimal(sumOf(peas, "dividends"), "EUR"), "reçus bruts, non imposés dans le plan"),
      kpi("Impôt si retrait total", formatDecimal(sumOf(peas, "exit_tax"), "EUR"),
        rates.length === 1 ? `${formatDecimal(rates[0])} % des gains (${peas[0].milestone_reached ? "plus de 5 ans" : "moins de 5 ans"})` : "selon l'âge de chaque PEA"),
    );
  }

  const c = data.cto;
  const latent = taxModes.cto === "latentes";
  $("tax-cto-sub").textContent = latent
    ? "Si tout était vendu aujourd'hui, impôt au prélèvement forfaitaire"
    : `Ventes de ${year}, impôt au prélèvement forfaitaire`;
  $("tax-cto-div-head").querySelector("button").textContent = `Dividendes ${year}`;
  $("tax-cto-tax-head").querySelector("button").textContent = latent ? "Impôt si tout vendu" : "Impôt estimé";
  $("tax-kpis").replaceChildren(
    latent
      ? kpi("Plus-values latentes", formatDecimal(c.latent_gains, "EUR"), "lignes en gain", "ok")
      : kpi(`Plus-values ${year}`, formatDecimal(c.gains, "EUR"), "ventes en gain", "ok"),
    latent
      ? kpi("Moins-values latentes", formatDecimal(c.latent_losses, "EUR"), "lignes en perte", "ko")
      : kpi(`Moins-values ${year}`, formatDecimal(c.losses, "EUR"), "ventes en perte", "ko"),
    kpi(`Dividendes ${year} (net reçu)`, formatDecimal(c.dividends, "EUR"), "déjà imposés à la source en général"),
    latent
      ? kpi(`Impôt si tout vendu (${formatDecimal(c.rate_pct)} %)`, formatDecimal(c.latent_tax, "EUR"), "sur le solde plus-values − moins-values")
      : kpi(`Impôt estimé (${formatDecimal(c.rate_pct)} %)`, formatDecimal(c.tax, "EUR")),
  );
  const rows = c.rows.map((r) => (latent
    ? { ...r, gains: r.latent_gains, losses: r.latent_losses, tax: r.latent_tax }
    : r));
  fillSortable("tax-cto-table", rows, (r) => rowOf(
    cell(r.account),
    cell(formatDecimal(r.gains, "EUR"), r.gains === "0.00" ? "num" : "num ok"),
    cell(formatDecimal(r.losses, "EUR"), r.losses === "0.00" ? "num" : "num ko"),
    cell(formatDecimal(r.dividends, "EUR"), "num"),
    cell(formatDecimal(r.tax, "EUR"), "num"),
  ), "Aucun compte-titres.");
}

// Carte « Assurance-vie » (D-144) : gains, abattement de l'année, rachat sans impôt, rachats.
function renderLifeInsurance(avs, life) {
  const n = avs.length;
  const warning = $("tax-av-warning");
  const notes = [];
  if (life.over_cap) notes.push(`Plus de 150 000 € de versements au ${frDate(life.encours_date)} (${formatDecimal(life.encours, "EUR")}) : après 8 ans, une partie des gains des versements faits depuis le 27/09/2017 passe de 7,5 % à 12,8 %.`);
  if (avs.some((a) => a.estimated)) notes.push("≈ Valeur du contrat inconnue le jour d'un rachat (contrat hors euros) : tout ce rachat est compté en versements, les gains restants sont donc un peu surestimés.");
  warning.hidden = !notes.length;
  warning.textContent = notes.join(" ");
  const gainPct = Number(life.remaining) > 0 ? `${formatDecimal(signed(((Number(life.gains) / Number(life.remaining)) * 100).toFixed(2)))} %` : "";
  $("tax-av-kpis").replaceChildren(
    kpi("Valeur", formatDecimal(life.value, "EUR"), `${n} contrat${n > 1 ? "s" : ""}, versements ${formatDecimal(life.remaining, "EUR")}`),
    kpi("Gains", formatDecimal(signed(life.gains), "EUR"), gainPct, toneClass(life.gains)),
    kpi(`Abattement restant ${life.today_year}`, formatDecimal(life.allowance_left, "EUR"),
      life.allowance_used !== "0.00" ? `${formatDecimal(life.allowance_used, "EUR")} déjà utilisés sur ${formatDecimal(life.allowance, "EUR")}` : `sur ${formatDecimal(life.allowance, "EUR")} par an, après 8 ans`),
    kpi("Impôt si rachat total", formatDecimal(life.exit_tax, "EUR"), `dont ${formatDecimal(life.exit_social, "EUR")} de prélèvements sociaux`),
    kpi("Prochains 8 ans", nextMilestone(avs)),
  );
  fillSortable("tax-av-table", avs, (a) => {
    const paid = cell(formatDecimal(a.remaining, "EUR"), "num");
    paid.title = a.buybacks
      ? `${formatDecimal(a.paid_in, "EUR")} versés, dont ${formatDecimal((Number(a.paid_in) - Number(a.remaining)).toFixed(2), "EUR")} déjà remboursés par des rachats`
      : depositsCell(a).title;
    const free = cell(a.tax_free === a.value ? "tout" : formatDecimal(a.tax_free, "EUR"), "num");
    free.title = a.tax_free === a.value
      ? "Contrat en perte ou sans gain : un rachat n'est pas imposé"
      : a.milestone_reached ? "Part de gains dans l'abattement restant ; prélèvements sociaux dus sur les unités de compte" : "Pas d'abattement avant 8 ans";
    const exit = cell(formatDecimal(a.exit_tax, "EUR"), "num");
    exit.title = `${formatDecimal(a.exit_ir, "EUR")} d'impôt + ${formatDecimal(a.exit_social, "EUR")} de prélèvements sociaux`;
    return rowOf(
      cell(a.account),
      openedCell(a),
      paid,
      cell(formatDecimal(a.value, "EUR"), "num"),
      cell(`${a.estimated ? "≈ " : ""}${formatDecimal(signed(a.gains), "EUR")}`, signClass(a.gains)),
      milestoneCell(a, AV_8),
      free,
      exit,
    );
  }, "Aucun compte de type assurance-vie.");
  $("tax-av-buybacks-title").textContent = `Rachats de ${life.year}`;
  fillSortable("tax-av-buybacks-table", life.buybacks, (b) => rowOf(
    cell(frDate(b.date)),
    cell(b.account),
    cell(formatDecimal(b.amount, "EUR"), "num"),
    cell(`${b.estimated ? "≈ " : ""}${formatDecimal(b.gain, "EUR")}`, "num"),
    cell(b.eight_years ? "✓ plus de 8 ans" : "non, moins de 8 ans", b.eight_years ? "ok" : "muted"),
  ), `Aucun rachat d'assurance-vie en ${life.year}.`);
}

// Carte « Crypto » (D-066) : ventes en euros de l'année au portefeuille global (formulaire 2086).
function renderCryptoTax(k) {
  const limit = formatDecimal(k.exempt_limit, "EUR");
  $("tax-crypto-kpis").replaceChildren(
    kpi(`${k.latent.startsWith("-") ? "Moins" : "Plus"}-value latente`, formatDecimal(k.latent, "EUR"), `valeur ${formatDecimal(k.value, "EUR")} − prix d'acquisition ${formatDecimal(k.acquisition, "EUR")}`, toneClass(k.latent)),
    kpi(`Ventes en euros ${k.year}`, formatDecimal(k.total, "EUR"), k.exempt ? `jusqu'à ${limit} : pas d'impôt` : `au-delà de ${limit} : imposables`),
    kpi(`${k.net.startsWith("-") ? "Moins" : "Plus"}-value nette ${k.year}`, formatDecimal(k.net, "EUR"), `gains ${formatDecimal(k.gains, "EUR")}, pertes ${formatDecimal(k.losses, "EUR")}`, toneClass(k.net)),
    kpi(`Impôt estimé (${formatDecimal(k.rate_pct)} %)`, formatDecimal(k.tax, "EUR"), k.swaps ? `${k.swaps} échange${k.swaps > 1 ? "s" : ""} non imposable${k.swaps > 1 ? "s" : ""}` : ""),
    kpi("Impôt si tout vendu", formatDecimal(k.exit_tax, "EUR"), "en euros, aujourd'hui"),
  );
  const warning = $("tax-crypto-warning");
  warning.hidden = !k.estimated;
  warning.textContent = "≈ Valeur du portefeuille estimée pour certaines ventes : l'app ne garde pas les anciens cours des cryptos (vendue au prix de la vente, les autres au dernier cours gardé ce jour-là, sinon au prix de leur dernière opération). Pour le chiffre exact, saisis sur la vente la valeur de toutes tes cryptos ce jour-là (Gérer > Opérations > Modifier).";
  $("tax-crypto-title").textContent = `Ventes imposables de ${k.year} (aide au formulaire 2086)`;
  fillSortable("tax-crypto-table", k.sales, (v) => {
    const value = cell(`${v.estimated ? "≈ " : ""}${formatDecimal(v.portfolio_value, "EUR")}`, "num");
    if (v.estimated) value.title = "Estimée : saisis la vraie valeur sur la vente pour la remplacer";
    return rowOf(
      cell(frDate(v.date)),
      clipCell(v.asset, "clip"),
      value,
      cell(formatDecimal(v.gross, "EUR"), "num"),
      cell(formatDecimal(v.fees, "EUR"), "num"),
      cell(formatDecimal(v.acquisition, "EUR"), "num"),
      cell(formatDecimal(v.fraction, "EUR"), "num"),
      cell(formatDecimal(signed(v.gain), "EUR"), signClass(v.gain)),
    );
  }, k.accounts.length ? `Aucune vente en euros sur un compte crypto en ${k.year}.` : "Aucun compte de type crypto.");
}

// Comptes à l'étranger à déclarer (D-104) et comptes crypto dont la garde reste à préciser.
function renderForeignAccounts(data) {
  const unknown = data.custody_unknown;
  const warning = $("tax-custody-warning");
  warning.hidden = !unknown.length;
  warning.textContent = `Garde à préciser (plateforme ou portefeuille personnel) dans Gérer : ${unknown.join(", ")}.`;
  fillSortable("tax-foreign-table", data.foreign_accounts, (r) => rowOf(
    clipCell(r.account, "clip"),
    cell(r.institution ?? "–"),
    cell(r.form),
    cell(r.opened_on ? frDate(r.opened_on) : "–"),
  ), "Aucun compte coché « Établissement à l'étranger ».");
}

$("form-tax").addEventListener("submit", (e) => e.preventDefault());
$("form-tax").year.addEventListener("change", () => refreshTax().catch((err) => showMessage(err.message, true)));
function saveTaxPref() {
  const per = $("form-per");
  // Taux vidé ou égal à celui proposé pour l'année : on suit de nouveau le taux de l'année.
  const pfu = cleanDecimal($("form-tax").pfu.value) ?? "";
  savePref(TAX_KEY, {
    pfu_custom: pfu && pfu !== $("form-tax").pfu.dataset.default ? pfu : "",
    tmi: per.tmi.value,
    ceiling: cleanDecimal(per.ceiling.value) ?? "",
    couple: $("form-av").couple.value,
  });
  refreshTax().catch((err) => showMessage(err.message, true));
}
$("form-tax").pfu.addEventListener("change", saveTaxPref);
$("form-per").tmi.addEventListener("change", saveTaxPref);
$("form-per").ceiling.addEventListener("change", saveTaxPref);
$("form-per").addEventListener("submit", (e) => e.preventDefault());
$("form-av").couple.addEventListener("change", saveTaxPref);
$("form-av").addEventListener("submit", (e) => e.preventDefault());
