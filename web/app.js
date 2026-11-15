// Page de saisie : comptes, actifs, opérations, positions et total au cours en ligne.
// Les montants arrivent de l'API en chaînes ("1234.56") et ne passent jamais par un
// nombre JavaScript (float) : formatDecimal les met en forme à partir du texte.

const TYPES = { achat: "Achat", vente: "Vente", solde_initial: "Titres déjà détenus", transfert_sortant: "Transfert sortant" };

const $ = (id) => document.getElementById(id);

// Réglages gardés dans le navigateur : rangés par profil (D-098), posé par profiles.js avant le
// chargement de cette page. Le profil « Principal » garde les clés d'avant les profils.
const PREF = window.patrimoinePrefPrefix ?? "patrimoine.";

// "12345.6" -> "12 345,6" (espace fine insécable pour les milliers, virgule décimale).
function formatDecimal(value, currency) {
  if (value === null || value === undefined) return "";
  const negative = value.startsWith("-");
  const [int, frac] = value.replace("-", "").split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  const text = (negative ? "-" : "") + grouped + (frac ? "," + frac : "");
  return currency ? `${text} ${currency === "EUR" ? "€" : currency}` : text;
}

// Arrondit une chaîne décimale à `places` chiffres (au plus proche), sans passer par un
// float ; les zéros finaux au-delà du centime sont retirés : "12.345678" -> "12.3457".
function roundDecimal(value, places = 4) {
  if (value === null || value === undefined) return value;
  const negative = value.startsWith("-");
  const [int, frac = ""] = value.replace("-", "").split(".");
  if (frac.length <= places) return value;
  const scaled = BigInt(int + frac.slice(0, places)) + (Number(frac[places]) >= 5 ? 1n : 0n);
  const digits = scaled.toString().padStart(places + 1, "0");
  const decimals = digits.slice(-places).replace(/0+$/, "").padEnd(2, "0");
  const sign = negative && /[1-9]/.test(digits) ? "-" : "";
  return `${sign}${digits.slice(0, -places)}.${decimals}`;
}

// Saisie française "1 234,5" -> "1234.5" ; les champs vides deviennent null.
function cleanDecimal(text) {
  const v = text.replace(/[\s ]/g, "").replace(",", ".");
  return v === "" ? null : v;
}

// "3.45" -> "+3,45 %" ; le signe aide à lire une variation d'un coup d'œil.
const signed = (value) => (value.startsWith("-") ? value : `+${value}`);
const formatPct = (value) => (value ? `${formatDecimal(signed(value))} %` : "");
// Couleur d'un chiffre dans une tuile (alignée à gauche, sans « + ») : vert, rouge, ou rien à zéro.
const toneClass = (value) => (/^-?0([.,]0+)?$/.test(value) ? "" : value.startsWith("-") ? "ko" : "ok");
const signClass = (value) => (value.startsWith("-") ? "num ko" : "num ok");

// Le message s'efface en fondu : 15 s pour une erreur (le temps de la lire), 5 s sinon.
const MESSAGE_DELAY = { error: 15000, ok: 5000 };
let messageTimer;

function showMessage(text, isError = false) {
  const el = $("message");
  el.querySelector("[data-text]").textContent = text;
  el.className = isError ? "toast ko" : "toast ok";
  el.hidden = false;
  syncToasts();
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => hideToast(el), isError ? MESSAGE_DELAY.error : MESSAGE_DELAY.ok);
}

// Le cadre des messages est un « popover » : il s'affiche dans la couche du dessus, comme les
// fenêtres. Le rouvrir le replace au-dessus d'une fenêtre ouverte entre-temps.
function syncToasts() {
  const box = $("toasts");
  if (!box.showPopover) return; // navigateur trop ancien : les messages restent dans la page
  if (box.matches(":popover-open")) box.hidePopover();
  if ([...box.children].some((el) => !el.hidden)) box.showPopover();
}

// Fondu (classe « fading », transition CSS) puis masquage.
function hideToast(el) {
  el.classList.add("fading");
  setTimeout(() => {
    if (!el.classList.contains("fading")) return; // un nouveau message est arrivé entre-temps
    el.hidden = true;
    syncToasts();
  }, 400);
}

$("message").querySelector("[data-close-toast]").addEventListener("click", () => {
  clearTimeout(messageTimer);
  $("message").hidden = true;
  syncToasts();
});

// Profils (D-098) : chaque appel annonce le profil que la page affiche. S'il a changé dans un
// autre onglet (409) ou s'est verrouillé (401), la page se recharge sur l'écran de choix.
function profileHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (window.patrimoineProfile) headers["X-Profile-Id"] = window.patrimoineProfile.id;
  return headers;
}

function checkProfile(res) {
  const flag = res.headers.get("X-Profile");
  if (flag !== "required" && flag !== "changed") return;
  location.reload();
  throw new Error(flag === "changed" ? "Profil changé dans un autre onglet." : "Profil verrouillé.");
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: profileHeaders(), ...options });
  checkProfile(res);
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) {
    // FastAPI renvoie soit un texte, soit une liste d'erreurs de validation.
    const detail = Array.isArray(data.detail)
      ? data.detail.map((e) => `${e.loc.at(-1)} : ${e.msg}`).join(" ; ")
      : data.detail;
    throw new Error(detail);
  }
  return data;
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text ?? "";
  if (className) td.className = className;
  return td;
}

function fillSelect(select, items, label) {
  const current = select.value;
  select.replaceChildren(
    ...items.map((item) => new Option(label(item), item.id)),
  );
  if (items.some((item) => String(item.id) === current)) select.value = current;
}

const SOURCES = { yahoo: "Yahoo Finance", coingecko: "CoinGecko", operation: "dernière opération", demo: "données fictives", manuel: "valeur saisie" };

// "2026-10-10T12:05:00Z" -> "14:05" (heure locale du navigateur) ; date ajoutée si ce n'est pas aujourd'hui.
function formatTime(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString()
    ? time
    : `${d.toLocaleDateString("fr-FR")} ${time}`;
}

// Infobulle et classe de la cellule « Cours » : d'où vient le prix et s'il y a eu un souci.
function priceCell(p) {
  const td = cell(formatDecimal(p.last_price, p.currency), "num");
  const from = SOURCES[p.price_source] ?? p.price_source;
  td.title = p.price_at ? `${from}, récupéré à ${formatTime(p.price_at)}` : `Prix de la ${from}`;
  if (p.manual) {
    // Valeur saisie (D-056) : la date de la dernière saisie plutôt qu'un cours unitaire.
    td.textContent = p.price_source === "manuel" ? `saisie le ${frDate(p.price_at.slice(0, 10))}` : "à saisir";
    td.title = p.price_source === "manuel" ? "Dernière valeur saisie dans « Gérer » > Actifs > Valeur" : "Aucune valeur saisie : valeur au prix d'achat";
    td.className = p.price_source === "manuel" ? "num muted" : "num warn";
    return td;
  }
  if (p.price_error) {
    td.title += ` (dernier essai en échec : ${p.price_error})`;
    td.classList.add("warn");
  }
  if (p.price_source === "operation") td.classList.add("warn");
  return td;
}

function showPricesStatus(portfolio) {
  const dates = portfolio.positions.filter((p) => !p.manual).map((p) => p.price_at).filter(Boolean).sort();
  const errors = portfolio.positions.filter((p) => p.price_error);
  const parts = [];
  if (dates.length) parts.push(`Cours du ${formatTime(dates[0])}`);
  if (errors.length) parts.push(`${errors.length} cours non mis à jour (survoler la ligne pour le détail)`);
  if (portfolio.fx_error) parts.push(portfolio.fx_error);
  const negative = portfolio.cash.filter((c) => c.cash.startsWith("-")).map((c) => c.account);
  if (negative.length) parts.push(`Espèces négatives (${negative.join(", ")}) : un versement manque sans doute`);
  parts.push(`actualisation automatique toutes les ${portfolio.price_ttl_minutes} min`);
  const el = $("prices-status");
  el.textContent = portfolio.positions.length || negative.length ? parts.join(" · ") : "";
  el.className = errors.length || portfolio.fx_error || negative.length ? "muted small warn" : "muted small";
}

let refreshTimer = null;
const TYPES_FULL = { ...TYPES, versement: "Versement", retrait: "Retrait", dividende: "Dividende", frais: "Frais", transfert_entrant: "Transfert reçu" };
const CASH_TYPES = ["versement", "retrait"];
const KINDS = { pea: "PEA", cto: "Compte-titres", crypto: "Crypto", assurance_vie: "Assurance-vie", per: "PER", livret: "Livret", immobilier: "Immobilier", autre: "Autre" };
// Garde d'un compte crypto (D-104) : libellé du type affiché dans les listes.
const CUSTODY_KINDS = { exchange: "Plateforme crypto", wallet: "Portefeuille crypto" };
function kindLabel(account) {
  return (account.kind === "crypto" && CUSTODY_KINDS[account.custody]) || KINDS[account.kind] || account.kind;
}
const CLASSES = {
  etf: "ETF", action: "Action", fonds: "Fonds", crypto: "Crypto", obligation: "Obligation", fonds_euros: "Fonds euros",
  scpi: "SCPI", or: "Or", immobilier: "Immobilier", autre: "Divers",
};
// Sans ticker ni id CoinGecko : valeur saisie à la main (D-056), montants plutôt que quantités.
const isManual = (a) => Boolean(a) && !a.ticker && !a.coingecko_id;
const lists = { accounts: [], assets: [], operations: [], debts: [] };
const assetLabel = (a) => `${a.name} (${a.ticker ?? a.coingecko_id ?? "valeur saisie"})`;

// --- Tri des tableaux ----------------------------------------------------------------
// Un clic sur un en-tête trie par cette colonne, un second inverse l'ordre ; le choix est
// gardé dans le navigateur. Number() sert seulement à comparer, jamais à calculer.
const SORT_KEY = PREF + "tri";
const sorts = loadPref(SORT_KEY, {
  "positions-table": { key: "value_eur", dir: "desc" },
  "operations-table": { key: "op_date", dir: "desc" },
});
const rerenders = {};

function sortRows(tableId, rows, numeric) {
  const { key, dir } = sorts[tableId];
  if (!key) return [...rows];
  const sign = dir === "asc" ? 1 : -1;
  const value = (r) => (numeric.includes(key) ? (r[key] === null || r[key] === undefined ? -Infinity : Number(r[key])) : String(r[key] ?? ""));
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    const order = typeof va === "string" ? va.localeCompare(vb, "fr") : va - vb;
    return order * sign || (a.id ?? 0) - (b.id ?? 0);
  });
}

// Ordre par défaut des tableaux des onglets Revenus, Cible et Fiscalité (avant tout clic) ;
// key null = l'ordre de l'API, ici celui des comptes choisi dans « Gérer » (D-070).
const SORT_DEFAULTS = {
  "income-lines-table": { key: "total", dir: "desc" },
  "income-years-table": { key: "year", dir: "desc" },
  "income-calendar-table": { key: "total", dir: "desc" },
  "tax-pea-sales-table": { key: "date", dir: "asc" },
  "ter-lines-table": { key: "value", dir: "desc" },
  "target-table": { key: "value", dir: "desc" },
  "tax-pea-table": { key: null, dir: "asc" },
  "tax-av-table": { key: null, dir: "asc" },
  "tax-per-table": { key: null, dir: "asc" },
  "tax-cto-table": { key: null, dir: "asc" },
  "tax-sales-table": { key: "date", dir: "asc" },
  "tax-crypto-table": { key: "date", dir: "asc" },
  "tax-foreign-table": { key: "account", dir: "asc" },
  "import-table": { key: "line", dir: "asc" },
  "watch-table": { key: null, dir: "asc" },
  "placements-table": { key: "estimated", dir: "desc" },
  "idle-table": { key: "value", dir: "desc" },
  "borrows-table": { key: "value_eur", dir: "desc" },
  "rewards-recent-table": { key: "date", dir: "desc" },
  "outbound-table": { key: null, dir: "asc" },
};
const sortNumeric = {};

function bindSortable(tableId, numeric) {
  const table = $(tableId);
  sorts[tableId] ??= SORT_DEFAULTS[tableId];
  sortNumeric[tableId] = numeric;
  const mark = () => {
    for (const th of table.querySelectorAll("th[data-sort]")) {
      if (th.dataset.sort === sorts[tableId].key) th.setAttribute("aria-sort", sorts[tableId].dir === "asc" ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    }
  };
  for (const th of table.querySelectorAll("th[data-sort]")) {
    th.querySelector("button").addEventListener("click", () => {
      const key = th.dataset.sort;
      const same = sorts[tableId].key === key;
      // Nouvelle colonne : montants et dates du plus grand au plus petit, textes de A à Z.
      const first = numeric.includes(key) || key.endsWith("date") ? "desc" : "asc";
      sorts[tableId] = { key, dir: same ? (sorts[tableId].dir === "asc" ? "desc" : "asc") : first };
      savePref(SORT_KEY, sorts);
      mark();
      rerenders[tableId]?.();
    });
  }
  mark();
}

// Remplit un tableau triable à partir de lignes de données, et garde ces données pour
// retrier au clic sans rappeler l'API. Au retri, les champs saisis (data-key) gardent leur valeur.
function fillSortable(tableId, rows, toRow, empty) {
  const render = (keep) => {
    const tbody = $(tableId).tBodies[0];
    const typed = new Map(keep ? [...tbody.querySelectorAll("input[data-key]")].map((i) => [i.dataset.key, i.value]) : []);
    const sorted = sortRows(tableId, rows, sortNumeric[tableId]);
    tbody.replaceChildren(...(sorted.length ? sorted.map(toRow) : [emptyRow(empty)]));
    for (const input of tbody.querySelectorAll("input[data-key]")) if (typed.has(input.dataset.key)) input.value = typed.get(input.dataset.key);
  };
  render(false);
  rerenders[tableId] = () => render(true);
}

// --- Questions posées par l'app (remplacent confirm() et prompt() du navigateur) -------------
// Une seule fenêtre « dlg-ask » : titre, détail facultatif, champ texte facultatif. Renvoie une
// promesse : true/false pour askConfirm, le texte saisi (ou null si annulé) pour askText.
function askDialog({ title, detail = "", input = false, value = "", placeholder = "", ok = "Confirmer", danger = false }) {
  return new Promise((resolve) => {
    const dlg = $("dlg-ask");
    const form = $("form-ask");
    $("ask-title").textContent = title;
    $("ask-detail").textContent = detail;
    $("ask-detail").hidden = !detail;
    const field = $("ask-input");
    field.hidden = !input;
    field.required = input;
    field.value = value;
    field.placeholder = placeholder;
    const button = $("ask-ok");
    button.textContent = ok;
    button.classList.toggle("danger", danger);
    button.classList.toggle("primary", !danger);
    let result = null;
    form.onsubmit = (e) => {
      e.preventDefault();
      result = input ? field.value : true;
      dlg.close();
    };
    dlg.addEventListener("close", () => resolve(result), { once: true });
    dlg.showModal();
    (input ? field : button).focus();
    if (input) field.select();
  });
}

// Confirmation : par défaut pour une suppression (bouton rouge) ; danger: false pour le reste.
const askConfirm = async (title, opts = {}) => (await askDialog({ danger: true, ok: "Supprimer", ...opts, title })) === true;
const askText = (title, opts = {}) => askDialog({ ...opts, title, input: true });

// --- Glisser-déposer (D-070) -------------------------------------------------------------
// Sans bibliothèque : les « Pointer Events » du navigateur couvrent la souris, le doigt et le
// stylet avec le même code (l'API « drag and drop » native, elle, ignore les écrans tactiles).
// Au clavier : la poignée prend le focus, les flèches déplacent l'élément d'un cran.
function makeReorderable(container, itemSelector, handleSelector, onChange) {
  const items = () => [...container.children].filter((el) => el.matches(itemSelector));
  const scroller = () => container.closest(".table-scroll");
  let drag = null;

  const finish = () => {
    if (!drag) return;
    const { item, before } = drag;
    drag = null;
    item.classList.remove("dragging");
    document.body.classList.remove("is-dragging");
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    const after = items();
    if (after.some((el, i) => el !== before[i])) onChange(after, item);
  };

  // Près d'un bord, la page (ou le tableau qui défile) suit le pointeur.
  const autoScroll = (y) => {
    const box = scroller();
    const r = box ? box.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
    const step = y < r.top + 40 ? -12 : y > r.bottom - 40 ? 12 : 0;
    if (step) (box ?? window).scrollBy(0, step);
  };

  function move(e) {
    if (!drag) return;
    autoScroll(e.clientY);
    const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(itemSelector);
    if (!over || over === drag.item || over.parentElement !== container) return;
    const list = items();
    if (list.indexOf(over) > list.indexOf(drag.item)) over.after(drag.item);
    else over.before(drag.item);
  }

  container.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(handleSelector);
    const item = handle?.closest(itemSelector);
    if (!item || item.parentElement !== container || e.button !== 0) return;
    e.preventDefault(); // pas de sélection de texte pendant le glissement
    drag = { item, before: items() };
    item.classList.add("dragging");
    document.body.classList.add("is-dragging");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  });

  container.addEventListener("keydown", (e) => {
    const step = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[e.key];
    const handle = e.target.closest(handleSelector);
    const item = handle?.closest(itemSelector);
    if (!step || !item || item.parentElement !== container) return;
    e.preventDefault();
    const list = items();
    const target = list[list.indexOf(item) + step];
    if (!target) return;
    if (step > 0) target.after(item);
    else target.before(item);
    handle.focus();
    onChange(items(), item);
  });
}

// Poignée en début de ligne d'un tableau réordonnable.
function gripCell(label) {
  const td = document.createElement("td");
  td.className = "grip";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "drag-handle";
  button.textContent = "⠿";
  button.title = "Glisser pour déplacer (ou flèches du clavier)";
  button.setAttribute("aria-label", `Déplacer « ${label} » (flèches haut et bas)`);
  td.append(button);
  return td;
}

// Comptes et actifs de « Gérer » : l'ordre choisi est enregistré en base et suivi partout
// (menus, filtre des lignes, Fiscalité). En cas d'échec, la liste reprend l'ordre enregistré.
async function saveOrder(kind, rows, moved) {
  const ids = rows.map((tr) => Number(tr.dataset.id));
  // Actifs soldés, absents de la liste déplaçable : gardés à la suite, dans leur ordre (D-085).
  ids.push(...lists[kind].map((x) => x.id).filter((id) => !ids.includes(id)));
  const focused = document.activeElement?.classList.contains("drag-handle");
  try {
    await api(`/api/${kind}/order`, { method: "PUT", body: JSON.stringify({ ids }) });
    lists[kind].sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
    showMessage("Ordre enregistré.");
  } catch (err) {
    showMessage(err.message, true);
  }
  renderLists(lists.accounts, lists.assets, lists.operations);
  renderLineFilter();
  if (focused) $(`${kind}-list`).querySelector(`tr[data-id="${moved.dataset.id}"] .drag-handle`)?.focus();
}

// Intérêts Aave estimés (D-095) : recalculés par l'app, corrigés par « Vérifier » (onglet Rendements crypto).
function aaveOpCell() {
  const td = cell("calculé", "muted small");
  td.append(" ", makeTip("Intérêts Aave estimés par l'app chaque mois. Pour les corriger : « Vérifier » dans l'onglet Rendements crypto."));
  return td;
}

function actionButtons(onEdit, onDelete) {
  const td = document.createElement("td");
  td.className = "actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.textContent = "Modifier";
  edit.className = "link edit";
  edit.onclick = onEdit;
  const del = document.createElement("button");
  del.type = "button";
  del.textContent = "Supprimer";
  del.className = "link";
  del.onclick = onDelete;
  td.append(edit, del);
  return td;
}

// Comptes, actifs et opérations : affichés d'abord, sans attendre les cours en ligne, pour
// que les formulaires restent utilisables même si une source de prix est lente ou en panne.
function renderLists(accounts, assets, operations) {
  Object.assign(lists, { accounts, assets, operations });
  for (const form of document.querySelectorAll(".op-form")) fillOperationSelects(form);
  fillSelect($("form-import-start").account_id, lists.accounts, (a) => a.name);
  // Aucun compte : un relevé reconnu (D-071) propose de créer ceux qu'il contient.
  if (!lists.accounts.length) $("form-import-start").account_id.append(new Option("Aucun : ceux du relevé", ""));
  // Compte ou actif créé depuis l'aperçu d'un import : l'aperçu se recalcule tout seul.
  if ($("dlg-import").open && importState.text) previewImport().catch((err) => showMessage(err.message, true));
  renderOperations();
  renderAccountsList();
  renderAssetsList();
  document.dispatchEvent(new CustomEvent("patrimoine:data")); // le tutoriel (tutorial.js) s'y appuie
}

function renderOperations() {
  renderPositions(); // positions et opérations sont rangées ensemble, par compte (D-073)
}

function renderAccountsList() {
  const movable = lists.accounts.length > 1;
  const rows = lists.accounts.map((a) => {
    const tr = document.createElement("tr");
    tr.dataset.id = a.id;
    if (movable) tr.append(gripCell(a.name));
    const name = cell(a.name);
    const detail = document.createElement("span");
    detail.className = "muted small block";
    detail.textContent = [kindLabel(a), a.institution].filter(Boolean).join(" · ");
    name.append(detail);
    // Un seul montant : le total du compte (titres + espèces), en euros, tel que dans le patrimoine.
    // Le détail des espèces et des versements reste au survol.
    const total = lastPortfolio?.groupings?.compte?.all?.find((p) => p.label === a.name);
    const entered = CASH_ENTERED.includes(a.kind);
    const byDeposits = a.kind in DEPOSITS_WHY;
    const money = cell(total ? formatDecimal(roundDecimal(total.value), "EUR") : "–", "num");
    const legacy = entered && a.kind !== "livret" && !a.cash_date && lists.operations.some((op) => op.account_id === a.id);
    if (legacy) {
      const sub = document.createElement("span");
      sub.className = "warn small block";
      sub.textContent = "espèces à vérifier";
      money.append(sub);
    }
    const cashText = `espèces ${formatDecimal(roundDecimal(a.cash), a.currency)}`;
    money.title = byDeposits || !a.cash_date
      ? `${cashText}${byDeposits ? ` · versé ${formatDecimal(roundDecimal(a.deposits), a.currency)}` : ""}. Espèces = versements + ventes + dividendes − achats − frais − retraits`
      : `${cashText}. Solde saisi le ${frDate(a.cash_date)}, opérations suivantes comprises`;
    const actions = actionButtons(() => openAccount(a), () => deleteItem("/api/accounts", a, a.name, "Compte"));
    actions.prepend(smallButton("Vérifier", "Comparer avec le relevé de ta banque", () => openReconcile(a).catch((err) => showMessage(err.message, true)), "link edit verify"));
    tr.append(name, money, actions);
    return tr;
  });
  $("accounts-list").replaceChildren(...(rows.length ? rows : [emptyRow("Aucun compte : commence par en ajouter un.")]));
}

// Actifs soldés (D-085) : plus détenus nulle part, rangés à part dans un volet replié, sans
// poignée ; ils reviennent dans la liste dès qu'une opération en remet.
function renderAssetsList() {
  const held = lists.assets.filter((a) => !a.sold_out);
  const sold = lists.assets.filter((a) => a.sold_out);
  const movable = held.length > 1;
  const row = (a, grip) => {
    const tr = document.createElement("tr");
    tr.dataset.id = a.id;
    if (grip) tr.append(gripCell(a.name));
    const name = cell(a.name);
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = a.color ? css(SERIES[a.color - 1]) : partColor("ligne", a.name);
    name.prepend(dot);
    const detail = document.createElement("span");
    detail.className = "muted small block";
    detail.textContent = [CLASSES[a.asset_class], a.category, a.ticker ?? a.coingecko_id ?? "valeur saisie", a.currency, a.color ? null : "couleur auto"].filter(Boolean).join(" · ");
    name.append(detail);
    const actions = actionButtons(() => openAsset(a), () => deleteItem("/api/assets", a, a.name, "Actif"));
    if (isManual(a)) actions.prepend(smallButton("Valeur", "Saisir la valeur de la ligne", () => openValuation(a)));
    tr.append(name, actions);
    return tr;
  };
  const rows = held.map((a) => row(a, movable));
  $("assets-list").replaceChildren(...(rows.length ? rows : [emptyRow(sold.length ? "Aucun actif détenu aujourd'hui." : "Aucun actif pour l'instant.")]));
  $("assets-sold-box").hidden = !sold.length;
  $("assets-sold-count").textContent = sold.length;
  $("assets-sold").replaceChildren(...sold.map((a) => row(a, false)));
}

// Menu « Actif » de la saisie : actifs détenus, puis les soldés dans un groupe à part (D-085).
function fillAssetSelect(select) {
  const current = select.value;
  const held = lists.assets.filter((a) => !a.sold_out);
  const sold = lists.assets.filter((a) => a.sold_out);
  const group = document.createElement("optgroup");
  group.label = "Soldés";
  group.append(...sold.map((a) => new Option(assetLabel(a), a.id)));
  select.replaceChildren(...held.map((a) => new Option(assetLabel(a), a.id)), ...(sold.length ? [group] : []));
  if (lists.assets.some((a) => String(a.id) === current)) select.value = current;
}

// Texte long (note, nom d'actif) : coupé à l'écran (« … »), lisible en entier au survol.
function clipCell(text, className) {
  const td = cell(text, className);
  if (text) td.title = text;
  return td;
}

const noteCell = (note) => clipCell(note, "note");

function emptyRow(text) {
  const tr = document.createElement("tr");
  tr.append(cell(text, "muted"));
  return tr;
}

// Indicateurs sous le total : une tuile par chiffre (libellé, valeur, détail facultatif).
function kpi(label, value, detail, className) {
  const box = document.createElement("div");
  box.className = "kpi";
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  // Un montant à zéro n'est ni un gain ni une perte : pas de couleur.
  if (className && !/[1-9]/.test(value)) className = "";
  if (className) dd.className = className;
  box.append(dt, dd);
  if (detail) {
    const small = document.createElement("span");
    // Le détail ne prend la couleur que s'il est lui-même un chiffre signé (« +17,13 % »).
    small.className = `kpi-detail ${/^[+-]/.test(detail) ? (className ?? "") : ""}`;
    small.textContent = detail;
    box.append(small);
  }
  return box;
}

function renderKpis(totals) {
  const tiles = [];
  for (const [cur, t] of totals) {
    const suffix = totals.length > 1 ? ` (${cur})` : "";
    const tone = (v) => (v.startsWith("-") ? "ko" : "ok");
    if (t.cash) {
      tiles.push(kpi(`Titres${suffix}`, formatDecimal(t.value, cur)));
      tiles.push(kpi(`Espèces${suffix}`, formatDecimal(t.cash, cur)));
    }
    tiles.push(kpi(`Investi${suffix}`, formatDecimal(t.cost, cur)));
    tiles.push(kpi(`${t.gain.startsWith("-") ? "Moins" : "Plus"}-value latente${suffix}`, formatDecimal(signed(t.gain), cur), formatPct(t.gain_pct), tone(t.gain)));
    if (t.day_change) {
      tiles.push(kpi(`Aujourd'hui${suffix}`, formatDecimal(signed(t.day_change), cur), formatPct(t.day_change_pct), tone(t.day_change)));
    }
  }
  $("kpis").replaceChildren(...tiles);
}

let lastPositions = null;

// Pastille de couleur devant le nom : la même que la part de la ligne dans la répartition.
function assetCell(p) {
  const td = clipCell(p.asset, "clip");
  const dot = document.createElement("span");
  dot.className = "dot";
  dot.style.background = partColor("ligne", p.asset);
  td.prepend(dot);
  return td;
}

// Recherche et filtre par compte : utiles dès qu'on a quelques dizaines de lignes.
const positionsFilter = { text: "", account: "" };

function matchesFilter(...fields) {
  const { text, account } = positionsFilter;
  if (account && fields[0] !== account) return false;
  if (!text) return true;
  const needle = text.toLocaleLowerCase("fr");
  return fields.some((f) => f && f.toLocaleLowerCase("fr").includes(needle));
}

// En-tête triable construit à la volée : tous les blocs de comptes partagent le même tri
// (« positions-table » ou « operations-table »), gardé dans le navigateur comme les autres.
function sortableHead(sortId, columns, numeric) {
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  for (const col of columns) {
    const th = document.createElement("th");
    if (col.num) th.className = "num";
    if (!col.key) {
      th.innerHTML = `<span class="sr-only">${col.label}</span>`;
      tr.append(th);
      continue;
    }
    th.dataset.sort = col.key;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sort";
    button.textContent = col.label;
    if (col.title) button.title = col.title;
    if (sorts[sortId]?.key === col.key) th.setAttribute("aria-sort", sorts[sortId].dir === "asc" ? "ascending" : "descending");
    button.addEventListener("click", () => {
      const same = sorts[sortId]?.key === col.key;
      const first = numeric.includes(col.key) || col.key.endsWith("date") ? "desc" : "asc";
      sorts[sortId] = { key: col.key, dir: same ? (sorts[sortId].dir === "asc" ? "desc" : "asc") : first };
      savePref(SORT_KEY, sorts);
      renderPositions();
      $("account-blocks").querySelector(`[data-sort-id="${sortId}"] th[data-sort="${col.key}"] button`)?.focus();
    });
    th.append(button);
    tr.append(th);
  }
  thead.append(tr);
  return thead;
}

const POSITION_COLUMNS = [
  { key: "asset", label: "Actif" },
  { key: "quantity", label: "Quantité", num: true },
  { key: "pru", label: "PRU", num: true },
  { key: "last_price", label: "Cours", num: true },
  { key: "value_eur", label: "Valeur", num: true },
  { key: "weight_pct", label: "Poids", num: true, title: "Part du patrimoine total, espèces comprises" },
  { key: "gain", label: "+/- value", num: true },
  { key: "day_change", label: "Jour", num: true },
];
const POSITION_NUMERIC = ["quantity", "pru", "last_price", "value", "value_eur", "weight_pct", "gain", "day_change"];
const OPERATION_COLUMNS = [
  { key: "op_date", label: "Date" },
  { key: "op_type", label: "Type" },
  { key: "asset", label: "Actif" },
  { key: "quantity", label: "Quantité", num: true },
  { key: "unit_price", label: "Prix", num: true },
  { key: "fees", label: "Frais", num: true },
  { key: "total", label: "Montant", num: true },
  { key: "note", label: "Note" },
  { key: null, label: "Actions" },
];
const OPERATION_NUMERIC = ["quantity", "unit_price", "fees", "total"];

function positionRow(p) {
  const tr = document.createElement("tr");
  tr.append(
    assetCell(p),
    p.manual ? cell("–", "num muted") : cell(formatDecimal(p.quantity), "num"),
    p.manual ? cell("–", "num muted") : cell(formatDecimal(p.pru, p.currency), "num"),
    priceCell(p),
    cell(formatDecimal(p.value, p.currency), "num"),
    p.weight_pct ? cell(`${formatDecimal(p.weight_pct)} %`, "num") : cell("–", "num muted"),
    cell(`${formatDecimal(p.gain, p.currency)}${p.gain_pct ? ` (${formatPct(p.gain_pct)})` : ""}`, signClass(p.gain)),
    p.day_change
      ? cell(`${formatDecimal(signed(p.day_change), p.currency)} (${formatPct(p.day_change_pct)})`, signClass(p.day_change))
      : cell("–", "num muted"),
  );
  return tr;
}

// Une ligne « Espèces » (ou « Livret ») par compte qui a des liquidités, toujours sous les titres.
function cashRow(c) {
  const tr = document.createElement("tr");
  tr.className = "cash-row";
  const value = cell(formatDecimal(c.cash, c.currency), c.cash.startsWith("-") ? "num ko" : "num");
  if (c.cash_date) value.title = `Dernier solde saisi le ${frDate(c.cash_date)}, opérations suivantes comprises`;
  tr.append(cell(c.kind === "livret" ? "Livret" : "Espèces"), cell(""), cell(""), cell(""), value, cell(""), cell(""), cell(""));
  return tr;
}

function operationRow(op) {
  const tr = document.createElement("tr");
  const cash = CASH_TYPES.includes(op.op_type);
  tr.append(
    cell(frDate(op.op_date)),
    cell(TYPES_FULL[op.op_type] ?? op.op_type),
    op.asset ? clipCell(op.asset, "clip") : cell("–", "muted"),
    cell(formatDecimal(op.quantity), "num"),
    cell(formatDecimal(op.unit_price, op.currency), "num"),
    cell(cash ? "" : formatDecimal(op.fees, op.currency), "num"),
    cell(formatDecimal(op.total, op.currency), `num ${["achat", "retrait", "frais"].includes(op.op_type) ? "" : "ok"}`),
    noteCell([op.where, op.note].filter(Boolean).join(" · ")),
    op.arrival ? cell("au départ", "muted small")
      : op.source === "aave" ? aaveOpCell()
      : actionButtons(() => openOperationEdit(op), () => deleteOperation(op)),
  );
  return tr;
}

// Somme exacte de montants en chaînes (« 12.5 », « -3.07 ») : entiers BigInt à 8 décimales,
// jamais de nombre à virgule flottante (règle 7). Résultat arrondi au centime.
function sumDecimals(values) {
  const units = (v) => {
    const [int, frac = ""] = v.replace("-", "").split(".");
    const n = BigInt(int + `${frac}00000000`.slice(0, 8));
    return v.startsWith("-") ? -n : n;
  };
  const total = values.reduce((sum, v) => sum + units(v), 0n);
  const cents = ((total < 0n ? -total : total) + 500000n) / 1000000n;
  const abs = cents.toString().padStart(3, "0");
  return `${total < 0n && cents ? "-" : ""}${abs.slice(0, -2)}.${abs.slice(-2)}`;
}

// Un « rectangle » du bloc d'un compte : titre, puis tableau triable (ou texte si vide).
function accountPanel(title, sortId, columns, numeric, rows, toRow, extra, empty) {
  const panel = document.createElement("div");
  panel.className = "subcard";
  panel.dataset.sortId = sortId;
  panel.setAttribute("aria-label", title); // le titre est sur l'onglet du compte (D-117)
  if (!rows.length && !extra.length) {
    panel.append(Object.assign(document.createElement("p"), { className: "muted small", textContent: empty }));
    return panel;
  }
  const table = document.createElement("table");
  table.className = "sortable";
  const body = document.createElement("tbody");
  body.append(...sortRows(sortId, rows, numeric).map(toRow), ...extra);
  table.append(sortableHead(sortId, columns, numeric), body);
  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  scroll.append(table);
  panel.append(scroll);
  return panel;
}

// Tableau ouvert dans chaque compte (positions ou opérations), gardé dans le navigateur.
const ACCOUNT_VIEW_KEY = PREF + "vue-compte";
const accountViews = loadPref(ACCOUNT_VIEW_KEY, {});

// Positions et opérations, un bloc par compte (D-073) dans l'ordre choisi dans Gérer.
function renderPositions() {
  const portfolio = lastPositions ?? { positions: [], cash: [] };
  const accountName = Object.fromEntries(lists.accounts.map((a) => [a.id, a.name]));
  const assetName = Object.fromEntries(lists.assets.map((a) => [a.id, a.name]));
  // Transfert vers un compte de l'app (D-085) : affiché aussi à l'arrivée, en lecture seule
  // (il se modifie au départ).
  const arrivals = lists.operations.filter((op) => op.to_account_id).map((op) => ({
    ...op,
    account_id: op.to_account_id,
    op_type: op.op_type === "retrait" ? "versement" : "transfert_entrant",
    where: `depuis ${accountName[op.account_id]}`,
    arrival: true,
  }));
  const operations = [...lists.operations, ...arrivals].map((op) => ({
    ...op,
    account: accountName[op.account_id],
    asset: op.asset_id ? assetName[op.asset_id] : "",
    type_label: TYPES_FULL[op.op_type] ?? op.op_type,
    where: op.where ?? (op.to_account_id ? `vers ${accountName[op.to_account_id]}` : op.destination ? `vers ${op.destination}` : null),
  }));
  const used = lists.accounts.filter((a) =>
    portfolio.positions.some((p) => p.account === a.name)
    || portfolio.cash.some((c) => c.account === a.name)
    || operations.some((op) => op.account_id === a.id));
  const select = $("positions-account");
  if (select.options.length !== used.length + 1 || used.some((a, i) => select.options[i + 1].value !== a.name)) {
    if (!used.some((a) => a.name === positionsFilter.account)) positionsFilter.account = "";
    select.replaceChildren(new Option("Tous les comptes", ""), ...used.map((a) => new Option(a.name, a.name)));
    select.value = positionsFilter.account;
  }
  let lines = 0;
  let shownLines = 0;
  const blocks = [];
  for (const account of used) {
    if (positionsFilter.account && account.name !== positionsFilter.account) continue;
    const all = portfolio.positions.filter((p) => p.account === account.name);
    const positions = all.filter((p) => matchesFilter(p.account, p.asset, p.ticker, p.asset_class));
    const cash = portfolio.cash.filter((c) => c.account === account.name && matchesFilter(c.account, "Espèces", "Livret"));
    const ops = operations.filter((op) => op.account_id === account.id && matchesFilter(op.account, op.asset, op.type_label, op.note, op.where));
    lines += all.length;
    shownLines += positions.length;
    if (positionsFilter.text && !positions.length && !cash.length && !ops.length) continue;
    const card = document.createElement("section");
    card.className = "card account-block";
    const head = document.createElement("div");
    head.className = "card-head";
    const h2 = document.createElement("h2");
    h2.textContent = account.name;
    const detail = document.createElement("span");
    detail.className = "muted small";
    const values = all.map((p) => p.value_eur);
    const cashAll = portfolio.cash.filter((c) => c.account === account.name);
    // Valeur du compte en EUR quand tout est convertible (titres et espèces en euros).
    const total = values.every((v) => v !== null) && cashAll.every((c) => c.currency === "EUR")
      ? sumDecimals([...values, ...cashAll.map((c) => c.cash)])
      : null;
    detail.textContent = [
      kindLabel(account),
      account.institution,
      `${all.length} ligne${all.length > 1 ? "s" : ""}`,
      total === null ? null : formatDecimal(total, "EUR"),
    ].filter(Boolean).join(" · ");
    // Positions ou Opérations : un tableau à la fois par compte, choix gardé (D-117).
    const panels = {
      positions: accountPanel("Positions", "positions-table", POSITION_COLUMNS, POSITION_NUMERIC, positions, positionRow, cash.map(cashRow), "Aucune position."),
      operations: accountPanel("Opérations", "operations-table", OPERATION_COLUMNS, OPERATION_NUMERIC, ops, operationRow, [], "Aucune opération."),
    };
    const counts = { positions: positions.length, operations: ops.length };
    const seg = document.createElement("div");
    seg.className = "segmented small-seg account-view";
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", `Tableau affiché pour ${account.name}`);
    const showView = (view) => {
      for (const [name, panel] of Object.entries(panels)) panel.hidden = name !== view;
      for (const b of seg.children) {
        b.classList.toggle("active", b.dataset.view === view);
        b.setAttribute("aria-pressed", String(b.dataset.view === view));
      }
    };
    for (const [view, label] of [["positions", "Positions"], ["operations", "Opérations"]]) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.view = view;
      b.textContent = `${label} ${counts[view]}`;
      b.addEventListener("click", () => {
        accountViews[account.id] = view;
        savePref(ACCOUNT_VIEW_KEY, accountViews);
        showView(view);
      });
      seg.append(b);
    }
    showView(accountViews[account.id] === "operations" ? "operations" : "positions");
    head.append(h2, seg, detail);
    card.append(head, panels.positions, panels.operations);
    blocks.push(card);
  }
  $("positions-count").textContent = shownLines === lines
    ? `${lines} ligne${lines > 1 ? "s" : ""}`
    : `${shownLines} sur ${lines} lignes`;
  $("account-blocks").replaceChildren(...(blocks.length ? blocks : [Object.assign(document.createElement("p"), {
    className: "muted",
    textContent: lists.accounts.length ? "Rien ne correspond à la recherche." : "Aucun compte pour l'instant : crée-en un dans « Gérer ».",
  })]));
}
$("positions-search").addEventListener("input", (e) => {
  positionsFilter.text = e.target.value.trim();
  renderPositions();
});
$("positions-account").addEventListener("change", (e) => {
  positionsFilter.account = e.target.value;
  renderPositions();
});

// `view` : le même portefeuille limité aux lignes cochées (filtre de Vue d'ensemble, D-069) ;
// Positions, Gérer, alertes et couleurs restent calculés sur le patrimoine complet.
function renderPortfolio(portfolio, view = portfolio) {
  // Total converti en EUR ; une devise sans taux de change reste sur sa propre ligne.
  const totals = Object.entries(view.totals);
  $("total-label").textContent = view === portfolio ? "Patrimoine total" : "Patrimoine des lignes cochées";
  // Le total inclut les espèces des comptes (t.total) quand il y en a.
  $("total-value").textContent = totals.length
    ? totals.map(([cur, t]) => formatDecimal(t.total ?? t.value, cur)).join(" + ")
    : "–";
  renderKpis(totals);
  const net = netWorthTile(portfolio);
  if (net) $("kpis").prepend(net);
  checkAlerts(portfolio);

  lastPositions = portfolio;
  lastPortfolio = portfolio; // couleurs des pastilles du tableau, calculées sur le patrimoine
  renderPositions();
  renderAssetsList(); // pastilles « couleur auto » de Gérer, connues avec le patrimoine
  renderAccountsList(); // total de chaque compte
  showPricesStatus(portfolio);
  renderCharts(view);
  if (currentPage === "visualisations") renderTreemap(); // sans attendre l'historique
}

// --- Graphiques (Chart.js, copié dans web/vendor : aucune requête vers un CDN) ---------
// Chart.js dessine à partir de nombres JavaScript : les montants y sont convertis avec
// Number() uniquement pour placer les points à l'écran. Les infobulles réaffichent le texte
// exact reçu de l'API (formatDecimal), jamais un nombre recalculé.

// Couleurs lues dans styles.css (variables CSS) : le mode sombre suit sans code en plus.
// Une teinte dérivée (color-mix, D-115) est peinte sur un point puis relue : Chart.js ne sait
// éclaircir ou rendre transparente qu'une couleur écrite en rgb.
// Écran de téléphone : légende sous le graphique, moins de dates sur l'axe.
const isNarrow = () => window.innerWidth < 600;
const cssProbe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
const css = (name) => {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!value.startsWith("color-mix")) return value;
  cssProbe.clearRect(0, 0, 1, 1);
  cssProbe.fillStyle = value;
  cssProbe.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = cssProbe.getImageData(0, 0, 1, 1).data;
  return a === 255 ? `rgb(${r} ${g} ${b})` : `rgb(${r} ${g} ${b} / ${(a / 255).toFixed(3)})`;
};
const CLASS_COLORS = { ETF: "--series-1", Actions: "--series-2", Crypto: "--series-3", "Espèces": "--neutral" };
// Sept couleurs bien distinctes (vérifiées pour les daltonismes courants), toujours dans cet
// ordre ; au-delà, l'œil ne les sépare plus : les plus petites parts passent dans « Autres ».
const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7"];
const otherColor = () => `color-mix(in srgb, ${css("--neutral")} 45%, ${css("--surface")})`;

// Couleurs des lignes (D-051, D-052) : celle choisie dans « Gérer » d'abord ; les autres
// lignes parmi les 7 plus grosses prennent, dans l'ordre du poids, les couleurs que personne
// n'a choisies ; au-delà, gris. Calculé sur le patrimoine complet, jamais sur une vue filtrée.
let lineColorCache = { portfolio: null, map: new Map() };

function lineColors() {
  const portfolio = lastPortfolio;
  if (lineColorCache.portfolio === portfolio) return lineColorCache.map;
  const map = new Map();
  for (const p of portfolio?.positions ?? []) if (p.color) map.set(p.asset, SERIES[p.color - 1]);
  const free = SERIES.filter((s) => ![...map.values()].includes(s));
  const ranked = (portfolio?.groupings?.ligne?.all ?? []).filter((p) => p.label !== "Espèces");
  ranked.slice(0, SERIES.length).forEach((p) => {
    if (!map.has(p.label) && free.length) map.set(p.label, free.shift());
  });
  lineColorCache = { portfolio, map };
  return map;
}

// Couleur d'une classe : fixe pour ETF, actions, crypto (les plus courantes) ; les autres
// classes (fonds euros, immobilier, livrets...) prennent les couleurs restantes par ordre de
// poids dans le patrimoine ; au-delà, gris (D-056).
function classColorName(label) {
  if (CLASS_COLORS[label]) return CLASS_COLORS[label];
  const fixed = new Set(Object.values(CLASS_COLORS));
  const free = SERIES.filter((s) => !fixed.has(s));
  const ranked = (lastPortfolio?.groupings?.classe?.all ?? [])
    .map((p) => p.label)
    .filter((l) => !CLASS_COLORS[l] && l !== "Autres");
  const i = ranked.indexOf(label);
  return i >= 0 && i < free.length ? free[i] : null;
}

// Couleur d'une part. Classes : voir classColorName. Lignes : voir lineColors. Comptes, devises :
// rang de la part dans le patrimoine complet.
function partColor(by, label) {
  if (label === "Espèces") return css("--neutral");
  if (label === "Autres") return otherColor();
  if (by === "classe") {
    const name = classColorName(label);
    return name ? css(name) : otherColor();
  }
  if (by === "ligne") {
    const name = lineColors().get(label);
    return name ? css(name) : otherColor();
  }
  const ranked = (lastPortfolio?.groupings?.[by]?.all ?? []).filter((p) => p.label !== "Espèces");
  const i = ranked.findIndex((p) => p.label === label);
  return i >= 0 && i < SERIES.length ? css(SERIES[i]) : otherColor();
}
const charts = {};
let lastPortfolio = null; // patrimoine complet : couleurs, positions, alertes
let overviewPortfolio = null; // ce que montre Vue d'ensemble (filtré ou non)
let lastHistory = null;

function chartTheme() {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = css("--text-2");
  Chart.defaults.borderColor = css("--border");
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
}

// Passage clair/sombre (système ou carte Apparence, theme.js) : graphiques recréés avec les nouvelles couleurs.
document.addEventListener("themechange", () => {
  for (const id of Object.keys(charts)) {
    charts[id].destroy();
    delete charts[id];
  }
  if (overviewPortfolio) renderCharts(overviewPortfolio);
  if (lastHistory) drawHistory(lastHistory);
  if (lastPerformance) renderPerformance(lastPerformance);
});

// Carte Apparence (D-133) : thème clair, sombre ou celui du système, gardé par theme.js.
function renderThemeChoice() {
  for (const button of $("theme-choice").querySelectorAll("button")) {
    const chosen = button.dataset.themeChoice === window.patrimoineTheme.get();
    button.classList.toggle("active", chosen);
    button.setAttribute("aria-pressed", String(chosen));
  }
}
$("theme-choice").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-theme-choice]");
  if (!button) return;
  window.patrimoineTheme.set(button.dataset.themeChoice);
  renderThemeChoice();
});
renderThemeChoice();
const euros = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });

// Crée le graphique au premier appel, puis remplace seulement ses données (sans animation)
// pour que l'actualisation automatique ne fasse pas « sauter » la page.
function drawChart(id, config) {
  if (typeof Chart === "undefined") return; // fichier vendor absent : le reste de la page marche
  chartTheme();
  if (charts[id]) {
    charts[id].data = config.data;
    charts[id].options = config.options; // infobulles liées aux nouvelles données
    charts[id].update("none");
  } else {
    charts[id] = new Chart($(id), config);
  }
}

// Légende des courbes : un vrai trait (plein ou en tirets, comme la courbe) au lieu du
// petit rectangle par défaut, dont le contour pointillé donnait un trait cassé et flou.
const lineLegend = {
  position: "top",
  align: "end",
  labels: {
    usePointStyle: true,
    pointStyle: "line",
    pointStyleWidth: 30,
    boxHeight: 6,
    // Épaisseur et tirets repris de la courbe (Chart.js ne lit pas l'épaisseur par défaut).
    generateLabels: (chart) =>
      Chart.defaults.plugins.legend.labels.generateLabels(chart).map((l) => {
        const ds = chart.data.datasets[l.datasetIndex];
        return { ...l, lineWidth: ds.borderWidth ?? 2.5, lineDash: ds.borderDash ?? [], strokeStyle: ds.borderColor };
      }),
  },
};

// Camembert : parts en EUR, pourcentage repris dans la légende (« ETF · 82,5 % »).
// `colorOf` donne la couleur d'une part ; `onPick` (facultatif) reçoit le libellé cliqué.
function doughnutConfig(parts, colorOf, onPick) {
  return {
    type: "doughnut",
    data: {
      labels: parts.map((p) => `${p.display ?? p.label} · ${formatDecimal(p.pct)} %`),
      datasets: [{
        data: parts.map((p) => Number(p.value)),
        backgroundColor: parts.map((p, i) => colorOf(p, i)),
        borderColor: css("--surface"),
        borderWidth: 2,
        hoverOffset: 4,
      }],
    },
    options: {
      maintainAspectRatio: false,
      cutout: "68%",
      onClick: onPick ? (_, elements) => elements.length && onPick(parts[elements[0].index].label) : undefined,
      onHover: (event, elements) => {
        event.native.target.style.cursor = onPick && elements.length ? "pointer" : "default";
      },
      plugins: {
        legend: { position: isNarrow() ? "bottom" : "right" },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${formatDecimal(parts[ctx.dataIndex].value, "EUR")} (${formatDecimal(parts[ctx.dataIndex].pct)} %)`,
          },
        },
      },
    },
  };
}

const classColor = (p) => partColor("classe", p.label);
// Détail d'une classe : nuances de la couleur de la classe, de la plus soutenue à la plus claire.
// Détail d'une classe : chaque ligne garde sa couleur (choisie dans « Gérer », sinon son rang) ;
// les lignes sans couleur prennent des nuances de gris pour rester distinctes. Espèces : nuances.
function detailColor(classLabel) {
  if (classLabel === "Espèces") return shadeColor("--neutral");
  const grey = shadeColor("--neutral");
  let greys = 0;
  return (p) => {
    const name = lineColors().get(p.label);
    return name ? css(name) : grey(p, greys++);
  };
}

const shadeColor = (base) => (p, i) => `color-mix(in srgb, ${css(base)} ${Math.max(30, 100 - i * 28)}%, ${css("--surface")})`;

// Plus-values par ligne ; `horizontal` pour la vue agrandie, lisible même avec 30 lignes.
function gainsConfig(lines, horizontal = false) {
  const value = { border: { display: false }, ticks: { callback: (v) => euros.format(v) } };
  const names = { grid: { display: false } };
  return {
    type: "bar",
    data: {
      labels: lines.map((p) => p.asset),
      datasets: [{
        data: lines.map((p) => Number(p.gain_eur)),
        backgroundColor: lines.map((p) => css(p.gain_eur.startsWith("-") ? "--ko" : "--ok")),
        borderRadius: 4,
        maxBarThickness: horizontal ? 22 : 56,
      }],
    },
    options: {
      maintainAspectRatio: false,
      indexAxis: horizontal ? "y" : "x",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const p = lines[ctx.dataIndex];
              return ` ${formatDecimal(signed(p.gain_eur), "EUR")}${p.gain_pct ? ` (${formatPct(p.gain_pct)})` : ""}`;
            },
          },
        },
      },
      scales: horizontal ? { x: value, y: names } : { x: names, y: value },
    },
  };
}

const gainLines = (portfolio) => portfolio.positions.filter((p) => p.gain_eur !== null);
// Petit graphique : les plus grosses plus-values et moins-values seulement (en valeur
// absolue), la vue agrandie montre toutes les lignes.
const GAINS_MAX = 12;
function biggestGains(lines) {
  if (lines.length <= GAINS_MAX) return lines;
  const kept = new Set([...lines].sort((a, b) => Math.abs(Number(b.gain_eur)) - Math.abs(Number(a.gain_eur))).slice(0, GAINS_MAX));
  return lines.filter((p) => kept.has(p));
}

// --- Répartition : regrouper par classe, ligne, compte ou devise (D-051) ---------------
// Niveau des cibles (D-061) : par ligne ou par catégorie, gardé dans le navigateur.
const TARGET_KEY = PREF + "cible";
const targetPref = loadPref(TARGET_KEY, { level: "ligne" });
const TARGET_HELP = {
  ligne: "Donne une part visée (en %) aux lignes qui comptent pour toi : l'app montre l'écart de chacune et répartit ton prochain versement pour t'en rapprocher, sans rien vendre. Les parts se calculent entre les lignes qui ont une cible.",
  categorie: "Donne une part visée (en %) à tes catégories (choisies dans « Gérer » > Actifs ; à défaut, la classe de l'actif). Le montant à acheter dans une catégorie va sur sa plus grosse ligne : un seul ordre, moins de frais. Pour viser une nouvelle catégorie, donne-la d'abord à un actif.",
};

const GROUP_KEY = PREF + "repartition";
const groupPref = loadPref(GROUP_KEY, { by: "ligne" });
const GROUP_NAMES = { ligne: ["ligne", "lignes"], classe: ["classe", "classes"], categorie: ["catégorie", "catégories"], compte: ["compte", "comptes"], devise: ["devise", "devises"] };

function groupedParts(portfolio, by) {
  const g = portfolio.groupings?.[by];
  if (!g) return portfolio.allocation; // ancienne réponse de l'API : par classe
  const [one, many] = GROUP_NAMES[by];
  return g.top.map((p) => (p.label === "Autres" ? { ...p, display: `Autres (${g.others} ${g.others > 1 ? many : one})` } : p));
}

function pickPart(by, label) {
  openZoom("liste", by);
}

function renderCharts(portfolio) {
  overviewPortfolio = portfolio;
  const by = groupPref.by;
  for (const b of $("group-by").querySelectorAll("button")) b.classList.toggle("active", b.dataset.group === by);
  const parts = groupedParts(portfolio, by);
  drawChart("chart-allocation", doughnutConfig(parts, (p) => partColor(by, p.label), (label) => pickPart(by, label)));
  const others = portfolio.groupings?.[by]?.others ?? 0;
  $("allocation-note").textContent = others
    ? `Les ${others} plus petites ${GROUP_NAMES[by][1]} sont regroupées dans « Autres » : clique dessus pour tout voir.`
    : "Clique sur une part pour voir son détail.";
  const lines = gainLines(portfolio);
  const shown = biggestGains(lines);
  drawChart("chart-gains", gainsConfig(shown));
  $("gains-note").hidden = shown.length === lines.length;
  $("gains-note").textContent = `Les ${shown.length} plus gros écarts sur ${lines.length} lignes : agrandis pour tout voir.`;
  if ($("zoom").open) renderZoom(); // actualisation automatique : la fenêtre suit
}

for (const b of $("group-by").querySelectorAll("button")) {
  b.addEventListener("click", () => {
    groupPref.by = b.dataset.group;
    savePref(GROUP_KEY, groupPref);
    // Le nombre de parts change : graphique neuf plutôt qu'une mise à jour animée.
    charts["chart-allocation"]?.destroy();
    delete charts["chart-allocation"];
    if (overviewPortfolio) renderCharts(overviewPortfolio);
  });
}

// Toutes les parts d'un regroupement en barres horizontales : lisible même avec 100 lignes.
function partsBarConfig(parts, by) {
  return {
    type: "bar",
    data: {
      labels: parts.map((p) => p.label),
      datasets: [{
        data: parts.map((p) => Number(p.value)),
        backgroundColor: parts.map((p) => partColor(by, p.label)),
        borderRadius: 4,
        maxBarThickness: 22,
      }],
    },
    options: {
      maintainAspectRatio: false,
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${formatDecimal(parts[ctx.dataIndex].value, "EUR")} (${formatDecimal(parts[ctx.dataIndex].pct)} %)`,
          },
        },
      },
      scales: {
        x: { border: { display: false }, ticks: { callback: (v) => euros.format(v) } },
        y: { grid: { display: false } },
      },
    },
  };
}

// --- Fenêtre « agrandir » ---------------------------------------------------------------
// <dialog> est une fenêtre native du navigateur : elle passe au-dessus de la page, se ferme
// avec Échap et n'ajoute aucune bibliothèque.

const zoom = { kind: null, detail: null };

function openZoom(kind, detail = null) {
  Object.assign(zoom, { kind, detail });
  const dialog = $("zoom");
  if (!dialog.open) dialog.showModal();
  renderZoom();
}

function zoomChip(text, active, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `chip${active ? " active" : ""}`;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function renderZoom() {
  const portfolio = overviewPortfolio ?? lastPortfolio; // agrandissement de Vue d'ensemble
  if (!portfolio) return;
  // Un camembert et des barres ne se transforment pas l'un en l'autre : graphique neuf.
  charts["chart-zoom"]?.destroy();
  delete charts["chart-zoom"];
  const chips = [];
  let config;
  let note = "";
  if (zoom.kind === "liste") {
    const by = GROUP_NAMES[zoom.detail] ? zoom.detail : "ligne";
    for (const key of Object.keys(GROUP_NAMES)) {
      chips.push(zoomChip(`Par ${GROUP_NAMES[key][0]}`, key === by, () => openZoom("liste", key)));
    }
    const parts = portfolio.groupings?.[by]?.all ?? [];
    $("zoom-title").textContent = `Répartition par ${GROUP_NAMES[by][0]}`;
    config = partsBarConfig(parts, by);
    $("zoom-chart").style.height = `${Math.max(320, parts.length * 30 + 60)}px`;
    note = parts.length > SERIES.length + 1
      ? `${parts.length} ${GROUP_NAMES[by][1]}, en euros et en % du patrimoine. En gris : celles regroupées dans « Autres » sur la vue d'ensemble.`
      : `En euros et en % du patrimoine.`;
  } else {
    const lines = gainLines(portfolio);
    $("zoom-title").textContent = "Plus/moins-values latentes";
    config = gainsConfig(lines, true);
    // Une ligne = 34 px : au-delà de la fenêtre, la zone défile au lieu d'écraser les barres.
    $("zoom-chart").style.height = `${Math.max(320, lines.length * 34 + 60)}px`;
    note = "En euros, par ligne détenue.";
  }
  $("zoom-chips").replaceChildren(...chips);
  $("zoom-note").textContent = note;
  drawChart("chart-zoom", config);
}

// Bouton « agrandir » de la répartition : le détail par classe, ou la liste complète du
// regroupement choisi.
for (const b of document.querySelectorAll("[data-zoom]")) {
  b.addEventListener("click", () => {
    if (b.dataset.zoom === "allocation") openZoom("liste", groupPref.by);
    else openZoom(b.dataset.zoom);
  });
}
$("zoom").querySelector("[data-close]").addEventListener("click", () => $("zoom").close());
// Clic sur le fond sombre (hors de la fenêtre) : on ferme aussi.
$("zoom").addEventListener("click", (e) => {
  if (e.target === $("zoom")) $("zoom").close();
});

// Taux d'inflation après le dernier mois publié par Eurostat (D-067), gardé dans le navigateur.
const INFLATION_KEY = PREF + "inflation";
const inflationPref = loadPref(INFLATION_KEY, { rate: "2" });
$("inflation-rate").value = inflationPref.rate;
$("inflation-rate").addEventListener("change", (e) => {
  const rate = e.target.value.trim();
  if (rate === "" || !e.target.checkValidity()) {
    e.target.value = inflationPref.rate;
    return;
  }
  inflationPref.rate = rate;
  savePref(INFLATION_KEY, inflationPref);
  refreshHistory().catch((err) => showMessage(err.message, true));
});

async function refreshHistory() {
  const data = await api(`/api/history?${addExclude(new URLSearchParams({ inflation_rate: inflationPref.rate }))}`);
  const points = data.points;
  const notes = [];
  if (data.without_history.length) {
    notes.push(`Sans cours (courbe au prix des opérations) : ${data.without_history.join(", ")}.`);
  }
  if (data.errors.length) notes.push(`Historique non complété : ${data.errors.join(" ; ")}.`);
  notes.push("Titres seulement : les espèces n'ont pas d'historique.");
  notes.push("Argent net placé : achats moins ventes ; cadeaux et récompenses exclus, un arbitrage ne le change pas.");
  const inflation = data.inflation;
  const rate = `${String(inflation?.rate ?? inflationPref.rate).replace(".", ",")} % par an`;
  if (inflation?.last_month) {
    const [y, m] = inflation.last_month.split("-");
    notes.push(`Inflation : ${inflation.source} jusqu'à ${m}/${y}, lissée sur chaque année, puis ${rate} (taux saisi).`);
  } else if (inflation) {
    notes.push(`Eurostat indisponible : inflation au taux saisi de ${rate} sur toute la période.`);
  }
  const partial = data.without_history.length || data.errors.length;
  $("history-status").textContent = partial ? "Historique incomplet pour certaines lignes." : "";
  $("history-tip").dataset.tip = notes.join(" ");
  $("history-tip").hidden = false;
  drawHistory(points);
}

function drawHistory(points) {
  lastHistory = points;
  drawChart("chart-history", {
    type: "line",
    data: {
      labels: points.map((p) => p.date.split("-").reverse().join("/")), // 2025-06-02 -> 02/06/2025
      datasets: [
        {
          label: "Valeur des titres",
          data: points.map((p) => Number(p.value)),
          borderColor: css("--series-1"),
          backgroundColor: css("--series-1-fill"),
          fill: true,
        },
        {
          label: "Argent net placé", // achats moins ventes, cadeaux exclus (D-110)
          data: points.map((p) => Number(p.invested)),
          borderColor: css("--series-2"), // orange : tranche nettement sur le bleu de la valeur
          backgroundColor: css("--series-2"),
          borderDash: [6, 4],
          stepped: true, // ne bouge qu'aux achats et ventes
        },
        // Pouvoir d'achat de l'argent placé : il baisse quand les prix montent (D-045, D-110).
        ...(points.some((p) => p.invested_eroded !== null) ? [{
          label: "Placé moins l'inflation",
          data: points.map((p) => (p.invested_eroded === null ? null : Number(p.invested_eroded))),
          borderColor: css("--ko"), // rouge : une perte, lisible sur fond clair comme sombre
          backgroundColor: css("--ko"),
          borderDash: [3, 3],
          borderWidth: 2.5,
        }] : []),
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false }, // infobulle de toutes les courbes à la fois
      elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2 } },
      plugins: {
        legend: lineLegend,
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const p = points[ctx.dataIndex];
              const amount = [p.value, p.invested, p.invested_eroded][ctx.datasetIndex];
              const line = ` ${ctx.dataset.label} : ${formatDecimal(amount, "EUR")}`;
              // Inflation sur un an du mois survolé (D-112).
              if (ctx.datasetIndex !== 2 || p.inflation_pct === null) return line;
              return `${line} (inflation ${formatDecimal(p.inflation_pct)} %/an)`;
            },
            footer: (items) => {
              const p = points[items[0].dataIndex];
              const lines = [`Gain total : ${formatDecimal(signed(p.gain), "EUR")}`];
              // Ce que l'inflation a pris à l'argent placé, en pouvoir d'achat.
              if (p.inflation_loss !== null) lines.push(`Perdu par l'inflation : ${formatDecimal(p.inflation_loss, "EUR")}`);
              return lines;
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
  $("perf-start").value = data?.start ?? perf.start ?? "";
  $("perf-end").value = data?.end ?? perf.end ?? "";
}

async function refreshPerformance() {
  const params = new URLSearchParams({ period: perf.period });
  const start = perf.preset ? presetStart(perf.preset) : perf.start;
  if (start) params.set("start", start);
  if (!perf.preset && perf.end) params.set("end", perf.end);
  addExclude(params);
  $("perf-csv").href = `/api/performance.csv?${params}`; // même découpage, mêmes dates
  const data = await api(`/api/performance?${params}`);
  renderPerformance(data);
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
  $("perf-status").textContent = (s
    ? "Performance « pondérée par le temps » : un achat ou une vente ne compte ni comme gain ni comme perte ; comptent la variation des cours, les dividendes reçus et les frais payés (droits de garde...)."
    : "Aucune opération sur cette période.");

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
          legend: {
            position: "top",
            align: "end",
            labels: {
              // Les barres de gain changent de couleur selon le signe : légende en vert.
              generateLabels: (chart) =>
                Chart.defaults.plugins.legend.labels.generateLabels(chart).map((l) =>
                  l.datasetIndex === 1 ? { ...l, fillStyle: css("--ok"), strokeStyle: css("--ok") } : l,
                ),
            },
          },
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
          legend: { display: false },
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

// --- Préférences de vue gardées dans le navigateur ---------------------------------

function loadPref(key, defaults) {
  try {
    return { ...defaults, ...(JSON.parse(localStorage.getItem(key)) ?? {}) };
  } catch {
    return { ...defaults };
  }
}

function savePref(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // navigation privée : la préférence ne sera simplement pas retenue
  }
}

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

// --- Liste de comparaison : indices et livrets (D-042) ----------------------------------
// Partagée par les onglets Comparaison et Calendrier, gardée en base (sauvegardée avec elle).

let benchList = [];

async function loadBenchList() {
  benchList = await api("/api/benchmarks");
  fillReferenceSelect($("bench-select"), false);
  fillReferenceSelect($("cal-select"), true);
  if ($("dlg-list").open) renderBenchList();
}

const refLabel = (b) => (b.kind === "livret" ? `${b.label} (${formatDecimal(b.rate)} %)` : b.label);

// Liste déroulante groupée (« Monde », « Livrets »...) ; valeurs « id:3 », « custom », « livret ».
function fillReferenceSelect(select, withPortfolio) {
  const current = select.value;
  const groups = new Map();
  for (const b of benchList) {
    const name = b.grp || (b.kind === "livret" ? "Livrets" : "Indices");
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(new Option(refLabel(b), `id:${b.id}`));
  }
  const options = [];
  if (withPortfolio) options.push(new Option("Mon portefeuille", "portfolio"));
  for (const [name, items] of groups) {
    const group = document.createElement("optgroup");
    group.label = name;
    group.append(...items);
    options.push(group);
  }
  if (!withPortfolio) options.push(new Option("Autre ticker Yahoo…", "custom"), new Option("Autre livret (taux)…", "livret"));
  select.replaceChildren(...options);
  if (current) select.value = current;
}

// Paramètres d'API d'une référence choisie : ticker d'indice ou taux de livret.
function referenceParams(choice, custom = {}) {
  if (choice.startsWith("ticker:")) return { ticker: choice.slice(7) };
  if (choice === "custom") return custom.ticker ? { ticker: custom.ticker.trim() } : null;
  if (choice === "livret") return cleanDecimal(custom.rate ?? "") ? { rate: cleanDecimal(custom.rate) } : null;
  const b = benchList.find((x) => `id:${x.id}` === choice);
  if (!b) return null;
  return b.kind === "livret" ? { rate: b.rate } : { ticker: b.ticker };
}

function referenceName(choice, custom = {}) {
  if (choice === "custom") return custom.ticker?.trim().toUpperCase() ?? "";
  if (choice === "livret") return `Livret à ${formatDecimal(cleanDecimal(custom.rate ?? "") ?? "0")} %`;
  const b = benchList.find((x) => `id:${x.id}` === choice);
  return b ? refLabel(b) : "";
}

// --- Fenêtre « Modifier la liste » ---

const benchForm = $("form-bench");

function syncBenchForm() {
  for (const label of benchForm.querySelectorAll("[data-kind]")) {
    const on = label.dataset.kind === benchForm.kind.value;
    label.hidden = !on;
    label.querySelector("input").required = on;
  }
}

function resetBenchForm() {
  benchForm.reset();
  delete benchForm.dataset.id;
  benchForm.querySelector("[type=submit]").textContent = "Ajouter";
  benchForm.querySelector("[data-cancel]").hidden = true;
  syncBenchForm();
}

function smallButton(text, title, onClick, className = "link edit") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = text;
  b.title = title;
  b.onclick = onClick;
  return b;
}

function renderBenchList() {
  $("list-rows").replaceChildren(
    ...benchList.map((b, i) => {
      const tr = document.createElement("tr");
      const name = cell(b.label);
      const detail = document.createElement("span");
      detail.className = "muted small block";
      detail.textContent = [b.kind === "livret" ? `livret à ${formatDecimal(b.rate)} %` : b.ticker, b.grp].filter(Boolean).join(" · ");
      name.append(detail);
      const actions = document.createElement("td");
      actions.className = "actions";
      const up = smallButton("↑", "Monter", () => moveBench(b, -1), "icon");
      const down = smallButton("↓", "Descendre", () => moveBench(b, 1), "icon");
      up.disabled = i === 0;
      down.disabled = i === benchList.length - 1;
      actions.append(
        up,
        down,
        smallButton("Modifier", "Modifier", () => editBench(b)),
        smallButton("Supprimer", "Supprimer", () => deleteBench(b), "link"),
      );
      tr.append(name, actions);
      return tr;
    }),
  );
}

function editBench(b) {
  benchForm.dataset.id = b.id;
  benchForm.kind.value = b.kind;
  benchForm.label.value = b.label;
  benchForm.ticker.value = b.ticker ?? "";
  benchForm.rate.value = b.rate ? b.rate.replace(".", ",") : "";
  benchForm.grp.value = b.grp ?? "";
  benchForm.querySelector("[type=submit]").textContent = "Enregistrer";
  benchForm.querySelector("[data-cancel]").hidden = false;
  syncBenchForm();
  benchForm.label.focus();
}

async function benchAction(promise, text) {
  try {
    await promise;
    showMessage(text);
    await loadBenchList();
  } catch (err) {
    showMessage(err.message, true);
  }
}

const moveBench = (b, step) => benchAction(api(`/api/benchmarks/${b.id}/move?step=${step}`, { method: "POST" }), "Liste réordonnée.");

async function deleteBench(b) {
  if (!await askConfirm(`Retirer « ${b.label} » de la liste ?`, { ok: "Retirer" })) return;
  benchAction(api(`/api/benchmarks/${b.id}`, { method: "DELETE" }), "Retiré de la liste.");
}

function saveBench(body, id = null) {
  return api(id ? `/api/benchmarks/${id}` : "/api/benchmarks", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) });
}

benchForm.kind.addEventListener("change", syncBenchForm);
benchForm.querySelector("[data-cancel]").addEventListener("click", resetBenchForm);
benchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = Object.fromEntries(new FormData(benchForm));
  const body = { kind: f.kind, label: f.label, grp: orNull(f.grp), ticker: orNull(f.ticker ?? ""), rate: cleanDecimal(f.rate ?? "") };
  const id = benchForm.dataset.id;
  await benchAction(saveBench(body, id), id ? "Entrée modifiée." : "Ajouté à la liste.");
  resetBenchForm();
});

for (const b of document.querySelectorAll("[data-edit-list]")) {
  b.addEventListener("click", () => {
    resetBenchForm();
    renderBenchList();
    $("dlg-list").showModal();
  });
}
$("dlg-list").addEventListener("close", () => loadPage()); // la page suit la liste modifiée

// --- Calendrier des rendements (mois × années) ---------------------------------------

const MONTHS = ["Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."];
const CAL_KEY = PREF + "calendrier";
const cal = loadPref(CAL_KEY, { choice: "portfolio" });

// Fond de la case : vert ou rouge, d'autant plus soutenu que la performance est forte.
// Une case « estimée » (avant ton premier achat) reste grise, le chiffre en clair.
function heatCell(value, scale, className = "", estimated = false) {
  const td = cell(value === undefined ? "" : formatPct(value), className);
  if (value === undefined) {
    td.classList.add("empty");
    return td;
  }
  if (estimated) {
    td.classList.add("estimated");
    td.title = "Estimé : tes lignes actuelles, avant ton premier achat";
    return td;
  }
  const strength = Math.min(Math.abs(Number(value)) / scale, 1); // affichage seulement
  const color = value.startsWith("-") ? "var(--ko)" : "var(--ok)";
  td.style.background = `color-mix(in srgb, ${color} ${Math.round(8 + strength * 42)}%, transparent)`;
  return td;
}

let calSeq = 0; // une réponse lente (ton portefeuille) ne remplace pas un choix fait après
async function refreshCalendar() {
  const seq = ++calSeq;
  if (!benchList.length) await loadBenchList();
  const select = $("cal-select");
  if (cal.choice.startsWith("ticker:")) addWatchedOption(cal.choice.slice(7), cal.label);
  select.value = cal.choice;
  if (select.value !== cal.choice) select.value = cal.choice = "portfolio";
  const params = cal.choice === "portfolio" ? addExclude(new URLSearchParams()) : new URLSearchParams(referenceParams(cal.choice) ?? {});
    const data = await api(`/api/calendar?${params}`);
  if (seq !== calSeq) return;
  const estimated = new Set(data.estimated);
  const values = Object.values(data.months);
  const scale = Math.max(1, ...values.map((v) => Math.abs(Number(v))));
  const head = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const t of ["", ...MONTHS, "Année"]) {
    const th = document.createElement("th");
    th.textContent = t;
    hr.append(th);
  }
  head.append(hr);
  const body = document.createElement("tbody");
  for (const year of Object.keys(data.years).sort().reverse()) {
    const tr = document.createElement("tr");
    tr.append(cell(year));
    MONTHS.forEach((_, i) => {
      const key = `${year}-${String(i + 1).padStart(2, "0")}`;
      tr.append(heatCell(data.months[key], scale, "", estimated.has(key)));
    });
    // Année entière d'avant ton premier achat : total estimé, gris lui aussi. Année à cheval :
    // le total n'enchaîne que les mois réels (le serveur laisse les mois gris de côté).
    const whole = data.estimated_years.includes(year);
    const mixed = !whole && [...estimated].some((k) => k.startsWith(year));
    const yearCell = heatCell(data.years[year], scale * 3, "year", whole);
    if (mixed) yearCell.title = "Mois réels seulement : les mois estimés (en gris) ne comptent pas";
    tr.append(yearCell);
    body.append(tr);
  }
  $("calendar").replaceChildren(head, body);
  if (!values.length) $("calendar").replaceChildren(cell(cal.choice === "portfolio" ? "Aucune opération pour l'instant." : "Pas encore assez d'historique."));
  const notes = [];
  if (estimated.size) notes.push("Cases grises : mois estimés d'avant ton premier achat (10 ans au plus).");
  if (data.currency !== "EUR") notes.push(`Rendements dans la devise de l'indice (${data.currency}), sans effet de change.`);
  $("cal-note").textContent = notes.join(" ");
  if (estimated.size) {
    $("cal-note").append(makeTip("Estimés avec le rendement de tes lignes actuelles, pondérées par leur prix de revient. Ils ne comptent dans « Année » que pour une année entièrement estimée."));
  }
}

$("cal-select").addEventListener("change", (e) => {
  cal.choice = e.target.value;
  cal.label = e.target.selectedOptions[0]?.textContent ?? "";
  savePref(CAL_KEY, cal);
  refreshCalendar().catch((err) => showMessage(err.message, true));
});

// Actif d'une liste de suivi affiché dans le calendrier : une option de plus, groupe à part.
function addWatchedOption(ticker, label) {
  const select = $("cal-select");
  const value = `ticker:${ticker}`;
  if ([...select.options].some((o) => o.value === value)) return;
  let group = select.querySelector("optgroup[data-watched]");
  if (!group) {
    group = document.createElement("optgroup");
    group.label = "Listes de suivi";
    group.dataset.watched = "";
    select.append(group);
  }
  group.append(new Option(label || ticker, value));
}

function showInCalendar(row) {
  const label = `${row.label} (${row.ticker})`;
  addWatchedOption(row.ticker, label);
  Object.assign(cal, { choice: `ticker:${row.ticker}`, label });
  savePref(CAL_KEY, cal);
  refreshCalendar().catch((err) => showMessage(err.message, true));
  document.querySelector('[data-card="rendements-mensuels"]').scrollIntoView({ behavior: "smooth", block: "start" });
}

// --- Visualisations : carte du portefeuille (treemap) ------------------------------------
// Plugin chartjs-chart-treemap copié dans web/vendor (pas de CDN). Surface = valeur en EUR de
// la ligne ; la couleur dit la variation du jour, la plus-value latente ou la couleur choisie.
// Suit le filtre « Lignes prises en compte », comme la Vue d'ensemble.

const TREEMAP_KEY = PREF + "carte";
const treemapPref = { area: "sqrt", ...loadPref(TREEMAP_KEY, { group: "account", color: "day" }) };

// Mélange de deux couleurs #rrggbb (le canvas ne comprend pas toujours color-mix()).
function mixHex(a, b, t) {
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [rgb(a), rgb(b)];
  return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(",")})`;
}

// Lignes de la carte : une par (compte, actif), avec de quoi regrouper et colorer.
function treemapRows(portfolio) {
  const assets = new Map(lists.assets.map((a) => [a.id, a]));
  return (portfolio?.positions ?? [])
    .filter((p) => p.value_eur !== null && Number(p.value_eur) > 0)
    .map((p) => ({
      asset: p.asset,
      account: p.account,
      class: p.asset_class,
      category: assets.get(p.asset_id)?.category || p.asset_class,
      value: Number(p.value_eur), // surface seulement : les textes viennent des chaînes
      // Surface adoucie (D-077) : racine carrée de la valeur, pour qu'une ligne très lourde
      // n'écrase pas les autres (90 % contre 1 % : surface 9,5 fois plus grande, pas 90).
      area: Math.sqrt(Number(p.value_eur)),
      value_text: formatDecimal(p.value_eur, "EUR"),
      weight: p.weight_pct,
      day: p.day_change_pct,
      gain: p.gain_pct,
      gain_eur: p.gain_eur,
    }));
}

function treemapFill(leaf, mode, scale) {
  if (mode === "line") {
    const name = lineColors().get(leaf.asset);
    return name ? css(name) : otherColor();
  }
  const pct = leaf[mode];
  if (pct === null || pct === undefined) return css("--neutral");
  const strength = Math.min(Math.abs(Number(pct)) / scale, 1);
  return mixHex(css("--surface"), css(Number(pct) < 0 ? "--ko" : "--ok"), 0.25 + strength * 0.75);
}

function renderTreemap() {
  const group = treemapPref.group;
  const mode = treemapPref.color;
  $("treemap-group").value = group;
  $("treemap-color").value = mode;
  $("treemap-area").value = treemapPref.area;
  const rows = treemapRows(overviewPortfolio);
  // Même échelle pour toute la carte : la plus forte variation visible donne la couleur la plus vive.
  const scale = Math.max(mode === "day" ? 1 : 5, ...rows.map((r) => Math.abs(Number(r[mode] ?? 0))));
  const leafOf = (ctx) => {
    const raw = ctx.raw;
    return raw && raw.l === (group ? 1 : 0) ? raw._data.children?.[0] : null;
  };
  const textColor = css("--text");
  drawChart("chart-treemap", {
    type: "treemap",
    data: {
      datasets: [{
        tree: rows,
        key: treemapPref.area === "value" ? "value" : "area",
        groups: group ? [group, "asset"] : ["asset"],
        spacing: 1,
        borderWidth: 1,
        borderColor: css("--surface"),
        backgroundColor: (ctx) => {
          if (ctx.type !== "data") return "transparent";
          const leaf = leafOf(ctx);
          return leaf ? treemapFill(leaf, mode, scale) : css("--surface-2") || css("--surface");
        },
        captions: { display: Boolean(group), color: textColor, font: { weight: "600" }, padding: 4 },
        labels: {
          display: true,
          color: textColor,
          overflow: "hidden",
          formatter: (ctx) => {
            const leaf = leafOf(ctx);
            if (!leaf) return "";
            const pct = mode === "line" ? leaf.weight : leaf[mode];
            const suffix = pct === null || pct === undefined ? "" : mode === "line" ? `${formatDecimal(pct)} %` : formatPct(pct);
            return suffix ? [leaf.asset, suffix] : leaf.asset;
          },
        },
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const leaf = leafOf(items[0]);
              return leaf ? `${leaf.asset}${group === "account" ? "" : ` · ${leaf.account}`}` : items[0].raw.g;
            },
            label: (item) => {
              const leaf = leafOf(item);
              if (!leaf) {
                const total = rows.filter((r) => r[group] === item.raw.g).reduce((t, r) => t + r.value, 0);
                return ` ${formatDecimal(total.toFixed(2), "EUR")}`;
              }
              const lines = [` Valeur : ${leaf.value_text}${leaf.weight ? ` (${formatDecimal(leaf.weight)} %)` : ""}`];
              if (leaf.day !== null) lines.push(` Jour : ${formatPct(leaf.day)}`);
              if (leaf.gain !== null) lines.push(` Plus-value latente : ${formatPct(leaf.gain)}${leaf.gain_eur ? ` (${formatDecimal(leaf.gain_eur, "EUR")})` : ""}`);
              return lines;
            },
          },
        },
      },
    },
  });
  const notes = [];
  if (!rows.length) notes.push("Aucune ligne valorisée pour l'instant.");
  if (group === "class" || group === "category") notes.push("Une même ligne détenue dans deux comptes forme une seule tuile.");
  if (treemapPref.area !== "value") notes.push("Surface adoucie : les grosses lignes paraissent moins grosses qu'en réalité ; la valeur exacte et le poids sont dans l'infobulle.");
  if (mode !== "line") notes.push("Vert : hausse, rouge : baisse, plus la couleur est vive, plus le mouvement est fort ; gris : pas de cours.");
  $("treemap-note").textContent = notes.join(" ");
}

for (const [id, key] of [["treemap-group", "group"], ["treemap-color", "color"], ["treemap-area", "area"]]) {
  $(id).addEventListener("change", (e) => {
    treemapPref[key] = e.target.value;
    savePref(TREEMAP_KEY, treemapPref);
    renderTreemap();
  });
}

// --- Visualisations : listes de suivi (D-072) ---------------------------------------------
// « Mes lignes » (actifs détenus, calculée) puis les listes créées, gardées en base.

const WATCH_KEY = PREF + "liste-suivi";
const watchPref = loadPref(WATCH_KEY, { choice: "lines" });
let watchLists = [];
const WATCH_PERIODS = ["1j", "1s", "1m", "ytd", "1a", "3a", "5a"];

const currentWatch = () => watchLists.find((w) => String(w.id) === watchPref.choice) ?? null;

function fillWatchSelect() {
  const select = $("watch-select");
  select.replaceChildren(new Option("Mes lignes", "lines"), ...watchLists.map((w) => new Option(`${w.name} (${w.items.length})`, String(w.id))));
  if (watchPref.choice !== "lines" && !currentWatch()) watchPref.choice = "lines";
  select.value = watchPref.choice;
  const own = currentWatch() !== null;
  $("watch-rename").hidden = $("watch-delete").hidden = $("form-watch-item").hidden = !own;
}

// L'ordre se règle à la poignée tant que le tableau est dans l'ordre choisi (aucun tri par colonne).
const watchOrderable = () => [null, "order"].includes(sorts["watch-table"].key);

function watchRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.id = row.id;
  if (watchOrderable()) tr.append(gripCell(row.label));
  else tr.append(document.createElement("td"));
  tr.append(cell(row.label), cell(row.ticker ?? "–"));
  const last = cell(row.last ? `${row.note ? "≈ " : ""}${formatDecimal(row.last, row.currency)}` : "–", "num");
  if (row.last_date) last.title = `Clôture du ${frDate(row.last_date)}`;
  if (row.note) last.title = row.note;
  tr.append(last);
  for (const key of WATCH_PERIODS) {
    const v = row[key];
    const td = cell(v === null || v === undefined ? "–" : formatPct(v), "num");
    if (v) td.classList.add(v.startsWith("-") ? "ko" : "ok");
    tr.append(td);
  }
  if (row.error) {
    tr.cells[2].textContent = "indisponible";
    tr.cells[2].title = row.error;
  }
  const actions = document.createElement("td");
  actions.className = "actions";
  if (row.ticker && !row.error) {
    const show = document.createElement("button");
    show.type = "button";
    show.className = "link edit";
    show.textContent = "Calendrier";
    show.onclick = () => showInCalendar(row);
    actions.append(show);
  }
  const list = currentWatch();
  if (list) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "link";
    del.textContent = "Retirer";
    del.onclick = () => watchAction(api(`/api/watchlists/${list.id}/items/${row.id}`, { method: "DELETE" }), `« ${row.label} » retiré de la liste.`);
    actions.append(del);
  }
  tr.append(actions);
  return tr;
}

let watchShown = null;
let watchSeq = 0; // une réponse lente ne remplace pas celle d'une liste choisie après
async function refreshWatch() {
  watchLists = await api("/api/watchlists");
  fillWatchSelect();
  const list = currentWatch();
  const seq = ++watchSeq;
  // Actualisation automatique de la même liste : on garde le tableau affiché en attendant.
  if (watchShown !== watchPref.choice) $("watch-rows").replaceChildren(emptyRow("Chargement des cours…"));
  watchShown = watchPref.choice;
  const data = await api(list ? `/api/watchlists/${list.id}/performance` : "/api/watchlists/lines/performance");
  if (seq !== watchSeq) return;
  const empty = list ? "Liste vide : ajoute un actif avec son ticker Yahoo." : "Aucune ligne cotée détenue pour l'instant.";
  fillSortable("watch-table", data.rows.map((r, i) => ({ ...r, order: i })), watchRow, empty);
}

// Glisser une ligne : « Mes lignes » suit l'ordre des actifs de « Gérer » (les lignes non
// affichées gardent leur place), une liste créée garde son propre ordre.
makeReorderable($("watch-rows"), "tr[data-id]", ".drag-handle", async (rows) => {
  const ids = rows.map((tr) => Number(tr.dataset.id));
  const list = currentWatch();
  try {
    if (list) {
      await api(`/api/watchlists/${list.id}/items/order`, { method: "PUT", body: JSON.stringify({ ids }) });
    } else {
      const shown = new Set(ids);
      const queue = [...ids];
      const full = lists.assets.map((a) => (shown.has(a.id) ? queue.shift() : a.id));
      await api("/api/assets/order", { method: "PUT", body: JSON.stringify({ ids: full }) });
      lists.assets.sort((a, b) => full.indexOf(a.id) - full.indexOf(b.id));
      renderLists(lists.accounts, lists.assets, lists.operations);
    }
    showMessage("Ordre enregistré.");
  } catch (err) {
    showMessage(err.message, true);
  }
  await refreshWatch().catch((err) => showMessage(err.message, true));
});

async function watchAction(promise, done) {
  try {
    await promise;
    if (done) showMessage(done);
    await refreshWatch();
  } catch (err) {
    showMessage(err.message, true);
  }
}

$("watch-select").addEventListener("change", (e) => {
  watchPref.choice = e.target.value;
  savePref(WATCH_KEY, watchPref);
  refreshWatch().catch((err) => showMessage(err.message, true));
});
$("watch-new").addEventListener("click", async () => {
  const name = (await askText("Nom de la nouvelle liste", { placeholder: "ex. ETF à surveiller", ok: "Créer" }))?.trim();
  if (!name) return;
  try {
    const created = await api("/api/watchlists", { method: "POST", body: JSON.stringify({ name }) });
    watchPref.choice = String(created.id);
    savePref(WATCH_KEY, watchPref);
    await watchAction(Promise.resolve(), `Liste « ${name} » créée : ajoute-lui des actifs.`);
    $("form-watch-item").label.focus();
  } catch (err) {
    showMessage(err.message, true);
  }
});
$("watch-rename").addEventListener("click", async () => {
  const list = currentWatch();
  const name = (await askText("Nouveau nom de la liste", { value: list.name, ok: "Renommer" }))?.trim();
  if (!name || name === list.name) return;
  watchAction(api(`/api/watchlists/${list.id}`, { method: "PATCH", body: JSON.stringify({ name }) }), "Liste renommée.");
});
$("watch-delete").addEventListener("click", async () => {
  const list = currentWatch();
  if (!await askConfirm(`Supprimer la liste « ${list.name} » et ses ${list.items.length} actifs suivis ?`)) return;
  watchPref.choice = "lines";
  savePref(WATCH_KEY, watchPref);
  watchAction(api(`/api/watchlists/${list.id}`, { method: "DELETE" }), "Liste supprimée.");
});
$("form-watch-item").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  const body = { label: form.label.value.trim(), ticker: form.ticker.value.trim(), crypto: form.kind.value === "crypto" };
  watchAction(api(`/api/watchlists/${currentWatch().id}/items`, { method: "POST", body: JSON.stringify(body) }), `« ${body.label} » ajouté.`).then(() => {
    const kind = form.kind.value;
    form.reset();
    form.kind.value = kind; // on ajoute souvent plusieurs cryptos d'affilée
    form.label.focus();
  });
});
// Crypto : l'id CoinGecko ou le symbole suffit, l'API trouve le ticker Yahoo (D-111).
$("form-watch-item").kind.addEventListener("change", (e) => {
  const crypto = e.target.value === "crypto";
  $("watch-ticker-label").textContent = crypto ? "Id CoinGecko ou symbole" : "Ticker Yahoo";
  e.target.form.ticker.placeholder = crypto ? "ex. bitcoin, BTC, BTC-EUR" : "ex. AI.PA, ^FCHI";
  e.target.form.label.placeholder = crypto ? "ex. Bitcoin" : "ex. Air Liquide";
});
bindSortable("watch-table", ["order", "last", ...WATCH_PERIODS]);

function refreshVisualisations() {
  renderTreemap();
  refreshWatch().catch((err) => {
    $("watch-rows").replaceChildren(emptyRow(`Listes indisponibles : ${err.message}`));
  });
  return refreshCalendar();
}

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
  $("bench-status").textContent = isLivret
    ? "Livret calculé sur ta machine à taux fixe, intérêts capitalisés chaque jour (très proche du calcul par quinzaine). Un livret réglementé est net d'impôt, ton PEA ne l'est qu'après 5 ans."
    : data.currency !== "EUR"
      ? `Indice coté en ${data.currency}, converti au taux du jour : l'effet de change passé n'est pas compté.`
      : "Indice en euros. Un indice « prix » ne compte pas les dividendes, ta performance si : l'écart te favorise donc un peu.";
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
$("bench-start").addEventListener("change", (e) => changeBench({ start: e.target.value || null }));
$("bench-end").addEventListener("change", (e) => changeBench({ end: e.target.value || null }));
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

// --- Pages (onglets) ---------------------------------------------------------------
// Une seule page visible à la fois ; l'adresse (#performance...) garde la page au
// rechargement et permet le bouton « Précédent » du navigateur.

const PAGES = {
  visualisations: refreshVisualisations, performance: refreshBenchmark, // la comparaison vit dans Performance (D-133)
  projection: () => Promise.all([refreshProjection(), refreshTargets()]),
  revenus: () => refreshIncome(), fiscalite: () => refreshTax(), crypto: () => refreshAave().finally(refreshRewards).finally(refreshBorrows),
};
// Anciennes adresses (favoris) : Calendrier, Allocation cible et Comparaison ont rejoint d'autres pages.
const PAGE_ALIASES = { calendrier: "visualisations", cible: "projection", comparaison: "performance" };
// Nom affiché d'un onglet (sans son icône ni sa pastille) ; la roue dentée n'a que son libellé.
const tabName = (a) => a.querySelector(".tab-label")?.textContent ?? a.getAttribute("aria-label") ?? "";
let currentPage = "apercu";

function showPage() {
  stopArranging(); // avant de lire le nom de l'onglet (les cases du réglage en font partie)
  const asked = location.hash.slice(1);
  if (PAGE_ALIASES[asked]) {
    history.replaceState(null, "", `#${PAGE_ALIASES[asked]}`);
    return showPage();
  }
  // Sans adresse : le premier onglet affiché, dans l'ordre choisi.
  const name = asked || $("tabs").querySelector("[data-tab]:not([hidden])").dataset.tab;
  currentPage = document.querySelector(`[data-page="${name}"]`) ? name : "apercu";
  for (const page of document.querySelectorAll("[data-page]")) page.hidden = page.dataset.page !== currentPage;
  for (const tab of document.querySelectorAll("[data-tab]")) {
    tab.classList.toggle("active", tab.dataset.tab === currentPage);
    tab.setAttribute("aria-current", tab.dataset.tab === currentPage ? "page" : "false");
  }
  const tab = document.querySelector(`[data-tab="${currentPage}"]`);
  $("current-page").textContent = tab ? tabName(tab) : "";
  // Fenêtre étroite : la barre d'onglets défile (D-115), l'onglet ouvert reste visible.
  const bar = $("tabs");
  if (tab && bar.scrollWidth > bar.clientWidth) bar.scrollLeft = tab.offsetLeft - bar.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2;
  closeMenu();
  renderFilterButton();
  resizeCharts();
  loadPage();
}

// Un graphique dessiné dans une page cachée (ou une carte repliée) a une taille nulle : on le recalcule.
function resizeCharts() {
  for (const canvas of document.querySelectorAll(`[data-page="${currentPage}"] canvas`)) charts[canvas.id]?.resize();
}

// --- Disposition des cartes (D-070) ------------------------------------------------------
// Chaque page range ses cartes (data-card) dans l'ordre choisi, gardé dans ce navigateur comme
// les autres réglages d'affichage. Les éléments fixes (Total, filtre, bandeaux) restent en haut.
// Mode « Réorganiser » : cartes repliées sur leur titre, déplaçables à la souris, au doigt ou
// au clavier ; hors de ce mode, rien ne bouge par accident.
const LAYOUT_KEY = PREF + "disposition";
const layout = loadPref(LAYOUT_KEY, {});
const defaultLayout = {};
const pageCards = (page) => [...page.children].filter((el) => el.matches(".card[data-card]"));

function applyLayout(page) {
  const name = page.dataset.page;
  const saved = layout[name] ?? defaultLayout[name];
  const cards = pageCards(page);
  const rank = (card) => {
    const i = saved.indexOf(card.dataset.card);
    return i < 0 ? saved.length + defaultLayout[name].indexOf(card.dataset.card) : i; // carte nouvelle : à la fin
  };
  const bar = page.querySelector(":scope > .layout-bar");
  for (const card of cards.sort((a, b) => rank(a) - rank(b))) page.insertBefore(card, bar);
}

function stopArranging() {
  for (const page of document.querySelectorAll(".page.arranging")) {
    page.classList.remove("arranging");
    for (const card of pageCards(page)) {
      card.removeAttribute("tabindex");
      card.removeAttribute("aria-label");
    }
    page.querySelector(".layout-bar").replaceChildren(arrangeButton(page));
  }
  stopArrangingTabs();
  resizeCharts();
}

// « Réorganiser » : les cartes de la page (s'il y en a au moins deux) et la barre d'onglets.
function arrangeButton(page) {
  const button = smallButton("Réorganiser", "Changer l'ordre des cartes de cette page et des onglets, masquer un onglet", () => {
    stopArranging();
    page.classList.add("arranging");
    const movable = pageCards(page).length >= 2;
    for (const card of movable ? pageCards(page) : []) {
      card.tabIndex = 0;
      card.setAttribute("aria-label", `Carte « ${card.querySelector("h2").textContent} » : flèches pour la déplacer`);
    }
    startArrangingTabs();
    const help = document.createElement("span");
    help.className = "muted small";
    help.textContent = movable
      ? "Glisse les cartes et les onglets à la souris ou au doigt (au clavier : Tab puis flèches) ; décoche un onglet pour le masquer."
      : "Glisse les onglets à la souris ou au doigt (au clavier : Tab puis flèches) ; décoche un onglet pour le masquer.";
    const buttons = [help];
    if (movable) {
      buttons.push(smallButton("Cartes par défaut", "Revenir à l'ordre d'origine des cartes de cette page", () => {
        delete layout[page.dataset.page];
        savePref(LAYOUT_KEY, layout);
        applyLayout(page);
      }, ""));
    }
    buttons.push(
      smallButton("Onglets par défaut", "Tous les onglets affichés, dans l'ordre d'origine", () => {
        Object.assign(tabsOrder, { order: null, hidden: [] });
        savePref(TABS_KEY, tabsOrder);
        applyTabsOrder();
      }, ""),
      smallButton("Terminé", "Quitter le mode Réorganiser", stopArranging, "primary"),
    );
    page.querySelector(".layout-bar").replaceChildren(...buttons);
    (movable ? pageCards(page)[0] : movableTabs()[0]).focus();
  });
  return button;
}

// Toutes les pages ont le bouton : même une page d'une seule carte permet de régler les onglets.
for (const page of document.querySelectorAll(".page[data-page]")) {
  defaultLayout[page.dataset.page] = pageCards(page).map((c) => c.dataset.card);
  const bar = document.createElement("div");
  bar.className = "layout-bar";
  bar.append(arrangeButton(page));
  page.append(bar);
  if (pageCards(page).length < 2) continue; // une seule carte : rien à réordonner
  applyLayout(page);
  makeReorderable(page, ".card[data-card]", ".page.arranging > .card[data-card]", (cards) => {
    layout[page.dataset.page] = cards.map((c) => c.dataset.card);
    savePref(LAYOUT_KEY, layout);
  });
}

// --- Ordre et masquage des onglets (D-072, D-073) --------------------------------------------
// Réglés dans le mode « Réorganiser », gardés dans ce navigateur comme la disposition des
// cartes ; « Gérer » reste en dernier et toujours affiché. Pendant le réglage, les onglets
// masqués réapparaissent grisés, avec une case pour les afficher de nouveau.
const TABS_KEY = PREF + "onglets";
const tabsOrder = loadPref(TABS_KEY, { order: null, hidden: [] });
if (!Array.isArray(tabsOrder.hidden)) tabsOrder.hidden = [];
const movableTabs = () => [...$("tabs").querySelectorAll("[data-tab]")].filter((a) => a.dataset.tab !== "gerer");
const defaultTabs = movableTabs().map((a) => a.dataset.tab);

function applyTabsOrder() {
  const saved = Array.isArray(tabsOrder.order) ? tabsOrder.order : defaultTabs;
  const rank = (a) => {
    const i = saved.indexOf(a.dataset.tab);
    return i < 0 ? saved.length + defaultTabs.indexOf(a.dataset.tab) : i; // onglet nouveau : à la fin
  };
  const gerer = $("tabs").querySelector('[data-tab="gerer"]');
  const arranging = $("tabs").classList.contains("arranging");
  for (const a of movableTabs().sort((x, y) => rank(x) - rank(y))) {
    $("tabs").insertBefore(a, gerer);
    const masked = tabsOrder.hidden.includes(a.dataset.tab);
    a.hidden = masked && !arranging;
    a.classList.toggle("masked", masked);
    const box = a.querySelector(".tab-shown");
    if (box) box.checked = !masked;
  }
}

function startArrangingTabs() {
  const tabs = $("tabs");
  tabs.classList.add("arranging", "open"); // petit écran : le menu s'ouvre pour montrer les onglets
  for (const a of movableTabs()) {
    const name = tabName(a);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "tab-shown";
    box.setAttribute("aria-label", `Afficher l'onglet « ${name} »`);
    box.addEventListener("change", () => {
      const others = tabsOrder.hidden.filter((t) => t !== a.dataset.tab);
      tabsOrder.hidden = box.checked ? others : [...others, a.dataset.tab];
      savePref(TABS_KEY, tabsOrder);
      applyTabsOrder();
    });
    a.prepend(box);
    a.setAttribute("aria-label", `Onglet « ${name} » : flèches pour le déplacer`);
  }
  applyTabsOrder();
}

function stopArrangingTabs() {
  const tabs = $("tabs");
  if (!tabs.classList.contains("arranging")) return;
  tabs.classList.remove("arranging", "open");
  for (const box of tabs.querySelectorAll(".tab-shown")) box.remove();
  for (const a of movableTabs()) a.removeAttribute("aria-label");
  applyTabsOrder();
}

// Pendant le réglage, un clic sur un onglet ne change pas de page (la case, elle, reste cochable).
$("tabs").addEventListener("click", (e) => {
  if ($("tabs").classList.contains("arranging") && !e.target.matches(".tab-shown")) e.preventDefault();
}, true);
makeReorderable($("tabs"), 'a[data-tab]:not([data-tab="gerer"])', '#tabs.arranging a[data-tab]', (tabs, moved) => {
  tabsOrder.order = tabs.map((a) => a.dataset.tab);
  savePref(TABS_KEY, tabsOrder);
  moved.focus();
});
applyTabsOrder();

function loadPage() {
  PAGES[currentPage]?.().catch((err) => showMessage(err.message, true));
}

window.addEventListener("hashchange", showPage);

// Menu « hamburger » des petits écrans : le même bloc d'onglets, affiché en liste à la demande.
function closeMenu() {
  $("tabs").classList.remove("open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
}
$("menu-toggle").addEventListener("click", () => {
  const open = $("tabs").classList.toggle("open");
  $("menu-toggle").setAttribute("aria-expanded", String(open));
});
$("tabs").addEventListener("click", (e) => {
  // Même page re-cliquée : pas de hashchange. Pendant « Réorganiser », le menu reste ouvert.
  if (e.target.closest("a") && !$("tabs").classList.contains("arranging")) closeMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
});

let ttlMinutes = 5;

async function refresh(force = false) {
  // Prochaine actualisation automatique : le serveur ne rappelle les sources qu'une fois
  // le cache expiré, donc ce minuteur ne génère pas d'appels en trop.
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(
    () => refresh().catch((err) => showMessage(err.message, true)),
    ttlMinutes * 60 * 1000,
  );
  const [accounts, assets, operations] = await Promise.all([
    api("/api/accounts"),
    api("/api/assets"),
    api("/api/operations"),
  ]);
  renderLists(accounts, assets, operations);
  renderLineFilter();
  refreshDebts().catch((err) => showMessage(err.message, true)); // sans réseau : immédiat
  refreshRecurring().catch((err) => showMessage(err.message, true)); // sans réseau : immédiat
  const portfolio = await api(force ? "/api/portfolio?force=true" : "/api/portfolio");
  ttlMinutes = portfolio.price_ttl_minutes;
  // Filtre actif : un second appel, servi par le cache des cours, pour la Vue d'ensemble.
  const view = filterActive() ? await api(`/api/portfolio?${addExclude(new URLSearchParams())}`) : portfolio;
  renderPortfolio(portfolio, view);
  renderFilterButton();
  // Après le portefeuille : la courbe finit au cours qui vient d'être mis en cache.
  refreshHistory()
    .catch((err) => {
      $("history-status").textContent = `Évolution indisponible : ${err.message}`;
    })
    .then(() => refreshPerformance()) // après l'historique : les clôtures sont déjà à jour
    .catch((err) => {
      $("perf-status").textContent = `Performance indisponible : ${err.message}`;
    })
    .then(loadPage); // page ouverte (calendrier, comparaison...) mise à jour elle aussi
}

async function deleteOperation(op) {
  if (!await askConfirm(`Supprimer l'opération du ${op.op_date} ?`)) return;
  try {
    await api(`/api/operations/${op.id}`, { method: "DELETE" });
    showMessage("Opération supprimée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

const orNull = (v) => (v && v.trim() !== "" ? v.trim() : null);

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
    ? css(SERIES[Number(form.color.value) - 1])
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
bindSortable("tax-av-table", ["deposits"]);
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

checkHealth();
setInterval(checkHealth, 60000);

// --- Avis de mise à jour (D-129) -----------------------------------------------------------
// Le serveur demande à GitHub le numéro de la dernière version, une fois par jour au plus et
// seulement si l'utilisateur l'a accepté ; la page ne contacte jamais GitHub elle-même. « Plus
// tard » masque cette version-là dans ce navigateur, la suivante s'affichera.
const UPDATE_KEY = PREF + "mise-a-jour";
const updatePref = loadPref(UPDATE_KEY, { later: null });

function frenchDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("fr-FR");
}

function renderUpdates(data) {
  const page = data.newer && data.url?.startsWith(`${data.releases}/tag/`) ? data.url : null;
  $("update-ask").hidden = data.enabled !== null;
  $("update-banner").hidden = !(data.enabled === true && page && updatePref.later !== data.latest);
  if (page) {
    $("update-latest").textContent = data.latest;
    $("update-current").textContent = data.current;
    $("update-link").href = page;
  }
  $("updates-enabled").checked = data.enabled === true;
  $("updates-releases").href = data.releases;
  const when = data.checked ? ` (vérifié le ${frenchDate(data.checked)})` : "";
  const state = $("updates-state");
  if (page) {
    const link = Object.assign(document.createElement("a"), {
      href: page, target: "_blank", rel: "noopener noreferrer", className: "link-plain",
      textContent: "Voir la version",
    });
    state.replaceChildren(`Version ${data.latest} disponible, tu as la ${data.current}${when}. `, link);
  } else if (data.error) {
    state.textContent = `Vérification impossible : ${data.error}${when}.`;
  } else if (data.checked) {
    state.textContent = data.latest
      ? `À jour, version ${data.current}${when}.`
      : `Aucune version publiée pour l'instant${when}.`;
  } else {
    state.textContent = data.enabled ? "Pas encore vérifié." : "Aucune demande envoyée à GitHub.";
  }
}

async function chooseUpdates(enabled) {
  try {
    await api("/api/updates", { method: "PUT", body: JSON.stringify({ enabled }) });
    // Oui : la vérification du jour part tout de suite (GET), sans attendre le prochain lancement.
    renderUpdates(await api("/api/updates"));
  } catch (err) {
    showMessage(err.message, true);
    api("/api/updates").then(renderUpdates).catch(() => {}); // case remise sur l'état réel
  }
}

document.querySelectorAll("[data-updates]").forEach((button) => {
  button.addEventListener("click", () => chooseUpdates(button.dataset.updates === "yes"));
});
$("updates-enabled").addEventListener("change", (event) => chooseUpdates(event.target.checked));
$("update-later").addEventListener("click", () => {
  updatePref.later = $("update-latest").textContent;
  savePref(UPDATE_KEY, updatePref);
  $("update-banner").hidden = true;
});
$("updates-check").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try {
    renderUpdates(await api("/api/updates/check", { method: "POST" }));
  } catch (err) {
    showMessage(err.message, true);
  } finally {
    event.target.disabled = false;
  }
});
api("/api/updates").then(renderUpdates).catch(() => {}); // serveur injoignable : signalé par checkHealth

// Carte Réglages (D-130) : ce que .env réglait. Seuls les champs changés sont envoyés ; la clé
// CoinGecko n'est jamais renvoyée par le serveur, le champ reste vide une fois enregistrée.
const SETTINGS_NUMBERS = {
  price_ttl_minutes: "Cache des prix",
  backup_keep: "Sauvegardes gardées",
  profile_lock_minutes: "Verrouillage",
};
const settingsForm = $("form-settings");
let settingsFields = null;

function settingsState(text, isError = false) {
  const state = $("settings-state");
  state.textContent = text;
  state.classList.toggle("ko", isError);
  state.hidden = !text;
}

function renderSettings(data) {
  settingsFields = data.fields;
  for (const [name, field] of Object.entries(data.fields)) {
    const input = settingsForm.elements[name];
    const note = settingsForm.querySelector(`[data-locked="${name}"]`);
    if (!input || !note) continue;
    input.disabled = Boolean(field.locked);
    note.hidden = !field.locked;
    note.textContent = field.locked === ".env" ? "Défini dans le fichier .env." : "Défini dans l'environnement.";
    if (name === "coingecko_api_key") {
      input.value = "";
      input.placeholder = field.set ? "Clé enregistrée" : "Facultative";
    } else {
      input.value = field.value ?? "";
      if (name in SETTINGS_NUMBERS) input.placeholder = String(field.default);
    }
  }
  const key = data.fields.coingecko_api_key;
  $("settings-key-remove").hidden = !key.set || Boolean(key.locked);
}

async function saveSettings(changes, done) {
  try {
    const data = await api("/api/settings", { method: "PUT", body: JSON.stringify(changes) });
    renderSettings(data);
    settingsState(data.message ? `${done} ${data.message}` : done);
  } catch (err) {
    settingsState(err.message, true);
  }
}

settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!settingsFields) return;
  const changes = {};
  for (const [name, label] of Object.entries(SETTINGS_NUMBERS)) {
    const input = settingsForm.elements[name];
    const field = settingsFields[name];
    if (input.disabled) continue;
    const text = input.value.trim();
    if (!input.validity.badInput && text === String(field.value ?? "")) continue;
    const value = text === "" && !input.validity.badInput ? null : Number(text);
    if (value !== null && !(Number.isInteger(value) && value >= field.min && value <= field.max)) {
      settingsState(`${label} : un nombre entier de ${field.min} à ${field.max}.`, true);
      input.focus();
      return;
    }
    changes[name] = value;
  }
  const folder = settingsForm.elements.backup_copy_dir;
  if (!folder.disabled && folder.value.trim() !== (settingsFields.backup_copy_dir.value ?? "")) {
    changes.backup_copy_dir = folder.value.trim(); // vide : plus de copie
  }
  const key = settingsForm.elements.coingecko_api_key;
  if (!key.disabled && key.value.trim()) changes.coingecko_api_key = key.value.trim();
  if (!Object.keys(changes).length) {
    settingsState("Rien à enregistrer.");
    return;
  }
  saveSettings(changes, "Enregistré.");
});
$("settings-key-remove").addEventListener("click", () => {
  saveSettings({ coingecko_api_key: "" }, "Clé retirée.");
});
api("/api/settings").then(renderSettings).catch(() => {});
refresh().catch((err) => showMessage(err.message, true));
// Avant le premier affichage : la page Fiscalité les lit dès l'ouverture sur #fiscalite.
const TAX_KEY = PREF + "fiscalite";
const taxPref = { tmi: "30", ceiling: "", ...loadPref(TAX_KEY, {}) };
showPage();

// --- Import CSV (D-053) ------------------------------------------------------------------
// Le fichier est lu par le navigateur et envoyé au serveur local (127.0.0.1) à chaque aperçu ;
// il n'est gardé qu'en mémoire, dans cette page, jusqu'à la fermeture de la fenêtre.

const IMPORT_FIELDS = {
  date: "Date *", type: "Type d'opération *", asset: "Actif (ISIN, ticker ou nom)", asset_name: "Nom de l'actif",
  quantity: "Quantité",
  unit_price: "Prix unitaire", amount: "Montant", fees: "Frais", currency: "Devise", note: "Note",
};
const IMPORT_STATUS = {
  ok: "À importer", erreur: "Erreur", actif_inconnu: "Actif inconnu", sans_compte: "Sans compte",
  deja_importee: "Déjà importée", deja_saisie: "Déjà saisie", ignoree: "Ignorée",
};
const IMPORT_MAP_KEY = PREF + "import"; // correspondances retenues, par liste de colonnes
// `profile` : null = reconnu d'après les en-têtes, "generique" = forcé ; `accounts` : compte
// choisi par partie du relevé ; `plans` : plans d'investissement cochés (D-071).
const importState = { text: null, accountId: null, mapping: null, columns: [], ready: 0, profile: null, accounts: {}, splits: {}, destinationTexts: {}, outside: {}, validated: new Set(), plans: new Set(), seenPlans: new Set() };

// UTF-8 d'abord ; sinon Windows-1252, l'encodage des CSV enregistrés par Excel en français.
function decodeFile(buffer) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer).replace(/^\uFEFF/, "");
  } catch {
    return new TextDecoder("windows-1252").decode(buffer);
  }
}

$("form-import-start").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const file = form.file.files[0];
  if (!file) {
    showMessage("Choisis d'abord un fichier, ou utilise « Coller depuis un tableur ».", true);
    return;
  }
  if (file.size > 2_000_000) {
    showMessage("Fichier trop gros (2 Mo au plus) : découpe-le par années.", true);
    return;
  }
  Object.assign(importState, {
    text: decodeFile(await file.arrayBuffer()),
    accountId: form.account_id.value ? Number(form.account_id.value) : null,
    mapping: null,
    profile: null,
    accounts: {},
    splits: {},
    destinationTexts: {},
    outside: {},
    validated: new Set(),
    plans: new Set(),
    seenPlans: new Set(),
  });
  $("import-header").checked = true;
  $("import-paste-zone").hidden = true;
  importState.title = `Import de ${file.name}`;
  importState.accountName = form.account_id.value ? form.account_id.selectedOptions[0]?.text : null;
  form.file.value = ""; // le même fichier pourra être relu après correction
  // Fenêtre ouverte tout de suite, avec un message d'attente : l'aperçu la remplit ensuite.
  $("dlg-import-title").textContent = importState.title;
  renderImport({ profile: null, columns: [], mapping: {}, parts: [], rows: [], ignored: [], counts: {}, plans: [] });
  $("import-generic").hidden = true;
  $("dlg-import").showModal();
  try {
    await previewImport();
  } catch (err) {
    $("dlg-import").close();
    showMessage(err.message, true);
  }
});

// Lignes copiées depuis Excel ou LibreOffice : du texte séparé par des tabulations, que le
// serveur lit exactement comme un fichier CSV (même aperçu, mêmes vérifications).
$("import-paste-open").addEventListener("click", () => {
  const form = $("form-import-start");
  if (!form.account_id.value) {
    showMessage("Crée d'abord un compte.", true);
    return;
  }
  Object.assign(importState, { text: null, title: null, accountId: Number(form.account_id.value), mapping: null, profile: "generique", accounts: {}, splits: {}, destinationTexts: {}, outside: {}, validated: new Set(), plans: new Set(), seenPlans: new Set() });
  $("import-header").checked = true;
  $("import-paste").value = "";
  $("import-paste-zone").hidden = false;
  $("dlg-import-title").textContent = `Coller des opérations dans ${form.account_id.selectedOptions[0]?.text ?? "le compte"}`;
  renderImport({ profile: null, columns: [], mapping: {}, parts: [], rows: [], ignored: [], counts: {}, plans: [] });
  $("import-summary").textContent = "Colle tes lignes ci-dessus : l'aperçu s'affiche aussitôt.";
  $("dlg-import").showModal();
  $("import-paste").focus();
});

let pasteTimer = null;
$("import-paste").addEventListener("input", () => {
  clearTimeout(pasteTimer);
  pasteTimer = setTimeout(() => {
    const text = $("import-paste").value;
    const firstLine = text.split("\n")[0];
    // Nouvelles colonnes : nouvelle proposition de correspondance.
    if (firstLine !== importState.firstLine) importState.mapping = null;
    Object.assign(importState, { text: text.trim() ? text : null, firstLine });
    if (!importState.text) return;
    previewImport().catch((err) => showMessage(err.message, true));
  }, 300);
});

// Modèle à remplir dans un tableur : en-têtes reconnus d'office, une ligne d'exemple FICTIVE.
// Séparateur « ; » et BOM UTF-8 : Excel français l'ouvre directement avec les accents.
$("import-template").addEventListener("click", (event) => {
  event.preventDefault();
  const lines = [
    "Date;Type;ISIN ou ticker;Nom;Quantité;Prix unitaire;Montant;Frais;Note",
    "02/01/2025;Versement;;;;;1000;;exemple fictif, à remplacer",
    "03/01/2025;Achat;FR0000000001;ETF exemple;10;50,00;;1,00;exemple fictif, à remplacer",
  ];
  const blob = new Blob(["\uFEFF" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "modele-operations.csv";
  link.click();
  URL.revokeObjectURL(link.href);
});

function importBody() {
  // Parts de retrait : seules les lignes complètes (compte choisi, quantité positive) partent.
  const splits = {};
  for (const [line, legs] of Object.entries(importState.splits)) {
    const done = legs.filter((l) => l.account_id && /^\d+(\.\d+)?$/.test(l.value) && Number(l.value) > 0)
      .map((l) => ({ account_id: Number(l.account_id), value: l.value }));
    if (done.length) splits[line] = done;
  }
  return JSON.stringify({
    account_id: importState.accountId,
    text: importState.text,
    mapping: importState.mapping,
    header: $("import-header").checked,
    profile: importState.profile,
    accounts: importState.accounts,
    splits,
    destination_texts: importState.destinationTexts,
    plans: [...importState.plans],
  });
}

// Barre de lecture (D-089) : avance avec la progression envoyée par le serveur ; sans pourcentage
// (enregistrement), elle défile. Cachée dès la fin.
function setProgress(percent, label = "") {
  const bar = document.querySelector(".import-progress-bar");
  bar.classList.toggle("indeterminate", percent === null);
  bar.style.setProperty("--p", `${percent ?? 100}%`);
  $("import-progress-label").textContent = label;
  $("import-progress").hidden = false;
}
const hideProgress = () => { $("import-progress").hidden = true; };

// Aperçu avec progression : le serveur répond en lignes JSON (`progress`, puis `result` ou `error`).
async function previewStream(body, onProgress) {
  const res = await fetch("/api/import/preview/stream", { method: "POST", headers: profileHeaders(), body });
  checkProfile(res);
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new Error(Array.isArray(data.detail) ? data.detail.map((e) => `${e.loc.at(-1)} : ${e.msg}`).join(" ; ") : data.detail ?? "Aperçu impossible pour l'instant.");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let result = null;
  const handle = (line) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.error) throw new Error(event.error);
    if (event.result) result = event.result;
    else onProgress(event.progress, event.label);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    const lines = pending.split("\n");
    pending = lines.pop();
    lines.forEach(handle);
  }
  handle(pending);
  if (!result) throw new Error("Aperçu impossible pour l'instant.");
  return result;
}

// Aperçu en cours : message d'attente, bouton Importer bloqué ; seule la dernière demande compte.
let previewRun = 0;
async function previewImport() {
  const run = ++previewRun;
  $("dlg-import").classList.add("busy");
  $("import-go").disabled = true;
  setProgress(0, "Lecture du fichier");
  $("import-summary").textContent = "";
  try {
    await runPreview(run);
  } catch (err) {
    if (run === previewRun) $("import-summary").textContent = "Aperçu impossible pour l'instant : corrige ou réessaie.";
    throw err;
  } finally {
    if (run === previewRun) {
      hideProgress();
      $("dlg-import").classList.remove("busy");
    }
  }
}

async function runPreview(run) {
  const memory = loadPref(IMPORT_MAP_KEY, {});
  const onProgress = (percent, label) => { if (run === previewRun) setProgress(percent, label); };
  let data = await previewStream(importBody(), onProgress);
  if (run !== previewRun) return;
  // Fichier déjà vu (mêmes colonnes) : on reprend la correspondance choisie la dernière fois.
  const remembered = memory[data.columns.join("|")];
  if (!importState.mapping && remembered) {
    importState.mapping = remembered;
    data = await previewStream(importBody(), onProgress);
    if (run !== previewRun) return;
  }
  importState.mapping = data.mapping;
  importState.columns = data.columns;
  // Plan vu pour la première fois : coché s'il est encore actif et créable.
  for (const plan of data.plans) {
    if (importState.seenPlans.has(plan.key) || plan.problem) continue;
    importState.seenPlans.add(plan.key);
    if (plan.active) importState.plans.add(plan.key);
  }
  if (importState.title) {
    // Le relevé reconnu s'affiche dessous : le titre ne garde que le fichier (et le compte d'un CSV quelconque).
    const where = !data.profile && importState.accountName ? ` dans ${importState.accountName}` : "";
    $("dlg-import-title").textContent = `${importState.title}${where}`;
  }
  renderImport(data);
}

// Parties du relevé (PEA, compte-titres...) : un compte de l'app chacune, ou « Créer ».
function renderImportParts(parts) {
  const offered = new Set(); // un seul bouton par compte proposé (obligations suivent le CTO)
  $("import-parts").replaceChildren(...parts.map((part) => {
    const row = document.createElement("div");
    row.className = "import-part";
    const label = document.createElement("label");
    const select = document.createElement("select");
    select.append(new Option("Choisir un compte", ""), ...lists.accounts.map((a) => new Option(`${a.name} (${kindLabel(a)})`, a.id)));
    select.value = part.account_id ?? "";
    select.addEventListener("change", () => {
      importState.accounts = { ...importState.accounts, [part.part]: select.value === "" ? null : Number(select.value) };
      previewImport().catch((err) => showMessage(err.message, true));
    });
    label.append(`${part.label} · ${part.count} opération${part.count > 1 ? "s" : ""}`, select);
    row.append(label);
    // Pas encore de compte de ce nom : création pré-remplie (nom, type, établissement).
    if (!part.account_id && !offered.has(part.suggested_name) && !lists.accounts.some((a) => a.name === part.suggested_name)) {
      offered.add(part.suggested_name);
      // Choix « aucun compte » oublié : le compte créé sera retenu à l'aperçu suivant (D-083).
      row.append(zoomChip(`Créer « ${part.suggested_name} »`, false, () => {
        for (const p of parts) if (p.suggested_name === part.suggested_name) delete importState.accounts[p.part];
        openAccount(null, { name: part.suggested_name, kind: part.kind, institution: part.institution });
      }));
    }
    return row;
  }));
}

function planLabel(plan) {
  const when = plan.frequency === "hebdomadaire" ? `chaque ${WEEKDAYS[plan.day - 1]}`
    : plan.frequency === "bimensuel" ? `un ${WEEKDAYS[plan.day - 1]} sur deux`
      : plan.frequency === "trimestriel" ? `le ${dayLabel(plan.day)}, chaque trimestre`
        : plan.frequency === "mensuel" ? `le ${dayLabel(plan.day)} de chaque mois` : "rythme irrégulier";
  const runs = `${plan.count} exécution${plan.count > 1 ? "s" : ""} du ${frDate(plan.first)} au ${frDate(plan.last)}`;
  return `${plan.asset_name ?? plan.asset} · ${formatDecimal(plan.amount, "EUR")} ${when} · ${runs}${plan.active ? "" : " (arrêté ?)"}`;
}

function renderImportPlans(plans) {
  $("import-plans-box").hidden = !plans.length;
  $("import-plans").replaceChildren(...plans.map((plan) => {
    const li = document.createElement("li");
    const label = document.createElement("label");
    label.className = "check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.disabled = Boolean(plan.problem);
    box.checked = !plan.problem && importState.plans.has(plan.key);
    box.addEventListener("change", () => {
      if (box.checked) importState.plans.add(plan.key);
      else importState.plans.delete(plan.key);
      updateImportButton();
    });
    label.append(box, planLabel(plan));
    li.append(label);
    if (plan.problem) {
      const why = document.createElement("span");
      why.className = "pill off";
      why.textContent = plan.problem;
      li.append(why);
    }
    return li;
  }));
}

function updateImportButton() {
  const ops = importState.ready;
  const plans = [...importState.plans].filter((k) => importState.creatablePlans?.has(k)).length;
  const parts = [];
  if (ops) parts.push(`${ops} opération${ops > 1 ? "s" : ""}`);
  if (plans) parts.push(`${plans} plan${plans > 1 ? "s" : ""}`);
  const pending = pendingOutflows();
  $("import-go").disabled = !parts.length || pending > 0;
  $("import-go").textContent = parts.length ? `Importer ${parts.join(" et ")}` : "Importer";
  $("import-pending").textContent = !outflowRows(lastImportData).length ? "" : pending ? `${pending} à valider` : "Tout est validé";
}

function renderImport(data) {
  const profile = Boolean(data.profile);
  $("import-generic").hidden = profile;
  $("import-profile").hidden = !profile;
  if (profile) {
    $("import-profile-label").textContent = `✓ Relevé ${data.profile.label}`;
    renderImportParts(data.parts);
  }
  importState.creatablePlans = new Set(data.plans.filter((p) => !p.problem).map((p) => p.key));
  renderImportPlans(data.plans);
  renderOutflows(data);
  const selects = Object.entries(IMPORT_FIELDS).map(([field, label]) => {
    const select = document.createElement("select");
    select.append(new Option("—", ""), ...data.columns.map((c, i) => new Option(c || `Colonne ${i + 1}`, i)));
    select.value = data.mapping[field] ?? "";
    select.addEventListener("change", () => {
      importState.mapping = { ...importState.mapping, [field]: select.value === "" ? null : Number(select.value) };
      previewImport().catch((err) => showMessage(err.message, true));
    });
    const wrap = document.createElement("label");
    wrap.append(label, select);
    return wrap;
  });
  $("import-mapping").replaceChildren(...selects);

  // Actifs inconnus : un bouton par valeur, qui ouvre « Ajouter un actif » pré-rempli.
  const unknown = new Map(data.rows.filter((r) => r.status === "actif_inconnu").map((r) => [r.asset, r]));
  const openers = [];
  const buttons = [...unknown].map(([value, row]) => {
    const name = row.asset_name;
    const isin = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/.test(value.toUpperCase());
    const preset = isin ? { isin: value.toUpperCase(), name: name ?? "" } : { name: name ?? value };
    // Classe devinée d'après le relevé, devise des montants du relevé.
    if (row.asset_class) preset.asset_class = row.asset_class;
    if (row.currency) preset.currency = row.currency;
    // Crypto : cotée en dollars par défaut, les montants du relevé sont convertis (D-085).
    if (row.asset_class === "crypto") preset.currency = "USD";
    // Une action américaine achetée en euros a aussi une cotation en euros : c'est celle-là qu'il
    // faut, sinon ses lignes sont refusées (devise différente de celle du relevé).
    // Crypto d'un relevé Binance (D-082) : id CoinGecko proposé pour les plus courantes ; sinon
    // le nom reste le symbole, c'est lui qui relie les lignes du relevé à l'actif.
    if (row.asset_class === "crypto" && !isin) {
      if (name && name !== value) {
        preset.coingecko_id = value;
        preset.hint = `Id CoinGecko proposé pour ${name} : ${value}. Vérifie-le avec « Tester le cours » avant d'enregistrer.`;
      } else {
        preset.hint = `Garde le nom « ${value} » (le symbole du relevé) et cherche l'id CoinGecko sur coingecko.com : c'est la fin de l'adresse de la page de la crypto.`;
      }
    } else if (row.ticker) {
      preset.ticker = row.ticker;
      preset.hint = `Ticker Yahoo proposé : ${row.ticker} (cotation en ${row.currency}). Vérifie-le avec « Tester le cours » avant d'enregistrer.`;
    } else if (isin && row.currency && ["action", "etf", "fonds"].includes(row.asset_class ?? "etf")) {
      preset.hint = `Ton relevé est en ${row.currency} : « Chercher le ticker (ISIN) » propose les cotations de ce titre, celles en ${row.currency} d'abord (seul l'ISIN part chez Yahoo). Garde une cotation en ${row.currency}, sinon ses lignes seront refusées.`;
    } else if (row.currency && ["action", "etf"].includes(row.asset_class ?? "etf")) {
      preset.hint = `Ton relevé est en ${row.currency} : choisis le ticker Yahoo de la cotation en ${row.currency} de ce titre et laisse la devise à ${row.currency}. Exemple : pour Apple, APC.DE (Francfort, en euros) et non AAPL (New York, en dollars). Vérifie sur finance.yahoo.com en cherchant l'ISIN : la devise est écrite sous le nom du titre. Sinon ses lignes seront refusées.`;
    }
    const open = () => openAsset(null, preset);
    openers.push(open);
    return zoomChip(`Créer « ${name && name !== value ? `${name} · ${value}` : value} »`, false, open);
  });
  if (buttons.length) {
    const label = document.createElement("span");
    label.className = "muted small";
    label.textContent = "Actifs inconnus :";
    buttons.unshift(label);
    // Plusieurs actifs à créer : « Tout créer » ouvre les formulaires l'un après l'autre.
    if (openers.length > 1) {
      const all = zoomChip(`Tout créer (${openers.length})`, true, () => {
        importState.chain = true;
        openers[0]();
      });
      buttons.splice(1, 0, all);
    }
  }
  // Actif connu mais coté dans une autre devise que le relevé (D-083) : bouton « Modifier »,
  // devise du relevé pré-remplie ; ses opérations déjà saisies seront converties.
  const mismatched = new Map(data.rows
    .filter((r) => r.status === "erreur" && r.asset_id && r.currency && /^devise /.test(r.message ?? ""))
    .map((r) => [r.asset_id, r.currency]));
  const edits = [...mismatched].flatMap(([id, currency]) => {
    const asset = lists.assets.find((a) => a.id === id);
    if (!asset || asset.currency === currency) return [];
    return [zoomChip(`Modifier « ${asset.name} » : ${asset.currency} → ${currency}`, false, () => {
      openAsset(asset, { hint: `Ton relevé est en ${currency} : devise passée de ${asset.currency} à ${currency}. Enregistre pour la garder ; les opérations déjà saisies sur cet actif seront converties au taux BCE de leur jour.` });
      $("form-asset").currency.value = currency;
    })];
  });
  if (edits.length) {
    const label = document.createElement("span");
    label.className = "muted small";
    label.textContent = "Devise à corriger :";
    buttons.push(label, ...edits);
  }
  $("import-unknown").replaceChildren(...buttons);
  // Enchaînement de « Tout créer » : l'aperçu recalculé après chaque enregistrement ouvre le suivant.
  if (importState.chain) {
    if (openers.length) openers[0]();
    else importState.chain = false;
  }

  const c = data.counts;
  importState.ready = c.ok ?? 0;
  const parts = [`${importState.ready} opération${importState.ready > 1 ? "s" : ""} à importer`];
  for (const [status, label] of Object.entries(IMPORT_STATUS)) {
    if (status !== "ok" && c[status]) parts.push(`${c[status]} ${label.toLowerCase()}${status === "ignoree" && c[status] > 1 ? "s" : ""}`);
  }
  // Cryptos déjà revendues dans le relevé (D-085) : créées à l'import, rangées en « soldés ».
  const auto = data.auto_assets ?? [];
  if (auto.length) parts.push(`${auto.length} crypto${auto.length > 1 ? "s" : ""} déjà revendue${auto.length > 1 ? "s" : ""} créée${auto.length > 1 ? "s" : ""} automatiquement (${auto.join(", ")})`);
  $("import-summary").textContent = data.rows.length || c.ignoree ? parts.join(" · ") : "Aucune ligne de données.";
  updateImportButton();

  // Lignes ignorées (dépenses, virements...) : résumées par raison, jamais affichées une à une.
  const ignored = data.ignored ?? [];
  $("import-ignored-box").hidden = !ignored.length;
  $("import-ignored-title").textContent = `Pourquoi ${c.ignoree ?? 0} ligne${c.ignoree > 1 ? "s sont ignorées" : " est ignorée"} ?`;
  $("import-ignored").replaceChildren(...ignored.map((i) => Object.assign(document.createElement("li"), { textContent: `${i.reason} : ${i.count}` })));

  const rows = data.rows.map((r) => ({
    ...r,
    status_label: IMPORT_STATUS[r.status] ?? r.status,
    type_label: TYPES_FULL[r.op_type] ?? "",
    asset_label: r.asset_name ?? r.asset ?? "",
  }));
  fillSortable("import-table", rows, (r) => {
    const tr = document.createElement("tr");
    if (r.status !== "ok") tr.className = ["erreur", "actif_inconnu", "sans_compte"].includes(r.status) ? "bad" : "skip";
    const status = cell("", "status");
    const tone = r.status === "ok" ? "ok" : ["erreur", "actif_inconnu", "sans_compte"].includes(r.status) ? "ko" : "off";
    status.append(Object.assign(document.createElement("span"), { className: `pill ${tone}`, textContent: IMPORT_STATUS[r.status] ?? r.status }));
    if (r.message && ["erreur", "deja_saisie", "sans_compte"].includes(r.status)) {
      const msg = document.createElement("span");
      msg.className = "msg";
      msg.textContent = r.message;
      status.append(msg);
    }
    tr.append(
      cell(r.line, "num muted"),
      status,
      cell(r.op_date ? frDate(r.op_date) : ""),
      cell(r.type_label),
      clipCell(r.asset_label, "clip"),
      clipCell(outflowAccountLabel(r), "clip"),
      cell(formatDecimal(r.quantity), "num"),
      cell(formatDecimal(r.unit_price, r.unit_price && r.currency), "num"),
      cell(formatDecimal(r.amount, r.amount && r.currency), "num"),
      cell(r.fees && r.fees !== "0" ? formatDecimal(r.fees, r.currency) : "", "num"),
    );
    return tr;
  }, "Aucune ligne à afficher.");
}
bindSortable("import-table", ["line", "quantity", "unit_price", "amount", "fees"]);

// Colonne « Compte » de l'aperçu : pour un retrait, où va l'argent (réglé dans « Fonds sortis »).
function outflowAccountLabel(r) {
  const flow = r.outflow;
  if (!flow) return r.account ?? "";
  const names = flow.legs.map((l) => lists.accounts.find((a) => a.id === l.account_id)?.name ?? "?");
  if (Number(flow.rest) > 0) names.push(flow.text ?? "hors de l'app");
  return `→ ${names.join(", ")}`;
}

// « Fonds sortis » (D-087, D-088) : un bloc par transfert sortant ou retrait. On choisit un compte
// d'un clic (la part se règle au besoin), ou « Hors de l'app », puis on valide : rien n'est choisi
// d'avance et l'import reste bloqué tant qu'un retrait n'est pas validé. Parts gardées par ligne ;
// chaque changement relance l'aperçu, le serveur recalcule le reste et refuse un dépassement.
let lastImportData = null;
const OUTFLOW_STATUS = ["ok", "sans_compte", "actif_inconnu"];

const outflowRows = (data) => (data?.rows ?? []).filter((r) => r.outflow && OUTFLOW_STATUS.includes(r.status));
const pendingOutflows = () => outflowRows(lastImportData).filter((r) => !importState.validated.has(r.line)).length;

function renderOutflows(data) {
  lastImportData = data;
  const flows = outflowRows(data);
  $("import-outflows").hidden = !flows.length;
  $("import-outflows-list").replaceChildren(...flows.map(outflowBlock));
  renderOutflowBulk(flows);
  updateImportButton();
}

// Beaucoup de retraits (CSV quelconque, relevé d'un PEA...) : même choix pour tous d'un coup.
// Valide les retraits restants ; chacun reste modifiable.
function renderOutflowBulk(flows) {
  const todo = flows.filter((r) => !importState.validated.has(r.line));
  const box = $("import-outflows-bulk");
  box.hidden = todo.length < 2;
  if (box.hidden) return;
  const ownId = (r) => lists.accounts.find((a) => a.name === r.account)?.id;
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Même compte pour tous les retraits restants");
  select.append(new Option(`Même compte pour les ${todo.length} retraits`, ""),
    ...lists.accounts.map((a) => new Option(a.name, a.id)), new Option("Hors de l'app pour tous", "outside"));
  select.addEventListener("change", () => {
    if (!select.value) return;
    for (const r of todo) {
      if (select.value === "outside") importState.outside[r.line] = true;
      else if (Number(select.value) !== ownId(r)) importState.splits[r.line] = [{ account_id: Number(select.value), value: r.outflow.total }];
      else continue;
      importState.validated.add(r.line);
    }
    if (select.value === "outside") renderOutflows(lastImportData);
    else previewImport().catch((err) => showMessage(err.message, true));
  });
  box.replaceChildren(select);
}

function outflowBlock(r) {
  const flow = r.outflow;
  const cash = r.op_type === "retrait";
  const unit = cash ? "€" : r.asset_name ?? r.asset;
  const show = (value) => (cash ? formatDecimal(value, "EUR") : `${formatDecimal(value)} ${unit}`);
  const ownId = lists.accounts.find((a) => a.name === r.account)?.id;
  const legs = (importState.splits[r.line] ?? flow.legs.map((l) => ({ account_id: l.account_id, value: l.value })))
    .filter((l) => l.account_id !== ownId); // le serveur ignore une part vers le compte d'origine
  const others = lists.accounts.filter((a) => a.id !== ownId);
  const nameOf = (id) => lists.accounts.find((a) => a.id === id)?.name ?? "?";
  const outside = Boolean(importState.outside[r.line]);
  const done = importState.validated.has(r.line);
  const block = document.createElement("div");
  block.className = `outflow${done ? " done" : ""}`;

  const head = document.createElement("div");
  head.className = "outflow-head";
  head.append(Object.assign(document.createElement("strong"), { textContent: show(flow.total) }),
    Object.assign(document.createElement("span"), { className: "muted", textContent: frDate(r.op_date) }));
  block.append(head);

  const change = () => {
    importState.splits[r.line] = legs;
    importState.validated.delete(r.line);
    previewImport().catch((err) => showMessage(err.message, true));
  };

  if (done) {
    const names = legs.map((l) => nameOf(l.account_id));
    if (Number(flow.rest) > 0) names.push(flow.text ?? "hors de l'app");
    const sum = document.createElement("div");
    sum.className = "outflow-summary";
    sum.append(Object.assign(document.createElement("span"), { textContent: `✓ ${names.join(" · ")}` }),
      zoomChip("Modifier", false, () => { importState.validated.delete(r.line); renderOutflows(lastImportData); }));
    block.append(sum);
    return block;
  }

  legs.forEach((leg, i) => {
    const row = document.createElement("div");
    row.className = "outflow-leg";
    const input = document.createElement("input");
    input.inputMode = "decimal";
    input.setAttribute("aria-label", `${nameOf(leg.account_id)} : ${cash ? "montant" : "quantité"}`);
    input.value = leg.value ?? "";
    input.addEventListener("change", () => { leg.value = input.value.trim().replace(",", "."); change(); });
    const remove = zoomChip("✕", false, () => { legs.splice(i, 1); change(); });
    remove.setAttribute("aria-label", `Retirer ${nameOf(leg.account_id)}`);
    row.append(Object.assign(document.createElement("span"), { className: "outflow-name", textContent: `→ ${nameOf(leg.account_id)}` }), input, remove);
    block.append(row);
  });

  const rest = Number(flow.rest) > 0;
  if (flow.over) {
    block.append(Object.assign(document.createElement("p"), { className: "bad-text", textContent: "Trop attribué : corrige les parts." }));
  } else if (rest) {
    const choices = document.createElement("div");
    choices.className = "outflow-choices";
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Compte qui reçoit ${show(flow.rest)}`);
    select.append(new Option(legs.length ? `Reste ${show(flow.rest)} →` : "Choisir un compte", ""),
      ...others.map((a) => new Option(a.name, a.id)), ...(outside ? [] : [new Option("Hors de l'app", "outside")]));
    select.addEventListener("change", () => {
      if (select.value === "outside") { importState.outside[r.line] = true; renderOutflows(lastImportData); }
      else if (select.value) { legs.push({ account_id: Number(select.value), value: flow.rest }); change(); }
    });
    const create = Object.assign(document.createElement("button"), { type: "button", className: "field-btn", textContent: "+ Nouveau compte" });
    create.addEventListener("click", () => openAccount(null, { kind: "crypto" }));
    choices.append(select, create);
    block.append(choices);
    if (outside) {
      const text = document.createElement("input");
      text.maxLength = 80;
      text.placeholder = "Hors de l'app : où ? (facultatif)";
      text.setAttribute("aria-label", "Destination hors de l'app");
      text.value = importState.destinationTexts[r.line] ?? flow.text ?? "";
      text.addEventListener("change", () => {
        const value = text.value.trim();
        if (value) importState.destinationTexts[r.line] = value;
        else delete importState.destinationTexts[r.line];
        previewImport().catch((err) => showMessage(err.message, true));
      });
      const undo = zoomChip("✕", false, () => { importState.outside[r.line] = false; renderOutflows(lastImportData); });
      undo.setAttribute("aria-label", "Ne plus laisser hors de l'app");
      const row = document.createElement("div");
      row.className = "outflow-leg";
      row.append(text, undo);
      block.append(row);
    }
  }

  const ok = zoomChip("Valider", false, () => { importState.validated.add(r.line); renderOutflows(lastImportData); });
  ok.className = "primary";
  ok.disabled = flow.over || (rest && !outside) || (!legs.length && !outside);
  const actions = document.createElement("div");
  actions.className = "outflow-actions";
  actions.append(ok);
  block.append(actions);
  return block;
}

$("import-as-generic").addEventListener("click", (event) => {
  event.preventDefault();
  if (!importState.accountId) {
    showMessage("Choisis d'abord, dans la carte, le compte où importer ce fichier.", true);
    return;
  }
  importState.profile = "generique";
  importState.mapping = null;
  previewImport().catch((err) => showMessage(err.message, true));
});

$("import-header").addEventListener("change", () => {
  importState.mapping = null; // autres colonnes : nouvelle proposition
  previewImport().catch((err) => showMessage(err.message, true));
});

$("import-cancel").addEventListener("click", () => $("dlg-import").close());

$("import-go").addEventListener("click", async () => {
  const button = $("import-go");
  button.disabled = true;
  setProgress(null, "Enregistrement");
  try {
    const result = await api("/api/import", { method: "POST", body: importBody() });
    // Correspondance retenue pour ce modèle de fichier : noms de colonnes seulement, aucun montant.
    const memory = loadPref(IMPORT_MAP_KEY, {});
    memory[importState.columns.join("|")] = importState.mapping;
    savePref(IMPORT_MAP_KEY, memory);
    $("dlg-import").close();
    const plans = result.plans ? ` et ${result.plans} achat${result.plans > 1 ? "s" : ""} programmé${result.plans > 1 ? "s" : ""} créé${result.plans > 1 ? "s" : ""}` : "";
    const auto = result.auto_assets?.length ? ` ; ${result.auto_assets.length} actif${result.auto_assets.length > 1 ? "s" : ""} déjà revendu${result.auto_assets.length > 1 ? "s" : ""} créé${result.auto_assets.length > 1 ? "s" : ""} (dans Gérer > Actifs soldés)` : "";
    showMessage(`${result.imported} opération${result.imported > 1 ? "s" : ""} importée${result.imported > 1 ? "s" : ""}${plans}${auto}.`);
    await refresh();
    if (result.plans) await refreshRecurring();
  } catch (err) {
    showMessage(err.message, true);
    button.disabled = false;
  } finally {
    hideProgress();
  }
});

$("dlg-import").addEventListener("close", () => {
  importState.chain = false;
  importState.text = null; // le contenu du fichier est oublié
  $("import-rows").replaceChildren();
  $("import-paste").value = ""; // le texte collé aussi
});

// --- Achats programmés (D-055) -----------------------------------------------------------
// Chaque mois échu est proposé avec une quantité estimée (dernier cours en cache) ; l'utilisateur
// la corrige d'après son relevé puis valide, ou ignore. Rien n'est enregistré tout seul.

let recurring = { items: [], pending: [] };

async function refreshRecurring() {
  recurring = await api("/api/recurring");
  renderRecurring();
}

const WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const WEEKLY = ["hebdomadaire", "bimensuel"]; // jour de la semaine plutôt que du mois
const dayLabel = (day) => (day === 1 ? "1er" : String(day));

// « le 5 de chaque mois », « le 5, chaque trimestre », « chaque lundi »
function scheduleLabel(r) {
  if (r.frequency === "hebdomadaire") return `chaque ${WEEKDAYS[r.day - 1]}`;
  if (r.frequency === "bimensuel") return `un ${WEEKDAYS[r.day - 1]} sur deux`;
  if (r.frequency === "trimestriel") return `le ${dayLabel(r.day)}, chaque trimestre`;
  return `le ${dayLabel(r.day)} de chaque mois`;
}

function renderRecurring() {
  const count = recurring.pending.length;
  const badge = $("recurring-badge");
  badge.hidden = !count;
  badge.textContent = count;
  badge.title = `${count} achat${count > 1 ? "s" : ""} programmé${count > 1 ? "s" : ""} à valider`;

  const rows = recurring.items.map((r) => {
    const tr = document.createElement("tr");
    const name = cell(r.asset);
    const detail = document.createElement("span");
    detail.className = "muted small block";
    const last = r.last_due ? `dernière échéance traitée : ${frDate(r.last_due)}` : null;
    detail.textContent = [r.account, `depuis le ${frDate(r.start_date)}`, last].filter(Boolean).join(" · ");
    name.append(detail);
    tr.append(
      name,
      cell(`${formatDecimal(r.amount, r.currency)} ${scheduleLabel(r)}`, "num"),
      actionButtons(() => openRecurring(r), () => deleteRecurring(r)),
    );
    return tr;
  });
  $("recurring-list").replaceChildren(...(rows.length ? rows : [emptyRow("Aucun achat programmé.")]));

  // Échéances : seule la plus ancienne de chaque modèle se traite (les mois dans l'ordre).
  const first = new Set();
  $("recurring-pending").hidden = !count;
  $("recurring-pending-rows").replaceChildren(...recurring.pending.map((p) => {
    const oldest = !first.has(p.recurring_id);
    first.add(p.recurring_id);
    return pendingRow(p, oldest);
  }));
}

function pendingRow(p, oldest) {
  const tr = document.createElement("tr");
  const input = (name, value, attrs = {}) => {
    const el = document.createElement("input");
    Object.assign(el, { name, value: value ?? "", ...attrs });
    el.setAttribute("aria-label", name);
    const td = document.createElement("td");
    td.append(el);
    return [td, el];
  };
  const [dateTd, dateIn] = input("op_date", p.due_date, { type: "date", max: isoDate(new Date()) });
  const [qtyTd, qtyIn] = input("quantity", p.quantity?.replace(".", ","), { inputMode: "decimal", placeholder: "quantité" });
  const [priceTd, priceIn] = input("unit_price", p.price?.replace(".", ","), { inputMode: "decimal", placeholder: "prix" });
  const [feesTd, feesIn] = input("fees", "", { inputMode: "decimal", placeholder: "0" });
  const actions = document.createElement("td");
  actions.className = "actions";
  const ok = smallButton("Valider", "Enregistrer l'achat", () => confirmPending(p, { op_date: dateIn.value, quantity: qtyIn.value, unit_price: priceIn.value, fees: feesIn.value }));
  const skip = smallButton("Ignorer", "Pas d'achat cette fois-là", () => skipPending(p), "link");
  for (const b of [ok, skip]) {
    b.disabled = !oldest;
    if (!oldest) b.title = "Traite d'abord l'échéance précédente de cet achat";
  }
  actions.append(ok, skip);
  // Mois de l'échéance (mensuel, trimestriel) ; date exacte en hebdomadaire ou bimensuel.
  const [y, m] = p.due_date.split("-");
  const month = cell(WEEKLY.includes(p.frequency) ? frDate(p.due_date) : `${MONTHS[Number(m) - 1]} ${y}`);
  const asset = clipCell(p.asset, "clip");
  const detail = document.createElement("span");
  detail.className = "muted small block";
  detail.textContent = p.account;
  asset.append(detail);
  tr.append(month, asset, cell(formatDecimal(p.amount, p.currency), "num"), dateTd, qtyTd, priceTd, feesTd, actions);
  if (!oldest) tr.className = "later";
  return tr;
}

async function recurringAction(promise, text) {
  try {
    await promise;
    showMessage(text);
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

function confirmPending(p, f) {
  const body = {
    due_date: p.due_date,
    op_date: f.op_date,
    quantity: cleanDecimal(f.quantity),
    unit_price: cleanDecimal(f.unit_price),
    fees: cleanDecimal(f.fees) ?? "0",
  };
  if (!body.quantity || !body.unit_price) {
    showMessage("Indique la quantité et le prix unitaire de ton relevé.", true);
    return;
  }
  recurringAction(api(`/api/recurring/${p.recurring_id}/confirm`, { method: "POST", body: JSON.stringify(body) }), `Achat de ${p.asset} enregistré.`);
}

function skipPending(p) {
  recurringAction(api(`/api/recurring/${p.recurring_id}/skip`, { method: "POST", body: JSON.stringify({ due_date: p.due_date }) }), "Échéance ignorée.");
}

// Jour du mois ou jour de la semaine, selon la fréquence.
function syncRecurringDay() {
  const form = $("form-recurring");
  const weekly = WEEKLY.includes(form.frequency.value);
  for (const label of form.querySelectorAll("[data-day]")) {
    const on = (label.dataset.day === "week") === weekly;
    label.hidden = !on;
    const input = label.querySelector("input, select");
    input.disabled = !on; // un champ désactivé n'est ni vérifié ni envoyé
    input.required = on;
  }
}

function syncRecurringCurrency() {
  const form = $("form-recurring");
  const asset = lists.assets.find((a) => String(a.id) === form.asset_id.value);
  form.querySelector("[data-recurring-cur]").textContent = asset ? `, en ${asset.currency}` : "";
}

function openRecurring(r = null) {
  if (!lists.accounts.length || !lists.assets.length) {
    showMessage("Crée d'abord un compte et un actif.", true);
    return;
  }
  const form = $("form-recurring");
  fillSelect(form.account_id, lists.accounts, (a) => a.name);
  fillSelect(form.asset_id, lists.assets, assetLabel);
  form.dataset.id = r?.id ?? "";
  fillForm(form, r, { account_id: lists.accounts[0].id, asset_id: lists.assets[0].id, frequency: "mensuel", day: 5, start_date: isoDate(new Date()) });
  if (r) form.amount.value = r.amount.replace(".", ",");
  const weekly = WEEKLY.includes(r?.frequency);
  form.weekday.value = weekly ? r.day : 1;
  if (weekly) form.day.value = 5;
  syncRecurringDay();
  syncRecurringCurrency();
  $("dlg-recurring-title").textContent = r ? `Modifier l'achat programmé de ${r.asset}` : "Ajouter un achat programmé";
  $("dlg-recurring").showModal();
}

async function deleteRecurring(r) {
  if (!await askConfirm(`Supprimer l'achat programmé de « ${r.asset} » ?`, { detail: "Les achats déjà validés restent." })) return;
  recurringAction(api(`/api/recurring/${r.id}`, { method: "DELETE" }), "Achat programmé supprimé.");
}

$("form-recurring").asset_id.addEventListener("change", syncRecurringCurrency);
$("form-recurring").frequency.addEventListener("change", syncRecurringDay);
document.querySelector('[data-add="recurring"]').addEventListener("click", () => openRecurring());
$("form-recurring").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const f = Object.fromEntries(new FormData(form));
  const id = form.dataset.id;
  const body = {
    account_id: Number(f.account_id),
    asset_id: Number(f.asset_id),
    amount: cleanDecimal(f.amount),
    frequency: f.frequency,
    day: Number(WEEKLY.includes(f.frequency) ? f.weekday : f.day),
    start_date: f.start_date,
    note: orNull(f.note),
  };
  try {
    await api(id ? `/api/recurring/${id}` : "/api/recurring", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) });
    $("dlg-recurring").close();
    showMessage(id ? "Achat programmé modifié." : "Achat programmé créé.");
    await refreshRecurring();
  } catch (err) {
    showMessage(err.message, true);
  }
});

// --- Valeurs saisies (D-056) -------------------------------------------------------------
// Fonds euros, bien immobilier, SCPI non cotée... : la valeur totale de la ligne à une date.

let valuationAsset = null;

async function openValuation(asset) {
  valuationAsset = asset;
  const form = $("form-valuation");
  form.reset();
  form.value_date.value = isoDate(new Date());
  form.querySelector("[data-valuation-cur]").textContent = `, en ${asset.currency}`;
  $("dlg-valuation-title").textContent = `Valeur de « ${asset.name} »`;
  try {
    renderValuations(await api(`/api/assets/${asset.id}/valuations`));
    $("dlg-valuation").showModal();
  } catch (err) {
    showMessage(err.message, true);
  }
}

function renderValuations(rows) {
  $("valuation-rows").replaceChildren(...(rows.length ? rows.map((r) => {
    const tr = document.createElement("tr");
    const actions = document.createElement("td");
    actions.className = "actions";
    actions.append(smallButton("Supprimer", "Supprimer cette valeur", () => deleteValuation(r), "link"));
    tr.append(cell(frDate(r.date)), cell(formatDecimal(r.value, r.currency), "num"), actions);
    return tr;
  }) : [emptyRow("Aucune valeur saisie : la ligne vaut son prix d'achat.")]));
}

async function deleteValuation(r) {
  if (!await askConfirm(`Supprimer la valeur du ${frDate(r.date)} ?`)) return;
  try {
    await api(`/api/assets/${valuationAsset.id}/valuations/${r.date}`, { method: "DELETE" });
    renderValuations(await api(`/api/assets/${valuationAsset.id}/valuations`));
    showMessage("Valeur supprimée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
}

$("form-valuation").addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = Object.fromEntries(new FormData(event.target));
  try {
    const rows = await api(`/api/assets/${valuationAsset.id}/valuations`, {
      method: "POST",
      body: JSON.stringify({ value_date: f.value_date, value: cleanDecimal(f.value) }),
    });
    renderValuations(rows);
    event.target.value.value = "";
    showMessage("Valeur enregistrée.");
    await refresh();
  } catch (err) {
    showMessage(err.message, true);
  }
});

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
  $("ter-note").textContent = `Les frais courants sont prélevés chaque jour dans le prix du fonds : tu ne les vois jamais passer. Un fonds détenu sur deux comptes apparaît dans chacun. Manque à gagner = valeur dans ${t.years} ans sans frais − avec frais, si le fonds rapporte ${formatDecimal(t.growth_pct)} %/an avant frais, sans nouveau versement.${missing}`;
}

// --- Allocation cible (D-058) -----------------------------------------------------------

let targetTimer = null;
// Lignes ou catégories ajoutées au tableau sans cible enregistrée (pas encore détenues).
const targetExtras = { ligne: [], categorie: [] };


async function refreshTargets() {
  const level = targetPref.level;
  for (const b of $("target-level").querySelectorAll("button")) b.classList.toggle("active", b.dataset.level === level);
  $("target-help").textContent = TARGET_HELP[level];
  $("target-first").querySelector("button").textContent = level === "categorie" ? "Catégorie" : "Ligne";
  const contribution = cleanDecimal($("form-target").contribution.value) ?? "0";
  const data = await api(`/api/targets?level=${level}&contribution=${encodeURIComponent(contribution)}`);
  // Ajoutées depuis le menu mais pas encore enregistrées : une ligne vide, à 0 €.
  const addable = data.addable ?? [];
  const keyOf = (a) => (level === "categorie" ? a : String(a.asset_id));
  targetExtras[level] = targetExtras[level].filter((k) => addable.some((a) => keyOf(a) === k));
  const empty = { value: "0.00", weight_pct: null, target: null, current_pct: null, target_pct: null, gap: null, buy: null, lines: [], buy_asset: null };
  const extras = targetExtras[level].map((k) => {
    const a = addable.find((x) => keyOf(x) === k);
    return level === "categorie" ? { ...empty, category: a } : { ...empty, asset_id: a.asset_id, asset: a.asset };
  });
  fillTargetAdd(addable.filter((a) => !targetExtras[level].includes(keyOf(a))), level);
  const rows = [...data.rows, ...extras].map((r) => ({ ...r, label: level === "categorie" ? r.category : r.asset }));
  fillSortable("target-table", rows, (r) => {
    const input = document.createElement("input");
    const label = level === "categorie" ? r.category : r.asset;
    Object.assign(input, { inputMode: "decimal", placeholder: "–", value: r.target?.replace(".", ",") ?? "" });
    input.dataset.key = level === "categorie" ? r.category : r.asset_id;
    input.setAttribute("aria-label", `Cible de ${label}`);
    input.addEventListener("input", showTargetTotal);
    const td = document.createElement("td");
    td.className = "num";
    td.append(input);
    const gap = r.gap === null ? cell("–", "num muted") : cell(formatDecimal(signed(r.gap), "EUR"), signClass(r.gap));
    const buy = r.buy === null ? cell("–", "num muted") : cell(formatDecimal(r.buy, "EUR"), r.buy !== "0.00" ? "num strong" : "num");
    let first;
    if (level === "categorie") {
      first = cell(r.category);
      const detail = document.createElement("span");
      detail.className = "muted small block";
      const n = r.lines.length;
      // Trois noms au plus (cellule sur une ligne) ; la liste complète au survol.
      const shown = r.lines.slice(0, 3).join(", ") + (n > 3 ? `… (+${n - 3})` : "");
      detail.textContent = n ? `${n} ligne${n > 1 ? "s" : ""} : ${shown}` : "aucune ligne détenue";
      first.title = n ? r.lines.join(", ") : "";
      first.append(detail);
      if (r.buy_asset) {
        const on = document.createElement("span");
        on.className = "muted small block";
        on.textContent = `sur ${r.buy_asset}`;
        buy.classList.add("buy-on");
        buy.append(on);
      }
    } else {
      first = assetCell({ asset: r.asset });
    }
    return rowOf(
      first,
      cell(formatDecimal(r.value, "EUR"), "num"),
      cell(pctText(r.weight_pct), "num"),
      td,
      cell(pctText(r.current_pct), "num"),
      cell(pctText(r.target_pct), "num"),
      gap,
      buy,
    );
  }, level === "categorie" ? "Aucune catégorie détenue." : "Aucune ligne détenue.");
  showTargetTotal();
}

function fillTargetAdd(choices, level) {
  const select = $("target-add");
  $("target-add-label").hidden = !choices.length;
  $("target-add-text").textContent = level === "categorie" ? "Ajouter une catégorie pas encore détenue" : "Ajouter un actif pas encore acheté";
  select.replaceChildren(new Option("–", ""), ...choices.map((a) => (level === "categorie" ? new Option(a, a) : new Option(a.asset, a.asset_id))));
}

$("target-add").addEventListener("change", async (e) => {
  const key = e.target.value;
  if (!key) return;
  // Les cibles déjà tapées mais pas enregistrées survivent au nouveau tableau.
  const typed = new Map(targetInputs().map((i) => [i.dataset.key, i.value]));
  targetExtras[targetPref.level].push(key);
  try {
    await refreshTargets();
  } catch (err) {
    showMessage(err.message, true);
    return;
  }
  for (const input of targetInputs()) if (typed.has(input.dataset.key)) input.value = typed.get(input.dataset.key);
  targetInputs().find((i) => i.dataset.key === key)?.focus();
  showTargetTotal();
});

function targetInputs() {
  return [...document.querySelectorAll("#target-rows input")];
}

function showTargetTotal() {
  const total = targetInputs().reduce((sum, i) => sum + (Number(cleanDecimal(i.value) ?? 0) || 0), 0);
  const el = $("target-total");
  const what = targetPref.level === "categorie" ? "catégories" : "lignes";
  el.textContent = `Total des cibles : ${formatDecimal(String(Math.round(total * 100) / 100))} %${total > 100 ? " (plus de 100 %)" : total && total < 100 ? ` (les parts sont recalculées sur ce total, entre les ${what} visées)` : ""}`;
  el.className = total > 100 ? "small warn" : "muted small";
}

$("target-save").addEventListener("click", async () => {
  const targets = {};
  for (const input of targetInputs()) {
    const v = cleanDecimal(input.value);
    if (v) targets[input.dataset.key] = v;
  }
  const path = targetPref.level === "categorie" ? "/api/targets/categories" : "/api/targets";
  try {
    await api(path, { method: "PUT", body: JSON.stringify({ targets }) });
    showMessage("Cibles enregistrées.");
    await refreshTargets();
  } catch (err) {
    showMessage(err.message, true);
  }
});
for (const b of $("target-level").querySelectorAll("button")) {
  b.addEventListener("click", () => {
    targetPref.level = b.dataset.level;
    savePref(TARGET_KEY, targetPref);
    refreshTargets().catch((err) => showMessage(err.message, true));
  });
}
$("form-target").addEventListener("submit", (e) => e.preventDefault());
$("form-target").contribution.addEventListener("input", () => {
  clearTimeout(targetTimer);
  targetTimer = setTimeout(() => refreshTargets().catch((err) => showMessage(err.message, true)), 400);
});

// --- Repères fiscaux (D-059) -----------------------------------------------------------

$("form-per").tmi.value = taxPref.tmi;
if (!$("form-per").tmi.value) $("form-per").tmi.value = "30";
$("form-per").ceiling.value = taxPref.ceiling.replace(".", ",");

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

  const avs = data.assurance_vie;
  $("tax-av-kpis").replaceChildren(
    kpi("Versements", formatDecimal(sumOf(avs, "deposits"), "EUR"), `${avs.length} contrat${avs.length > 1 ? "s" : ""}`),
    kpi("Prochains 8 ans", nextMilestone(avs)),
  );
  fillSortable("tax-av-table", avs, (a) => rowOf(
    cell(a.account),
    openedCell(a),
    depositsCell(a),
    milestoneCell(a, AV_8),
  ), "Aucun compte de type assurance-vie.");

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
  });
  refreshTax().catch((err) => showMessage(err.message, true));
}
$("form-tax").pfu.addEventListener("change", saveTaxPref);
$("form-per").tmi.addEventListener("change", saveTaxPref);
$("form-per").ceiling.addEventListener("change", saveTaxPref);
$("form-per").addEventListener("submit", (e) => e.preventDefault());

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
