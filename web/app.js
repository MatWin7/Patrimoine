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
  if (errors.length) parts.push(`${errors.length} cours non mis à jour`);
  if (portfolio.fx_error) parts.push(portfolio.fx_error);
  const negative = portfolio.cash.filter((c) => c.cash.startsWith("-")).map((c) => c.account);
  if (negative.length) parts.push(`Espèces négatives (${negative.join(", ")}) : un versement manque sans doute`);
  const el = $("prices-status");
  const shown = portfolio.positions.length || negative.length;
  el.textContent = shown ? parts.join(" · ") : "";
  if (shown) {
    const detail = [`Les cours se mettent à jour seuls, toutes les ${portfolio.price_ttl_minutes} min au plus.`];
    if (errors.length) detail.push("Cours non mis à jour : dans Positions et opérations, survole le cours de la ligne pour voir pourquoi.");
    el.append(" ", makeTip(detail.join(" ")));
  }
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
function askDialog({ title, detail = "", input = false, value = "", placeholder = "", ok = "Confirmer", danger = false, choices = null }) {
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
    // Choix facultatif sous le champ (ex. type d'une liste) : le résultat devient { text, choice }.
    const pick = $("ask-choice");
    pick.hidden = !choices;
    if (choices) {
      $("ask-choice-label").textContent = choices.label;
      $("ask-choice-select").replaceChildren(...choices.options.map(([v, t]) => new Option(t, v)));
    }
    const button = $("ask-ok");
    button.textContent = ok;
    button.classList.toggle("danger", danger);
    button.classList.toggle("primary", !danger);
    let result = null;
    form.onsubmit = (e) => {
      e.preventDefault();
      result = input ? field.value : true;
      if (input && choices) result = { text: field.value, choice: $("ask-choice-select").value };
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

// --- Préférences de vue gardées dans le navigateur ---------------------------------

// Dans ce fichier : les autres lisent leurs préférences dès leur chargement.
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
