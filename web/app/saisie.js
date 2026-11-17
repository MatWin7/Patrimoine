// --- Formulaire d'opération (ajout dans « Gérer », correction dans une fenêtre) -----------

const OP_HINTS = {
  achat: "Débite les espèces du compte : quantité × prix + frais.",
  vente: "Crédite les espèces du compte : quantité × prix − frais.",
  versement: "Argent déposé sur le compte : ajouté à ses espèces.",
  retrait: "Argent retiré du compte : déduit de ses espèces. Vers un autre de tes comptes de l'app : il y arrive en versement.",
  dividende: "Dividende ou coupon reçu (montant net crédité), ajouté aux espèces du compte. Visible dans l'onglet Revenus et frais.",
  frais: "Droits de garde, frais de gestion... débités des espèces du compte. Comptés dans l'onglet Revenus et frais.",
  transfert_sortant:
    "Titres ou crypto envoyés hors de ce compte (ton wallet, un autre courtier) : la quantité baisse sans vente ni espèces. Vers un autre de tes comptes de l'app : ils y arrivent au même prix de revient (ni gain ni perte). Prix = valeur unitaire ce jour-là, pour la performance. Vendus ailleurs ? Saisis plutôt une vente.",
  solde_initial:
    "Titres détenus avant le suivi, sans toucher aux espèces. Saisis ton PRU si tu l'as ; sinon le montant actuel de la ligne : le PRU devient montant ÷ quantité et la plus-value part de zéro à cette date.",
};
// Actif à valeur saisie (fonds euros, bien immobilier...) : un montant, pas de quantité (D-056).
const VALUED_HINTS = {
  achat: "Montant investi, débité des espèces du compte. Mets ensuite à jour la valeur de la ligne (Gérer > Actifs > Valeur).",
  vente: "Montant retiré de la ligne (rachat, vente), crédité aux espèces du compte.",
  solde_initial: "Ce que tu as investi dans cette ligne avant le suivi (prix d'achat du bien, versements...), sans toucher aux espèces. Saisis ensuite sa valeur actuelle avec le bouton « Valeur ».",
};

function fillOperationSelects(form) {
  fillSelect(form.account_id, lists.accounts, (a) => a.name);
  fillAssetSelect(form.asset_id);
  delete form.dataset.assetsFor; // liste d'actifs refaite : le filtre du compte se réapplique
  syncOperationForm(form); // devise de l'actif choisi
}

// Types d'opération proposés selon le compte (le compte se choisit en premier) : un livret ne
// reçoit que des versements et retraits, une crypto ne verse pas de dividende ; les autres
// comptes gardent tout. Le type déjà choisi (modification d'une opération) reste affiché.
const TYPES_BY_KIND = {
  livret: ["versement", "retrait", "frais"],
  crypto: ["achat", "vente", "versement", "retrait", "solde_initial", "transfert_sortant", "frais"],
};

function restrictTypes(form, reset = false) {
  const account = lists.accounts.find((a) => String(a.id) === form.account_id.value);
  let allowed = TYPES_BY_KIND[account?.kind] ?? null;
  // Portefeuille personnel (D-104) : pas d'euros, donc ni versement ni retrait.
  if (allowed && account.custody === "wallet") allowed = allowed.filter((t) => t !== "versement" && t !== "retrait");
  const options = [...form.op_type.options];
  if (reset && allowed && !allowed.includes(form.op_type.value)) form.op_type.value = allowed[0];
  for (const o of options) o.hidden = o.disabled = !!allowed && !allowed.includes(o.value) && o.value !== form.op_type.value;
}

// Actifs proposés selon le compte, pour une nouvelle opération : une crypto sur un compte
// crypto, actions/ETF/fonds sur un PEA... Restent toujours proposés les actifs déjà utilisés
// sur ce compte et l'actif choisi. Livret, Autre : tous. En modification : tous.
const CLASSES_BY_KIND = {
  crypto: ["crypto"],
  pea: ["action", "etf", "fonds"],
  cto: ["action", "etf", "fonds", "obligation", "or", "scpi", "autre"],
  assurance_vie: ["action", "etf", "fonds", "fonds_euros", "obligation", "scpi", "or", "autre"],
  per: ["action", "etf", "fonds", "fonds_euros", "obligation", "scpi", "or", "autre"],
  immobilier: ["immobilier", "scpi", "autre"],
};

function restrictAssets(form) {
  if (form.dataset.id) return;
  const account = lists.accounts.find((a) => String(a.id) === form.account_id.value);
  const classes = CLASSES_BY_KIND[account?.kind];
  const key = account ? String(account.id) : "";
  if (form.dataset.assetsFor === key) return;
  form.dataset.assetsFor = key;
  const used = new Set(lists.operations.filter((op) => op.account_id === account?.id).map((op) => op.asset_id));
  const current = form.asset_id.value;
  const keep = (a) => !classes || classes.includes(a.asset_class) || used.has(a.id) || String(a.id) === current;
  const held = lists.assets.filter((a) => !a.sold_out && keep(a));
  const sold = lists.assets.filter((a) => a.sold_out && keep(a));
  const group = document.createElement("optgroup");
  group.label = "Soldés";
  group.append(...sold.map((a) => new Option(assetLabel(a), a.id)));
  form.asset_id.replaceChildren(...held.map((a) => new Option(assetLabel(a), a.id)), ...(sold.length ? [group] : []));
  if ([...form.asset_id.querySelectorAll("option")].some((o) => o.value === current)) form.asset_id.value = current;
}

// Champs affichés selon le type : titres (actif, quantité, prix, frais), titres déjà détenus
// (quantité, puis PRU ou montant actuel) ou montant seul.
const OPTIONAL_FIELDS = { initial: ["unit_price", "line_value"] }; // l'un ou l'autre, vérifié par le serveur

function syncOperationForm(form) {
  restrictTypes(form);
  restrictAssets(form);
  const type = form.op_type.value;
  const asset = lists.assets.find((a) => String(a.id) === form.asset_id.value);
  const amountOnly = CASH_TYPES.includes(type) || type === "frais";
  const valued = !amountOnly && type !== "dividende" && isManual(asset);
  const want = amountOnly ? "cash" : type === "dividende" ? "income" : valued ? "valued" : type === "solde_initial" ? "initial" : "trade";
  const optional = ["fees", "offered", "tax_withheld", ...(OPTIONAL_FIELDS[want] ?? [])];
  for (const label of form.querySelectorAll("[data-for]")) {
    const on = label.dataset.for.split(" ").includes(want) && (!label.hasAttribute("data-buy-only") || type === "achat");
    label.hidden = !on;
    for (const input of label.querySelectorAll("input, select")) {
      input.disabled = !on; // un champ désactivé n'est pas envoyé
      input.required = on && !optional.includes(input.name);
    }
  }
  form.querySelector("[data-price-label]").textContent = want === "initial" ? "ou ton PRU, si tu l'as" : "Prix unitaire";
  // Montant et PRU dans la devise de cotation de l'actif (ex. USD pour une crypto cotée en dollars).
  form.querySelector("[data-asset-cur]").textContent = asset ? `, en ${asset.currency}` : "";
  form.querySelector("[data-amount-label]").textContent = valued || want === "income"
    ? `${type === "solde_initial" ? "Montant investi" : "Montant"}${asset ? `, en ${asset.currency}` : ""}`
    : "Montant";
  form.querySelector("[data-hint]").textContent = (valued ? VALUED_HINTS[type] : OP_HINTS[type]) ?? "";
  // Vente d'un compte crypto (D-066) : échange non imposable, valeur globale du portefeuille.
  const account = lists.accounts.find((a) => String(a.id) === form.account_id.value);
  const cryptoSale = type === "vente" && account?.kind === "crypto" && !valued;
  for (const label of form.querySelectorAll("[data-crypto-sale]")) {
    const on = cryptoSale && !(label.hasAttribute("data-crypto-value") && form.crypto_swap.checked);
    label.hidden = !on;
    for (const input of label.querySelectorAll("input")) input.disabled = !on;
  }
  // Achat de crypto (D-092) : type de récompense, si une part est offerte (onglet Rendements crypto).
  const cryptoBuy = type === "achat" && !valued && (account?.kind === "crypto" || asset?.asset_class === "crypto");
  for (const label of form.querySelectorAll("[data-crypto-reward]")) {
    label.hidden = !cryptoBuy;
    for (const input of label.querySelectorAll("select")) input.disabled = !cryptoBuy;
  }
  // Transfert sortant ou retrait (D-085) : vers un autre compte de l'app, ou hors de l'app.
  const transfer = type === "transfert_sortant" || type === "retrait";
  const to = form.to_account_id;
  const chosen = to.value;
  to.replaceChildren(new Option("Hors de l'app", ""), ...lists.accounts
    .filter((a) => String(a.id) !== form.account_id.value)
    .map((a) => new Option(a.name, a.id)));
  to.value = [...to.options].some((o) => o.value === chosen) ? chosen : "";
  for (const label of form.querySelectorAll("[data-transfer]")) {
    const on = transfer && !(label.hasAttribute("data-transfer-text") && to.value);
    label.hidden = !on;
    for (const input of label.querySelectorAll("input, select")) input.disabled = !on;
  }
}

// Destination d'un transfert sortant ou d'un retrait (D-085), absente ailleurs.
function transferBody(f) {
  return { to_account_id: f.to_account_id ? Number(f.to_account_id) : null, destination: orNull(f.destination ?? "") };
}

function operationBody(form) {
  const f = Object.fromEntries(new FormData(form));
  const body = { account_id: Number(f.account_id), op_type: f.op_type, op_date: f.op_date, note: orNull(f.note) };
  if (CASH_TYPES.includes(f.op_type) || f.op_type === "frais") return { ...body, amount: cleanDecimal(f.amount), ...transferBody(f) };
  if (f.op_type === "dividende") return { ...body, asset_id: Number(f.asset_id), amount: cleanDecimal(f.amount), tax_withheld: cleanDecimal(f.tax_withheld ?? "") };
  if (isManual(lists.assets.find((a) => String(a.id) === f.asset_id))) {
    // Valeur saisie : le serveur convertit le montant en parts au dernier prix connu.
    return { ...body, asset_id: Number(f.asset_id), amount: cleanDecimal(f.amount), fees: cleanDecimal(f.fees ?? "") ?? "0" };
  }
  if (f.op_type === "solde_initial") {
    // PRU ou montant actuel : le serveur calcule le PRU en Decimal (montant ÷ quantité).
    if (!cleanDecimal(f.unit_price ?? "") && !cleanDecimal(f.line_value ?? "")) {
      throw new Error("Indique le montant actuel de la ligne, ou ton PRU si tu l'as.");
    }
    return {
      ...body,
      asset_id: Number(f.asset_id),
      quantity: cleanDecimal(f.quantity),
      unit_price: cleanDecimal(f.unit_price ?? ""),
      amount: cleanDecimal(f.line_value ?? ""),
    };
  }
  return {
    ...body,
    asset_id: Number(f.asset_id),
    quantity: cleanDecimal(f.quantity),
    unit_price: cleanDecimal(f.unit_price),
    fees: cleanDecimal(f.fees ?? "") ?? "0",
    offered: cleanDecimal(f.offered ?? ""), // part payée par la plateforme (D-076), achat seulement
    reward_kind: f.reward_kind || null, // achat de crypto offert (D-092)
    crypto_swap: f.crypto_swap === "on", // champs désactivés hors vente crypto : absents
    portfolio_value: cleanDecimal(f.portfolio_value ?? ""),
    ...transferBody(f),
  };
}

// Crée une copie du formulaire dans `container` ; `onSaved` reçoit l'opération enregistrée.
function operationForm(container, onSaved) {
  const form = $("tpl-operation").content.firstElementChild.cloneNode(true);
  container.replaceChildren(form);
  fillOperationSelects(form);
  form.op_date.value = isoDate(new Date());
  form.op_type.addEventListener("change", () => syncOperationForm(form));
  form.asset_id.addEventListener("change", () => syncOperationForm(form));
  form.account_id.addEventListener("change", () => {
    restrictTypes(form, true); // le compte change : le type doit rester permis
    syncOperationForm(form);
  });
  form.crypto_swap.addEventListener("change", () => syncOperationForm(form));
  form.to_account_id.addEventListener("change", () => syncOperationForm(form));
  syncOperationForm(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = form.dataset.id;
    try {
      const op = await api(id ? `/api/operations/${id}` : "/api/operations", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(operationBody(form)),
      });
      await onSaved(op, form);
    } catch (err) {
      showMessage(err.message, true);
    }
  });
  return form;
}

// Carte « Ajouter des opérations » (D-135) : saisie une par une ou import d'un relevé, un
// panneau à la fois ; le dernier choix est gardé dans ce navigateur.
const OPERATION_MODE_KEY = PREF + "ajout-operations";
function showOperationMode(mode, keep = false) {
  const chosen = mode === "import" ? "import" : "saisie";
  for (const panel of document.querySelectorAll("[data-operation-mode]")) panel.hidden = panel.dataset.operationMode !== chosen;
  for (const button of $("operation-mode").querySelectorAll("button")) {
    const active = button.dataset.mode === chosen;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  if (keep) savePref(OPERATION_MODE_KEY, { mode: chosen });
}
$("operation-mode").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-mode]");
  if (button) showOperationMode(button.dataset.mode, true);
});
// Le tutoriel, la vérification d'un compte et le bouton « Nouvelle opération » de Positions
// ouvrent le bon panneau sans changer le choix gardé.
document.addEventListener("operationmode", (e) => showOperationMode(e.detail));
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-operation-open]");
  if (link) showOperationMode(link.dataset.operationOpen);
});
showOperationMode(loadPref(OPERATION_MODE_KEY, { mode: "saisie" }).mode);

const newOperation = operationForm($("new-operation"), async (op, form) => {
  const agree = { vente: "e", solde_initial: "s" }[op.op_type] ?? ""; // « Vente enregistrée »
  showMessage(`${TYPES_FULL[op.op_type]} enregistré${agree}.`);
  // Compte, type et date restent : pratique pour saisir plusieurs opérations à la suite.
  for (const name of ["quantity", "unit_price", "line_value", "fees", "amount", "note", "portfolio_value", "offered", "tax_withheld", "destination"]) newOperation.elements[name].value = "";
  newOperation.crypto_swap.checked = false;
  newOperation.reward_kind.value = "";
  syncOperationForm(newOperation);
  await refresh();
});

const editOperation = operationForm($("edit-operation"), async () => {
  $("dlg-operation").close();
  showMessage("Opération modifiée.");
  await refresh();
});

function openOperationEdit(op) {
  const form = editOperation;
  form.dataset.id = op.id;
  fillOperationSelects(form);
  for (const name of ["op_type", "account_id", "asset_id", "op_date", "note"]) form.elements[name].value = op[name] ?? "";
  // Nombres affichés à la française (virgule), comme on les saisit.
  for (const name of ["quantity", "unit_price", "fees", "amount", "portfolio_value", "offered", "tax_withheld"]) form.elements[name].value = op[name]?.replace(".", ",") ?? "";
  form.crypto_swap.checked = Boolean(op.crypto_swap);
  form.reward_kind.value = op.reward_kind ?? "";
  if (isManual(lists.assets.find((a) => a.id === op.asset_id)) && op.gross) form.amount.value = op.gross.replace(".", ",");
  form.line_value.value = ""; // le PRU enregistré suffit
  form.destination.value = op.destination ?? "";
  syncOperationForm(form); // comptes de destination selon le compte de départ
  form.to_account_id.value = op.to_account_id ?? "";
  syncOperationForm(form);
  $("dlg-operation").showModal();
}

async function deleteOperation(op) {
  if (!await askConfirm(`Supprimer l'opération du ${frDate(op.op_date)} ?`)) return;
  try {
    await api(`/api/operations/${op.id}`, { method: "DELETE" });
    showMessage("Opération supprimée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

// --- Comptes et actifs : une fenêtre pour ajouter ou modifier ----------------------------

function fillForm(form, item, defaults) {
  for (const input of form.elements) {
    if (!input.name) continue;
    const value = item ? item[input.name] : defaults[input.name];
    if (input.type === "checkbox") { input.checked = Boolean(value); continue; }
    input.value = value === null || value === undefined ? "" : String(value);
  }
}

// Champs utiles selon la classe d'actif ou le type de compte : `data-for="etf action"`.
// Un champ rempli en base reste visible même s'il ne sert plus, pour ne rien effacer en
// silence ; un champ masqué est remis à vide (devise : EUR) avant l'envoi.
function syncFieldsFor(form, value, stored) {
  for (const el of form.querySelectorAll("[data-for]")) {
    const input = el.querySelector("input, select");
    const saved = input && stored ? stored[input.name] : null;
    const keep = saved !== null && saved !== undefined && saved !== "" && !(input.name === "currency" && saved === "EUR");
    el.hidden = !el.dataset.for.split(" ").includes(value) && !keep;
    if (el.hidden && input) input.value = input.name === "currency" ? "EUR" : "";
  }
}

// `preset` : champs pré-remplis d'un nouveau compte (partie d'un relevé importé, D-071).
function openAccount(account = null, preset = {}) {
  const form = $("form-account");
  form.dataset.id = account?.id ?? "";
  fillForm(form, account, { kind: "pea", currency: "EUR", ...preset });
  if (account) form.cash.value = account.cash.replace(".", ",");
  delete form.abroad.dataset.touched;
  if (!account) guessCustody(form);
  form.deposits_before.value = account && account.deposits_before !== "0" ? account.deposits_before.replace(".", ",") : "";
  syncAccountForm();
  $("dlg-account-title").textContent = account ? `Modifier « ${account.name} »` : "Ajouter un compte";
  $("dlg-account").showModal();
}

// Champ d'espèces selon le type de compte (D-075). PEA, assurance-vie, PER : « Versements »,
// indispensable à leur fiscalité, espèces déduites (D-062). Livret, CTO, crypto, Autre : les
// espèces lues chez la banque, puis les opérations suivantes. Immobilier : aucun des deux.
const CASH_ENTERED = ["livret", "cto", "crypto", "autre"];
const DEPOSITS_WHY = {
  pea: "Versements : le total versé sur le PEA (ta banque l'affiche). Il sert au plafond de 150 000 € et à l'impôt au retrait (gain = valeur − versements).",
  assurance_vie: "Versements : le total des primes versées. L'impôt d'un rachat porte sur la part de gains (valeur − versements).",
  per: "Versements : le total versé sur le PER. Ceux de l'année sont déductibles ; à la sortie, versements et gains sont imposés différemment.",
};

// Noms proposés dans « Établissement » selon la garde, et devinette de la garde et de « à
// l'étranger » d'après le nom et l'établissement (D-104). Mêmes listes que app/custody.py.
const CUSTODY_NAMES = {
  exchange: ["Binance", "Kraken", "Coinbase", "Bitpanda", "Bitstamp", "Bybit", "OKX", "KuCoin", "Crypto.com", "Gemini", "Bitget", "Bitvavo", "Paymium", "Coinhouse", "Revolut", "Trade Republic"],
  wallet: ["Ledger", "Trezor", "MetaMask", "Rabby", "Phantom", "Trust Wallet", "Exodus", "Rainbow", "Coinbase Wallet", "Tangem", "Keystone", "BitBox", "Electrum", "Sparrow"],
};
const ABROAD_NAMES = ["binance", "kraken", "coinbase", "bitpanda", "bitstamp", "bybit", "okx", "kucoin", "crypto.com", "gemini", "bitget", "bitvavo", "revolut", "trade republic", "degiro", "interactive brokers", "scalable", "n26", "saxo", "freedom24"];

function guessCustody(form) {
  const text = `${form.name.value} ${form.institution.value}`.toLowerCase();
  const has = (names) => names.some((n) => text.includes(n.toLowerCase()));
  if (form.kind.value === "crypto" && !form.custody.value) {
    form.custody.value = has([...CUSTODY_NAMES.wallet, "aave"]) ? "wallet" : has(CUSTODY_NAMES.exchange) ? "exchange" : "";
  }
  if (!form.dataset.id && !form.abroad.dataset.touched) form.abroad.checked = has(ABROAD_NAMES);
}

function syncAccountForm() {
  const form = $("form-account");
  const kind = form.kind.value;
  const stored = lists.accounts.find((a) => String(a.id) === form.dataset.id);
  syncFieldsFor(form, kind, stored);
  const wallet = kind === "crypto" && form.custody.value === "wallet";
  const names = kind === "crypto" ? CUSTODY_NAMES[form.custody.value] ?? [...CUSTODY_NAMES.exchange, ...CUSTODY_NAMES.wallet] : [];
  $("account-institutions").replaceChildren(...names.map((n) => new Option(n)));
  $("account-abroad").hidden = wallet || kind === "immobilier"; // rien à déclarer
  // Un portefeuille personnel ne contient pas d'euros : pas de champ Espèces, sauf solde déjà saisi.
  const entered = CASH_ENTERED.includes(kind) && !(wallet && !(stored && Number(stored.cash)));
  const deposits = kind in DEPOSITS_WHY;
  for (const el of form.querySelectorAll("[data-kind]")) el.hidden = el.dataset.kind === "cash" ? !entered : !deposits;
  $("account-deposits-why").textContent = DEPOSITS_WHY[kind] ?? "";
  $("account-cash-label").textContent = kind === "livret" ? "Solde du livret aujourd'hui" : "Espèces aujourd'hui";
  $("account-cash-help").textContent = kind === "livret"
    ? "Ce solde devient la référence ; les versements et retraits enregistrés ensuite le feront bouger."
    : "Les espèces disponibles sur le compte, comme ta banque les affiche (« solde espèces », « liquidités ») ; 0 si tout est investi, ou si la plateforme mélange espèces et compte courant (Trade Republic). Les opérations enregistrées ensuite le font bouger ; un achat sans assez d'espèces compte un versement supposé.";
  const account = lists.accounts.find((a) => String(a.id) === form.dataset.id);
  // Compte d'avant D-075 : espèces encore déduites de l'ancien champ, à confirmer une fois.
  const legacy = account && entered && kind !== "livret" && !account.cash_date;
  $("account-cash-warn").hidden = !legacy;
  if (legacy) $("account-cash-warn").textContent = "À vérifier : ces espèces sont encore calculées avec l'ancien champ « Versements ». Compare-les avec ta banque, corrige si besoin, puis enregistre : elles deviendront la référence.";
  // Anti-doublon : le total de la banque contient déjà les versements saisis en opérations.
  const entered_ops = account && deposits ? account.deposits_entered : "0";
  const warn = $("account-deposits-warn");
  warn.hidden = Number(entered_ops) === 0;
  if (!warn.hidden) {
    warn.textContent = `Attention : ${formatDecimal(entered_ops, account.currency)} d'opérations « Versement » sont déjà saisies sur ce compte et s'ajoutent à ce champ. Si tu reportes le total affiché par ta banque, retire-les de ce chiffre pour ne pas les compter deux fois.`;
  }
}
$("form-account").kind.addEventListener("change", () => { guessCustody($("form-account")); syncAccountForm(); });
$("form-account").custody.addEventListener("change", syncAccountForm);
$("form-account").abroad.addEventListener("change", (e) => { e.target.dataset.touched = "1"; });
for (const name of ["name", "institution"]) {
  $("form-account")[name].addEventListener("change", () => { guessCustody($("form-account")); syncAccountForm(); });
}
