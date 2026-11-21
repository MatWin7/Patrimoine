// --- Vérifier un compte avec le relevé de la banque (D-063) -----------------------------
// Rien n'est enregistré par la comparaison ; une correction n'est faite qu'avec ton accord.

let reconcileAccount = null;

function reconcileUrl() {
  return `/api/accounts/${reconcileAccount.id}/reconcile`;
}

async function openReconcile(account) {
  reconcileAccount = account;
  const form = $("form-reconcile");
  form.reset();
  $("dlg-reconcile-title").textContent = `Vérifier « ${account.name} »`;
  $("reconcile-cash-label").textContent = account.kind === "livret" ? "Solde affiché par la banque" : "Espèces affichées par la banque";
  $("reconcile-result").replaceChildren();
  const data = await api(reconcileUrl(), { method: "POST", body: "{}" });
  renderReconcileLines(data.lines);
  $("dlg-reconcile").showModal();
}

function renderReconcileLines(lines) {
  tableRows("reconcile-lines", lines.map((l) => {
    const bank = document.createElement("td");
    bank.className = "num";
    if (l.manual) {
      bank.textContent = "valeur saisie";
      bank.classList.add("muted");
    } else {
      const input = document.createElement("input");
      input.name = `q-${l.asset_id}`;
      input.inputMode = "decimal";
      input.className = "qty-input";
      input.setAttribute("aria-label", `Quantité de ${l.asset} selon la banque`);
      bank.append(input);
    }
    const gap = document.createElement("td");
    gap.dataset.gap = l.asset_id;
    return rowOf(clipCell(l.asset, "clip"), cell(formatDecimal(l.app), "num"), bank, gap);
  }), "Aucune ligne sur ce compte : seules les espèces se vérifient.");
}

function reconcileBody(form) {
  const quantities = {};
  for (const input of form.querySelectorAll("input[name^='q-']")) {
    const q = cleanDecimal(input.value);
    if (q !== null) quantities[input.name.slice(2)] = q;
  }
  return { cash: cleanDecimal(form.cash.value), quantities };
}

$("form-reconcile").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api(reconcileUrl(), { method: "POST", body: JSON.stringify(reconcileBody(event.target)) });
    showReconcile(data);
  } catch (err) {
    showMessage(err.message, true);
  }
});

function causesText(causes) {
  return `Causes probables : ${causes.join(", ")}.`;
}

function showReconcile(data) {
  const cur = data.currency;
  for (const l of data.lines) {
    const td = document.querySelector(`[data-gap="${l.asset_id}"]`);
    if (!td) continue;
    td.replaceChildren();
    td.className = "";
    if (!("ok" in l)) continue;
    if (l.ok) {
      td.textContent = "✓";
      td.className = "ok";
      continue;
    }
    td.className = "ko small";
    td.textContent = `${l.gap.startsWith("-") ? "" : "+"}${formatDecimal(l.gap)} `;
    td.title = causesText(l.causes);
    // Achat ou vente oublié : le formulaire « Nouvelle opération » pré-rempli, prix à compléter.
    const type = l.gap.startsWith("-") ? "vente" : "achat";
    td.append(smallButton(`Saisir ${type === "achat" ? "l'achat" : "la vente"}`, td.title, () => prefillOperation(type, l)));
  }
  const out = [];
  const c = data.cash;
  if (c) {
    const p = document.createElement("p");
    if (c.ok) {
      p.className = "ok";
      p.textContent = `✓ Espèces : ${formatDecimal(c.app, cur)}, comme la banque.`;
    } else {
      p.className = "warn";
      p.textContent = `Espèces : ${formatDecimal(c.app, cur)} selon l'app, ${formatDecimal(c.bank, cur)} selon la banque (écart ${formatDecimal(signed(c.gap), cur)}). ${causesText(c.causes)}`;
      p.append(" ", smallButton(correctionLabel(c.correction, cur), "Rien n'est enregistré sans ta confirmation", () => applyCorrection(c.correction)));
    }
    out.push(p);
  }
  const summary = document.createElement("p");
  summary.className = "muted small";
  const checked = data.lines.filter((l) => "ok" in l).length + (c ? 1 : 0);
  summary.textContent = !checked
    ? "Rien à comparer : saisis au moins un chiffre de ta banque."
    : data.ok ? "Tout concorde avec ta banque." : "Survole un écart pour ses causes probables. Si l'écart vient d'une opération oubliée, saisis-la plutôt qu'une correction : l'historique restera juste.";
  out.push(summary);
  $("reconcile-result").replaceChildren(...out);
}

function correctionLabel(fix, cur) {
  const amount = formatDecimal(fix.amount, cur);
  return { solde: `Prendre ${amount} comme solde`, versement: `Enregistrer un versement de ${amount}`, frais: `Enregistrer ${amount} de frais` }[fix.kind];
}

async function applyCorrection(fix) {
  const account = reconcileAccount;
  const cur = account.currency;
  if (!await askConfirm(`${correctionLabel(fix, cur)} sur « ${account.name} », à la date d'aujourd'hui ?`, { ok: "Appliquer", danger: false })) return;
  try {
    if (fix.kind === "solde") {
      const body = { name: account.name, kind: account.kind, institution: account.institution, currency: cur, cash: fix.amount, opened_on: account.opened_on, deposits_before: "0" };
      await api(`/api/accounts/${account.id}`, { method: "PATCH", body: JSON.stringify(body) });
    } else {
      const body = { account_id: account.id, op_date: isoDate(new Date()), op_type: fix.kind, amount: fix.amount, note: "Correction après vérification avec la banque" };
      await api("/api/operations", { method: "POST", body: JSON.stringify(body) });
    }
    showMessage("Correction enregistrée.");
    await refresh();
    reconcileAccount = lists.accounts.find((a) => a.id === account.id) ?? account;
    $("form-reconcile").requestSubmit(); // nouvelle comparaison avec les mêmes chiffres
  } catch (err) {
    showMessage(err.message, true);
  }
}

function prefillOperation(type, line) {
  $("dlg-reconcile").close();
  const form = newOperation;
  showOperationMode("saisie");
  form.op_type.value = type;
  form.account_id.value = reconcileAccount.id;
  form.asset_id.value = line.asset_id;
  form.quantity.value = line.gap.replace("-", "").replace(".", ",");
  form.op_date.value = isoDate(new Date());
  syncOperationForm(form);
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  form.unit_price.focus();
  showMessage(`Complète la date et le prix de ${type === "achat" ? "l'achat" : "la vente"} d'après ton relevé.`);
}

let presetAsset = {};

// `preset` : champs pré-remplis d'un nouvel actif (actif inconnu d'un import CSV).
function openAsset(asset = null, preset = {}) {
  const form = $("form-asset");
  form.dataset.id = asset?.id ?? "";
  const { hint, ...fields } = preset;
  fillForm(form, asset, { asset_class: "etf", currency: "EUR", ...fields });
  presetAsset = fields; // champs du relevé : gardés visibles même hors de la classe
  $("asset-ticker-choices").hidden = true;
  // Actif créé depuis un relevé : rappel de la devise à respecter (D-071).
  $("asset-currency-hint").hidden = !hint;
  $("asset-currency-hint").textContent = hint ?? "";
  // Catégories déjà utilisées, proposées à la saisie (D-061).
  const used = [...new Set(lists.assets.map((a) => a.category).filter(Boolean))].sort((x, y) => x.localeCompare(y, "fr"));
  $("category-list").replaceChildren(...used.map((c) => Object.assign(document.createElement("option"), { value: c })));
  showPickedColor();
  syncAssetForm();
  $("dlg-asset-title").textContent = asset ? `Modifier « ${asset.name} »` : "Ajouter un actif";
  $("dlg-asset").showModal();
}

// Aide du formulaire d'actif, selon sa classe.
const ASSET_HELP = {
  action: "Ticker à vérifier sur finance.yahoo.com/quote/TICKER (.PA = Paris, .DE = Xetra, sans suffixe = États-Unis).",
  etf: "Ticker à vérifier sur finance.yahoo.com/quote/TICKER (.PA = Paris). Frais courants : sur le DIC ou la fiche du fonds.",
  fonds: "OPCVM ou unité de compte : coté sur Yahoo, renseigne son ticker ; sinon laisse-le vide, sa valeur se saisit à la main (bouton « Valeur »). Frais courants : sur le DIC.",
  crypto: "L'id CoinGecko est la fin de l'adresse de sa page : coingecko.com/fr/coins/bitcoin → bitcoin.",
  obligation: "Cotée : renseigne son ticker. Sinon laisse-le vide : sa valeur se saisit à la main (bouton « Valeur » de la liste).",
  fonds_euros: "Pas de cours public : sa valeur se saisit à la main, d'après le relevé (bouton « Valeur » de la liste).",
  scpi: "SCPI non cotée : laisse le ticker vide, sa valeur se saisit à la main (prix de retrait, bouton « Valeur »).",
  or: "ETC or coté (ex. GOLD.PA) : renseigne son ticker. Pièces ou lingots : laisse-le vide, valeur saisie à la main.",
  immobilier: "Pas de cours public : sa valeur se saisit à la main (bouton « Valeur » de la liste), sans déduire de dette.",
  autre: "Avec un ticker Yahoo ou un id CoinGecko, le cours se met à jour ; sans, la valeur se saisit à la main.",
};

// Classe choisie : seuls les champs utiles s'affichent ; ticker ou id obligatoire si coté.
function syncAssetForm() {
  const form = $("form-asset");
  const cls = form.asset_class.value;
  syncFieldsFor(form, cls, lists.assets.find((a) => String(a.id) === form.dataset.id) ?? presetAsset);
  form.ticker.required = cls === "action" || cls === "etf";
  form.coingecko_id.required = cls === "crypto";
  form.querySelector("[data-optional]").hidden = form.ticker.required;
  form.querySelector("[data-crypto-tip]").hidden = cls !== "crypto";
  form.ticker.placeholder = cls === "crypto" ? "auto, ex. BTC-EUR" : "ex. CW8.PA";
  const note = "Si le ticker change, les cours en cache de l'actif sont effacés. Changer la devise convertit ses opérations au taux BCE de leur jour.";
  $("asset-help").textContent = `${ASSET_HELP[cls] ?? ""} ${cls === "fonds_euros" || cls === "immobilier" ? "" : note}`.trim();
}
$("form-asset").asset_class.addEventListener("change", () => {
  // Nouvel actif crypto : coté en dollars par défaut (D-085), modifiable.
  const form = $("form-asset");
  if (!form.dataset.id && form.asset_class.value === "crypto" && form.currency.value === "EUR") form.currency.value = "USD";
  syncAssetForm();
});

// Pastille à côté du choix de couleur : la couleur choisie, ou celle donnée automatiquement.
function showPickedColor() {
  const form = $("form-asset");
  const asset = lists.assets.find((a) => String(a.id) === form.dataset.id);
  $("asset-color-dot").style.background = form.color.value
    ? css(PALETTE[Number(form.color.value) - 1])
    : asset && !asset.color ? partColor("ligne", asset.name) : otherColor();
}
$("form-asset").color.addEventListener("change", showPickedColor);

// Branche un formulaire de fenêtre : POST si nouveau, PATCH sinon ; toBody nettoie les champs.
function bindDialogForm(formId, dialogId, path, noun, toBody, force = false) {
  const form = $(formId);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = form.dataset.id;
    try {
      await api(id ? `${path}/${id}` : path, {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(toBody(Object.fromEntries(new FormData(form)))),
      });
      $(dialogId).close();
      showMessage(`${noun} ${id ? "modifié" : "créé"}.`);
      await refresh(force); // nouveau ticker : cours rechargé tout de suite
    } catch (err) {
      showMessage(err.message, true);
    }
  });
}

bindDialogForm("form-account", "dlg-account", "/api/accounts", "Compte", (f) => ({
  name: f.name,
  kind: f.kind,
  institution: orNull(f.institution),
  currency: f.currency,
  // Un seul des deux champs compte selon le type (D-075) ; l'autre part à zéro. Portefeuille
  // personnel : espèces à zéro, sauf un solde déjà saisi (champ resté visible).
  cash: CASH_ENTERED.includes(f.kind) && !$("form-account").querySelector("[data-kind=cash]").hidden ? cleanDecimal(f.cash) ?? "0" : "0",
  custody: f.kind === "crypto" ? f.custody || null : null,
  abroad: f.abroad === "on" && !$("account-abroad").hidden,
  opened_on: f.opened_on || null,
  deposits_before: f.kind in DEPOSITS_WHY ? cleanDecimal(f.deposits_before) ?? "0" : "0",
}));

bindDialogForm("form-asset", "dlg-asset", "/api/assets", "Actif", (f) => ({
  name: f.name,
  asset_class: f.asset_class,
  isin: orNull(f.isin),
  ticker: orNull(f.ticker),
  coingecko_id: orNull(f.coingecko_id),
  currency: f.currency,
  color: f.color ? Number(f.color) : null,
  ter: cleanDecimal(f.ter),
  category: orNull(f.category),
}), true);

// Compte ou actif avec des opérations (D-074) : une fenêtre d'avertissement demande d'abord
// de les déplacer vers un autre compte ou actif de même devise, ou de les supprimer aussi.
// Le serveur fait une sauvegarde de la base juste avant.
let deleting = null;

function openDelete(path, item, label, noun) {
  const isAccount = path === "/api/accounts";
  const ops = lists.operations.filter((op) => (isAccount ? op.account_id : op.asset_id) === item.id);
  // Transferts reçus d'un autre compte (D-085) : comptés à part, ils restent au départ.
  const received = isAccount ? lists.operations.filter((op) => op.to_account_id === item.id) : [];
  const plans = recurring.items.filter((r) => (isAccount ? r.account_id : r.asset_id) === item.id).length;
  deleting = { path, item, label, noun, count: ops.length };
  const form = $("form-delete");
  form.reset();
  const all = [...ops, ...received];
  const dates = all.map((op) => op.op_date).sort();
  const n = new Set(ops.map((op) => op.account_id)).size;
  const plural = (k, word) => `${k} ${word}${k > 1 ? "s" : ""}`;
  const counted = [ops.length && plural(ops.length, "opération"), received.length && `${plural(received.length, "transfert")} reçu${received.length > 1 ? "s" : ""}`].filter(Boolean).join(" et ");
  const where = isAccount ? "" : ` dans ${n} compte${n > 1 ? "s" : ""}`;
  const when = dates[0] === dates.at(-1) ? `le ${frDate(dates[0])}` : `du ${frDate(dates[0])} au ${frDate(dates.at(-1))}`;
  $("dlg-delete-title").textContent = `Supprimer « ${label} »`;
  $("delete-warning").textContent = `Attention : ${isAccount ? "ce compte" : "cet actif"} a ${counted}${where}, ${when}${plans ? `, et ${plans} achat(s) programmé(s)` : ""}. Sa suppression est définitive : choisis ce que deviennent ses opérations.`;
  const others = (isAccount ? lists.accounts : lists.assets).filter((x) => x.id !== item.id && x.currency === item.currency);
  form.vers.replaceChildren(...others.map((x) => new Option(isAccount ? x.name : assetLabel(x), x.id)));
  const move = form.querySelector('input[value="deplacer"]');
  move.disabled = form.vers.disabled = !others.length;
  $("delete-move-text").textContent = others.length
    ? `Les déplacer${plans ? ", avec les achats programmés," : ""} vers`
    : `Les déplacer : aucun autre ${isAccount ? "compte" : "actif"} en ${item.currency}`;
  const kept = received.length ? `${ops.length ? ", " : ""}transferts reçus gardés en « Hors de l'app »` : "";
  $("delete-drop-text").textContent = `Les supprimer aussi (${ops.length ? plural(ops.length, "opération") : ""}${plans ? ` et ${plans} achat(s) programmé(s)` : ""}${kept})`;
  $("delete-note").textContent = `${isAccount ? "Les positions, les espèces et l'historique" : "Les positions et l'historique"} sont recalculés. Une sauvegarde de la base est faite juste avant (dossier sauvegardes des données).`;
  syncDeleteForm();
  $("dlg-delete").showModal();
}

function syncDeleteForm() {
  const form = $("form-delete");
  const mode = form.mode.value;
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = !mode;
  const { label, count } = deleting;
  submit.textContent = mode === "deplacer"
    ? `Déplacer puis supprimer « ${label} »`
    : mode === "supprimer" ? `Supprimer « ${label} »${count ? ` et ${count} opération${count > 1 ? "s" : ""}` : ""}` : "Supprimer";
}
$("form-delete").addEventListener("change", syncDeleteForm);
$("form-delete").querySelector("[data-cancel]").addEventListener("click", () => $("dlg-delete").close());
$("form-delete").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const { path, item, noun } = deleting;
  const params = new URLSearchParams({ operations: form.mode.value });
  if (form.mode.value === "deplacer") params.set("vers", form.vers.value);
  try {
    await api(`${path}/${item.id}?${params}`, { method: "DELETE" });
    $("dlg-delete").close();
    showMessage(form.mode.value === "deplacer" ? `${noun} supprimé, opérations déplacées.` : `${noun} et opérations supprimés.`);
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
});

async function deleteItem(path, item, label, noun) {
  const isAccount = path === "/api/accounts";
  const used = lists.operations.some((op) => (isAccount ? op.account_id : op.asset_id) === item.id
    || (isAccount && op.to_account_id === item.id));
  if (used) return openDelete(path, item, label, noun);
  if (!await askConfirm(`Supprimer « ${label} » ?`)) return;
  try {
    await api(`${path}/${item.id}`, { method: "DELETE" });
    showMessage(`${noun} supprimé.`);
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

document.querySelector('[data-add="account"]').addEventListener("click", () => openAccount());
document.querySelector('[data-add="asset"]').addEventListener("click", () => openAsset());
// Croix ✕ et clic sur le fond sombre : ferment la fenêtre (Échap aussi, nativement). L'import
// ne se ferme qu'avec la croix ou Échap : un clic à côté ferait perdre l'aperçu et ses réglages.
for (const dialog of document.querySelectorAll("dialog.form-dialog")) {
  for (const close of dialog.querySelectorAll("[data-close]")) close.addEventListener("click", () => dialog.close());
  if (dialog.id === "dlg-import") continue;
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });
}

makeReorderable($("accounts-list"), "tr[data-id]", ".drag-handle", (rows, moved) => saveOrder("accounts", rows, moved));
makeReorderable($("assets-list"), "tr[data-id]", ".drag-handle", (rows, moved) => saveOrder("assets", rows, moved));
bindSortable("income-lines-table", ["total", "last_12m", "yield_pct", "withheld"]);
bindSortable("income-calendar-table", [...Array.from({ length: 12 }, (_, i) => `m${i}`), "total"]);
bindSortable("income-years-table", ["year", "dividends", "fees"]);
bindSortable("placements-table", ["capital", "rate_today_pct", "realized_pct", "income_12m", "estimated", "current_value"]);
bindSortable("idle-table", ["held", "value", "share_pct"]);
bindSortable("rewards-recent-table", ["quantity", "value"]);
bindSortable("ter-lines-table", ["value", "ter", "first_year", "over_years"]);
bindSortable("target-table", ["value", "weight_pct", "target", "current_pct", "target_pct", "gap", "buy"]);
bindSortable("tax-pea-table", ["deposits", "used_pct", "remaining"]);
bindSortable("tax-av-table", ["remaining", "value", "gains", "tax_free", "exit_tax"]);
bindSortable("tax-av-buybacks-table", ["amount", "gain"]);
bindSortable("tax-per-table", ["deposits", "deposits_year", "value", "gains", "tax", "net"]);
bindSortable("tax-cto-table", ["gains", "losses", "dividends", "tax"]);
bindSortable("tax-sales-table", ["proceeds", "cost", "gain_eur"]);
bindSortable("tax-pea-sales-table", ["proceeds", "cost", "gain_eur"]);
bindSortable("tax-crypto-table", ["portfolio_value", "gross", "fees", "acquisition", "fraction", "gain"]);
bindSortable("tax-foreign-table", []);

async function checkHealth() {
  // Le point n'apparaît qu'en cas de problème (serveur arrêté page ouverte, base illisible) :
  // rouge, texte au survol. Vérifié toutes les minutes.
  const el = $("health");
  let text = "";
  try {
    const data = await api("/api/health");
    if (data.status !== "ok") text = `Serveur ${data.status}`;
    // Numéro de l'app (D-121), à citer dans un signalement ; suit une mise à jour sans recharger.
    if (data.version) $("app-version").textContent = `Version ${data.version}`;
  } catch (err) {
    text = "Serveur injoignable";
  }
  el.hidden = !text;
  el.className = "status-dot ko";
  el.title = text;
  el.firstElementChild.textContent = text;
}
// « Chercher le ticker » (D-083) : table locale, sinon Yahoo, à partir de l'ISIN saisi ; les
// cotations dans la devise du formulaire d'abord. Un clic sur l'une remplit ticker et devise.
$("asset-find-ticker").addEventListener("click", async (event) => {
  const form = $("form-asset");
  const isin = form.isin.value.trim().toUpperCase();
  const box = $("asset-ticker-choices");
  if (!/^[A-Z]{2}[A-Z0-9]{9}[0-9]$/.test(isin)) {
    showMessage("Saisis d'abord l'ISIN du titre (12 caractères, ex. FR0011871128).", true);
    return;
  }
  const currency = form.currency.value.trim().toUpperCase() || "EUR";
  event.target.disabled = true;
  try {
    const data = await api(`/api/tickers?${new URLSearchParams({ isin, currency })}`);
    const label = document.createElement("span");
    label.className = "muted small";
    label.textContent = data.quotes.length ? "Cotations trouvées :" : "Aucune cotation trouvée chez Yahoo : saisis le ticker à la main.";
    box.replaceChildren(label, ...data.quotes.map((q) => zoomChip(
      `${q.ticker} · ${q.currency}${q.price ? ` · ${formatDecimal(q.price, q.currency)}` : ""}`,
      q.ticker === form.ticker.value.trim().toUpperCase(),
      () => {
        form.ticker.value = q.ticker;
        form.currency.value = q.currency;
        if (!form.name.value.trim() && q.name) form.name.value = q.name;
        box.hidden = true;
      },
    )));
    box.hidden = false;
  } catch (err) {
    showMessage(err.message, true);
  } finally {
    event.target.disabled = false;
  }
});

// « Tester le cours » : interroge la source avec le ticker saisi, sans rien enregistrer.
for (const button of document.querySelectorAll("[data-test-quote]")) {
  button.addEventListener("click", async () => {
    const f = button.form.elements;
    const params = new URLSearchParams();
    if (orNull(f.coingecko_id.value)) params.set("coingecko_id", f.coingecko_id.value.trim());
    else if (orNull(f.ticker.value)) params.set("ticker", f.ticker.value.trim());
    params.set("currency", f.currency.value.trim() || "EUR");
    try {
      const q = await api(`/api/quote?${params}`);
      showMessage(`Cours trouvé : ${formatDecimal(q.price, q.currency)} (${SOURCES[q.source] ?? q.source}).`);
    } catch (err) {
      showMessage(err.message, true);
    }
  });
}

$("refresh-prices").addEventListener("click", async () => {
  const button = $("refresh-prices"); // event.target peut être la flèche à l'intérieur
  button.disabled = true;
  try {
    await refresh(true);
  } catch (err) {
    showMessage(err.message, true);
  } finally {
    button.disabled = false;
  }
});
