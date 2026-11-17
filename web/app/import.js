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
