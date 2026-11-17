// --- Allocation cible (D-058) -----------------------------------------------------------

let targetTimer = null;
// Lignes ou catégories ajoutées au tableau sans cible enregistrée (pas encore détenues).
const targetExtras = { ligne: [], categorie: [] };


async function refreshTargets() {
  const level = targetPref.level;
  for (const b of $("target-level").querySelectorAll("button")) b.classList.toggle("active", b.dataset.level === level);
  $("target-help").replaceChildren(TARGET_HELP[level][0], " ", makeTip(TARGET_HELP[level][1]));
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
