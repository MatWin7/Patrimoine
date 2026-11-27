// Messages d'erreur en français (D-150). Chargé avant profiles.js : l'écran de choix du profil,
// app.js et les fenêtres s'en servent tous. Aucun accès réseau ici.
// Un seul endroit traduit ce que le navigateur, FastAPI
// et Pydantic renvoient en anglais, et dit quoi faire. `showMessage` y passe toute erreur, en
// filet de sécurité ; `api()` s'en sert pour les erreurs de validation (un message par champ).
const SERVER_DOWN = "Le serveur ne répond pas. Relance l'app, puis recharge la page.";
const ERROR_PATTERNS = [
  [/failed to fetch|load failed|networkerror|network request failed|fetch failed/i, SERVER_DOWN],
  [/unexpected token|unexpected end of|json\.parse|is not valid json|not valid json/i, "Réponse inattendue du serveur. Recharge la page ; si ça continue, copie le diagnostic (Réglages, Aide)."],
  [/^internal server error$/i, "Erreur inattendue. Pour la signaler : Réglages, « Copier le diagnostic »."],
  [/^not found$/i, "Élément introuvable. Recharge la page."],
  [/^method not allowed$/i, "Action refusée par le serveur. Recharge la page."],
  [/^(forbidden|unauthorized)$/i, "Accès refusé. Recharge la page ou rouvre le profil."],
  [/timed? ?out|timeout/i, "Le service met trop de temps à répondre. Réessaie dans un moment."],
  [/^field required$/i, "à renseigner"],
  [/input should be a valid (number|decimal)|unable to parse string as a (number|decimal)/i, "doit être un nombre"],
  [/input should be a valid integer/i, "doit être un nombre entier"],
  [/input should be a valid date|invalid date/i, "doit être une date valide"],
  [/input should be a valid (string|boolean)/i, "valeur invalide"],
  [/input should be greater than or equal to (\S+)/i, "doit être d'au moins $1"],
  [/input should be greater than (\S+)/i, "doit être supérieur à $1"],
  [/input should be less than or equal to (\S+)/i, "doit être d'au plus $1"],
  [/input should be less than (\S+)/i, "doit être inférieur à $1"],
  [/string should have at least (\d+) character/i, "doit avoir au moins $1 caractères"],
  [/string should have at most (\d+) character/i, "doit avoir au plus $1 caractères"],
  [/input should be (.+)/i, "valeur non autorisée"],
  [/decimal input should have no more than/i, "a trop de chiffres"],
];

// Message en français ; un texte déjà en français passe tel quel.
function frenchError(text) {
  const raw = String(text ?? "").trim().replace(/^value error, /i, "");
  for (const [pattern, replacement] of ERROR_PATTERNS) {
    const found = raw.match(pattern);
    if (found) return replacement.replace("$1", found[1] ?? "");
  }
  return raw || "Erreur inattendue. Recharge la page.";
}

// Libellé d'un champ de formulaire d'après son nom technique (« price_ttl_minutes ») : le texte
// de son étiquette dans la page, sinon le nom lui-même.
function fieldLabel(name) {
  const input = document.querySelector(`[name="${CSS.escape(String(name))}"]`);
  const label = input?.closest("label");
  if (!label) return name;
  const clone = label.cloneNode(true);
  clone.querySelectorAll("input, select, textarea, button, small, .muted").forEach((el) => el.remove());
  return clone.textContent.replace(/\s+/g, " ").trim() || name;
}
