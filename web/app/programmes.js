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
