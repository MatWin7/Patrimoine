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
