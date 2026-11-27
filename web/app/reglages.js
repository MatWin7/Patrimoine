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
  event.target.setAttribute("aria-busy", "true");
  try {
    renderUpdates(await api("/api/updates/check", { method: "POST" }));
  } catch (err) {
    showMessage(err.message, true);
  } finally {
    event.target.disabled = false;
    event.target.removeAttribute("aria-busy");
  }
});

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
// Avant le premier affichage : la page Fiscalité les lit dès l'ouverture sur #fiscalite.
const TAX_KEY = PREF + "fiscalite";
const taxPref = { tmi: "30", ceiling: "", ...loadPref(TAX_KEY, {}) };
