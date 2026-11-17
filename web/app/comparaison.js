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
