"use strict";

const main = document.getElementById("main");
const dialog = document.getElementById("dialog");
let me = null;

// ---------- petits outils ----------

// Crée un élément DOM ; le texte passe toujours par des nœuds texte (pas d'injection HTML possible).
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) {
    if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

const render = (...nodes) => main.replaceChildren(...nodes.flat(Infinity).filter((n) => n != null && n !== false));
const btn = (label, cls, onclick) => h("button", { type: "button", class: "btn " + cls, onclick }, label);
const plural = (n, words) => `${n} ${words}${n > 1 ? "s" : ""}`;
const initial = (name) => name.charAt(0).toUpperCase();
const empty = (title, hint) => h("div", { class: "card empty" }, h("p", { class: "empty-title" }, title), h("p", {}, hint));
const field = (label, input, hint) =>
  h("label", { class: "field" }, h("span", { class: "label" }, label), input, hint && h("small", {}, hint));
const codeInput = (autocomplete) => h("input", {
  name: "code", type: "password", inputmode: "numeric", pattern: "[0-9]{4,12}", required: true,
  autocomplete, title: "De 4 à 12 chiffres",
});

function euros(cents) {
  return (cents / 100).toLocaleString("fr-FR", {
    style: "currency", currency: "EUR", minimumFractionDigits: cents % 100 ? 2 : 0,
  });
}
const eurosInput = (cents) => (cents == null ? null : String(cents / 100).replace(".", ","));

function parseEuros(value) {
  const clean = value.replace(/[\s€]/g, "").replace(",", ".");
  if (!clean) return null;
  const n = Number(clean);
  if (!isFinite(n) || n < 0) throw new Error("Le prix doit être un nombre, par exemple 25 ou 19,90.");
  return Math.round(n * 100);
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body || {});
  }
  let res;
  try {
    res = await fetch("/api/" + path, options);
  } catch {
    throw new Error("Pas de connexion. Vérifiez que vous êtes bien connecté à internet.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && path !== "login") me = null;
    const error = new Error(data.error || "Une erreur est survenue. Réessayez.");
    error.status = res.status;
    throw error;
  }
  return data;
}

let toastTimer;
function toast(message, isError) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, 4000);
}

// Lance une action, affiche le résultat puis rafraîchit l'écran (même en cas d'erreur, pour montrer l'état réel).
async function act(fn, success) {
  try {
    await fn();
    if (success) toast(success);
  } catch (e) {
    toast(e.message, true);
  }
  route();
}

// Branche un formulaire : désactive le bouton pendant l'envoi et affiche l'erreur dans le formulaire.
function onSubmit(form, error, fn) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    error.textContent = "";
    try {
      await fn(form);
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  });
}

function formDialog(title, fields, submitLabel, send, success) {
  const error = h("p", { class: "form-error", role: "alert" });
  const form = h("form", { class: "sheet" },
    h("h2", {}, title),
    fields,
    error,
    h("div", { class: "actions" },
      btn("Annuler", "ghost", () => dialog.close()),
      h("button", { type: "submit", class: "btn primary" }, submitLabel)));
  onSubmit(form, error, async () => {
    await send(form);
    dialog.close();
    toast(success);
    route();
  });
  dialog.replaceChildren(form);
  dialog.showModal();
}

// ---------- navigation ----------

function showChrome(on) {
  document.getElementById("nav").hidden = !on;
  const badge = document.getElementById("badge");
  badge.hidden = !(on && me.unseen);
  badge.textContent = on && me.unseen > 99 ? "99+" : on ? me.unseen : "";
  badge.setAttribute("aria-label", `${badge.textContent} nouveaux`);
  document.getElementById("who").replaceChildren(...(on ? [
    h("button", { type: "button", class: "account", onclick: accountMenu, "aria-label": `Mon compte : ${me.name}` },
      h("span", { class: "avatar small" }, initial(me.name)), h("span", { class: "who-name" }, me.name)),
  ] : []));
}

// Petit menu derrière le prénom : garde la barre du bas à 4 onglets.
function accountMenu() {
  const close = () => dialog.close();
  dialog.replaceChildren(h("div", { class: "sheet" },
    h("h2", {}, me.name),
    h("p", { class: "muted" }, "Vous êtes connecté(e) avec ce prénom."),
    me.is_admin && h("a", { class: "btn secondary big", href: "#/reglages", onclick: close }, "⚙️ Réglages de la famille"),
    btn("Se déconnecter", "ghost big", () => {
      close();
      act(() => api("POST", "logout").then(() => { me = null; }));
    }),
    btn("Fermer", "primary big", close)));
  dialog.showModal();
}

async function route() {
  try {
    const start = await api("GET", "start");
    me = start.me;
    if (!me) {
      showChrome(false);
      return start.setup ? viewSetup() : viewLogin(start.names);
    }
    showChrome(true);
    const [, tab = "liste", id] = location.hash.split("/");
    document.querySelectorAll("#nav a").forEach((a) => {
      if (a.dataset.tab === tab) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (tab === "offrir") return await (id ? viewPerson(id) : viewFamilies());
    if (tab === "achats") return await viewPurchases();
    if (tab === "changements") return await viewChanges();
    if (tab === "reglages" && me.is_admin) return await viewAdmin();
    return await viewList();
  } catch (e) {
    if (e.status === 401) return route();
    render(h("div", { class: "card empty" }, h("p", { class: "empty-title" }, e.message), btn("Réessayer", "primary", route)));
  }
}

// ---------- accueil : première installation et connexion ----------

function viewSetup() {
  const error = h("p", { class: "form-error", role: "alert" });
  const form = h("form", { class: "card stack" },
    field("Votre prénom", h("input", { name: "name", required: true, maxlength: 50, autocomplete: "given-name" })),
    field("Nom de votre famille", h("input", { name: "family", required: true, maxlength: 50, placeholder: "Famille Martin" })),
    field("Choisissez un code secret", codeInput("new-password"), "De 4 à 12 chiffres. Il vous servira à vous connecter."),
    error,
    h("button", { type: "submit", class: "btn primary big" }, "C'est parti"));
  onSubmit(form, error, async (f) => {
    await api("POST", "setup", { name: f.elements.name.value, family: f.elements.family.value, code: f.elements.code.value });
    me = null;
    await route();
  });
  render(
    h("h1", {}, "Bienvenue"),
    h("p", { class: "lead" }, "Créons votre compte et votre famille. Vous ajouterez les autres personnes juste après."),
    form);
}

function viewLogin(names) {
  render(
    h("h1", {}, "Bonjour !"),
    h("p", { class: "lead" }, "Touchez votre prénom."),
    h("div", { class: "names" }, names.map((n) =>
      h("button", { type: "button", class: "name-btn", onclick: () => viewCode(n) },
        h("span", { class: "avatar" }, initial(n.name)), n.name))));
}

function viewCode(person) {
  const error = h("p", { class: "form-error", role: "alert" });
  const input = codeInput("current-password");
  const form = h("form", { class: "card stack" },
    field("Votre code secret", input),
    error,
    h("button", { type: "submit", class: "btn primary big" }, "Entrer"));
  onSubmit(form, error, async () => {
    await api("POST", "login", { id: person.id, code: input.value });
    me = null;
    await route();
  });
  render(
    h("button", { type: "button", class: "back", onclick: route }, "← Ce n'est pas moi"),
    h("h1", {}, `Bonjour ${person.name}`),
    form);
  input.focus();
}

// ---------- ma liste ----------

function giftInfo(g) {
  return h("div", { class: "gift-info" },
    h("h3", {}, g.title),
    g.price != null && h("p", { class: "price" }, euros(g.price)),
    g.note && h("p", { class: "note" }, g.note),
    g.link && h("a", { class: "link", href: g.link, target: "_blank", rel: "noopener noreferrer" }, "Voir sur internet ↗"));
}

async function viewList() {
  const gifts = await api("GET", "my-gifts");
  render(
    h("h1", {}, "Ma liste"),
    h("p", { class: "lead" }, "Notez ce qui vous ferait plaisir. Votre famille choisira dedans, et la surprise reste entière : vous ne verrez jamais qui offre quoi."),
    btn("＋ Ajouter un cadeau", "primary big", () => giftForm()),
    gifts.length
      ? h("ul", { class: "list" }, gifts.map((g) => h("li", { class: "card" },
          giftInfo(g),
          h("div", { class: "actions" },
            btn("Modifier", "ghost", () => giftForm(g)),
            btn("Supprimer", "danger", () => {
              if (confirm(`Supprimer « ${g.title} » de votre liste ?`)) act(() => api("DELETE", `gifts/${g.id}`), "Cadeau supprimé.");
            })))))
      : empty("Votre liste est vide pour l'instant.", "Touchez « Ajouter un cadeau » pour commencer."));
}

function giftForm(g = {}) {
  formDialog(g.id ? "Modifier le cadeau" : "Ajouter un cadeau", [
    field("Quel cadeau ?", h("input", { name: "title", required: true, maxlength: 120, value: g.title, placeholder: "Ex. : un livre de cuisine" })),
    field("Prix environ, en euros", h("input", { name: "price", inputmode: "decimal", value: eurosInput(g.price), placeholder: "Ex. : 25" }), "Facultatif"),
    field("Lien vers un site", h("input", { name: "link", type: "url", maxlength: 1000, value: g.link, placeholder: "https://…" }), "Facultatif"),
    field("Précisions", h("textarea", { name: "note", maxlength: 500, rows: 3, placeholder: "Taille, couleur, magasin…" }, g.note), "Facultatif"),
  ], "Enregistrer", (f) => {
    const data = {
      title: f.elements.title.value, price: parseEuros(f.elements.price.value),
      link: f.elements.link.value, note: f.elements.note.value,
    };
    return g.id ? api("PUT", `gifts/${g.id}`, data) : api("POST", "gifts", data);
  }, g.id ? "Cadeau modifié." : "Cadeau ajouté à votre liste.");
}

// ---------- offrir ----------

function personSummary(p) {
  if (!p.gifts) return "Liste vide pour l'instant";
  return `${plural(p.gifts, "idée")} · ${p.free ? plural(p.free, "encore libre") : "tout est déjà pris"}`;
}

async function viewFamilies() {
  const families = await api("GET", "families");
  render(
    h("h1", {}, "Offrir"),
    h("p", { class: "lead" }, "Choisissez une personne pour voir sa liste."),
    families.length
      ? families.map((f) => h("section", { class: "group" },
          h("h2", {}, f.name),
          f.budget != null && h("p", { class: "tag" }, `Budget : ${euros(f.budget)} par personne`),
          f.people.length
            ? h("div", { class: "people" }, f.people.map((p) =>
                h("a", { class: "card person", href: `#/offrir/${p.id}` },
                  h("span", { class: "avatar" }, initial(p.name)),
                  h("span", { class: "person-text" }, h("strong", {}, p.name), h("small", {}, personSummary(p))),
                  h("span", { class: "chev", "aria-hidden": "true" }, "›"))))
            : h("p", { class: "muted" }, "Personne d'autre dans cette famille pour l'instant.")))
      : empty("Vous ne faites partie d'aucune famille.", "Demandez à la personne qui gère l'application de vous ajouter."));
}

async function viewPerson(id) {
  const p = await api("GET", `people/${id}`);
  render(
    h("a", { class: "back", href: "#/offrir" }, "← Retour"),
    h("h1", {}, p.name),
    p.budget != null && h("p", { class: "tag" }, `Budget : ${euros(p.budget)}`),
    p.gifts.length
      ? h("ul", { class: "list" }, p.gifts.map(giverCard))
      : empty(`${p.name} n'a encore rien ajouté.`, "Revenez un peu plus tard !"));
}

// Part de chacun dans un cadeau : le montant choisi, sinon une part égale de ce qui reste à payer.
function shares(g) {
  const fixed = g.givers.reduce((sum, x) => sum + (x.amount || 0), 0);
  const free = g.givers.filter((x) => x.amount == null).length;
  const each = free && g.price != null ? Math.max(0, g.price - fixed) / free : 0;
  const byId = new Map(g.givers.map((x) => [x.id, x.amount != null ? x.amount : Math.round(each)]));
  const collected = [...byId.values()].reduce((a, b) => a + b, 0);
  return { byId, collected, missing: g.price != null ? Math.max(0, g.price - collected) : 0 };
}
const isPot = (g) => g.givers.length > 1 || g.givers.some((x) => x.amount != null);

function potBox(g) {
  const { byId, collected, missing } = shares(g);
  const names = g.givers.map((x) => `${x.id === me.id ? "Vous" : x.name} : ${euros(byId.get(x.id))}`).join(" · ");
  return h("div", { class: "pot" },
    h("p", { class: "pot-title" }, "Cagnotte", g.price != null && ` · ${euros(collected)} sur ${euros(g.price)}`),
    g.price != null && h("progress", { value: Math.min(collected, g.price), max: g.price || 1, "aria-label": "Montant réuni" }),
    h("p", { class: "pot-names" }, names),
    g.price != null && h("p", { class: missing ? "pot-missing" : "pot-done" }, missing ? `Il manque ${euros(missing)}` : "Le montant est atteint"));
}

function amountForm(g, title, success) {
  const mine = g.givers.find((x) => x.id === me.id);
  formDialog(title, [
    h("p", { class: "muted" }, `« ${g.title} »`, g.price != null && ` · ${euros(g.price)}`),
    field("Combien voulez-vous mettre ? (en euros)",
      h("input", { name: "amount", inputmode: "decimal", value: eurosInput(mine && mine.amount), placeholder: "Ex. : 20" }),
      "Laissez vide pour partager le reste à parts égales avec les autres participants."),
  ], "Valider", (f) => api("POST", `gifts/${g.id}/give`, { together: true, amount: parseEuros(f.elements.amount.value) }), success);
}

function giverCard(g) {
  const mine = g.givers.some((x) => x.id === me.id);
  const others = g.givers.filter((x) => x.id !== me.id).map((x) => x.name).join(", ");
  const pot = isPot(g);
  const missing = shares(g).missing;
  let status;
  let actions;
  if (mine) {
    status = h("span", { class: "status mine" }, pot ? "Vous participez" : "Vous l'offrez");
    actions = [
      pot && btn("Changer ma participation", "secondary", () => amountForm(g, "Ma participation", "Participation modifiée.")),
      btn(pot ? "Je ne participe plus" : "Je ne l'offre plus", "ghost", () => {
        if (confirm("Vous ne voulez plus participer à ce cadeau ?")) act(() => api("DELETE", `gifts/${g.id}/give`), "C'est noté.");
      }),
    ];
  } else if (others) {
    status = h("span", { class: missing ? "status waiting" : "status taken" }, missing ? "Cagnotte en cours" : `Déjà pris par ${others}`);
    actions = [btn("Participer à la cagnotte", missing ? "primary" : "secondary", () =>
      amountForm(g, "Participer à la cagnotte", "Merci ! Votre participation est enregistrée."))];
  } else {
    status = h("span", { class: "status free" }, "Libre");
    actions = [
      btn("Je l'offre", "primary", () =>
        act(() => api("POST", `gifts/${g.id}/give`, { together: false }), "Super ! Ce cadeau est réservé pour vous.")),
      btn("Lancer une cagnotte", "ghost", () =>
        amountForm(g, "Lancer une cagnotte", "Cagnotte lancée ! Les autres peuvent maintenant participer.")),
    ];
  }
  return h("li", { class: "card" },
    giftInfo(g),
    h("div", { class: "statuses" }, status, g.bought ? h("span", { class: "status bought" }, "Déjà acheté") : null),
    pot && potBox(g),
    h("div", { class: "actions" }, actions));
}

// ---------- mes achats ----------

async function viewPurchases() {
  const people = await api("GET", "my-purchases");
  const share = (g) => shares(g).byId.get(me.id);
  const spent = (p) => p.gifts.reduce((sum, g) => sum + share(g), 0);
  const total = people.reduce((sum, p) => sum + spent(p), 0);
  render(
    h("h1", {}, "Mes achats"),
    people.length ? [
      h("div", { class: "card total" }, h("span", {}, "Total de vos cadeaux"), h("strong", {}, euros(total))),
      people.map((p) => {
        const over = p.budget != null && spent(p) > p.budget;
        return h("section", { class: "group" },
          h("h2", {}, `Pour ${p.name}`),
          h("p", { class: over ? "tag over" : "tag" }, p.budget != null
            ? `${euros(spent(p))} sur ${euros(p.budget)} prévus${over ? " — budget dépassé" : ""}`
            : `${euros(spent(p))} au total`),
          h("ul", { class: "list" }, p.gifts.map((g) => h("li", { class: "card" },
            giftInfo(g),
            isPot(g) && potBox(g),
            isPot(g) && h("div", { class: "actions" },
              btn("Changer ma participation", "secondary", () => amountForm(g, "Ma participation", "Participation modifiée."))),
            h("label", { class: "check" },
              h("input", {
                type: "checkbox", checked: !!g.bought,
                onchange: (e) => act(() => api("PUT", `gifts/${g.id}/bought`, { bought: e.target.checked }),
                  e.target.checked ? "Noté comme acheté ✓" : "Noté comme pas encore acheté."),
              }),
              "Déjà acheté")))));
      }),
    ] : empty("Vous n'avez encore choisi aucun cadeau.", "Allez dans « Offrir » pour choisir un cadeau dans la liste de quelqu'un."));
}

// ---------- changements ----------

// [verbe à la 3e personne, verbe avec « Vous »] puis la suite de la phrase.
const PHRASES = {
  add: ["a ajouté", "avez ajouté", (e, ta) => ` à ${ta} liste`],
  edit: ["a modifié", "avez modifié", () => ""],
  delete: ["a retiré", "avez retiré", (e, ta) => ` de ${ta} liste`],
  give: ["offre", "offrez", (e) => ` à ${e.owner}`],
  join: ["participe à la cagnotte", "participez à la cagnotte", (e) => ` pour ${e.owner}`],
  amount: ["a changé sa participation à", "avez changé votre participation à", (e) => ` pour ${e.owner}`],
  ungive: ["n'offre plus", "n'offrez plus", (e) => ` à ${e.owner}`],
  bought: ["a acheté", "avez acheté", (e) => ` pour ${e.owner}`],
};

function dayLabel(date) {
  const today = new Date();
  const yesterday = new Date(today.getTime() - 864e5);
  if (date.toDateString() === today.toDateString()) return "Aujourd'hui";
  if (date.toDateString() === yesterday.toDateString()) return "Hier";
  return date.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

async function viewChanges() {
  const { seen, events } = await api("GET", "changes");
  document.getElementById("badge").hidden = true;
  const days = new Map();
  for (const e of events) {
    const date = new Date(e.at * 1000);
    const label = dayLabel(date);
    if (!days.has(label)) days.set(label, []);
    const [verb, verbYou, tail] = PHRASES[e.action] || ["a changé", "avez changé", () => ""];
    const isMe = e.actor_id === me.id;
    const isNew = e.id > seen && !isMe;
    days.get(label).push(h("li", { class: isNew ? "card change new" : "card change" },
      h("span", { class: "avatar small" }, initial(e.actor)),
      h("div", { class: "change-text" },
        h("p", {}, h("strong", {}, isMe ? "Vous" : e.actor), ` ${isMe ? verbYou : verb} « ${e.title} »`, tail(e, isMe ? "votre" : "sa")),
        e.detail && h("p", { class: "change-detail" }, e.detail),
        h("p", { class: "change-time" },
          isNew && h("span", { class: "new-tag" }, "Nouveau"),
          date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })))));
  }
  render(
    h("h1", {}, "Changements"),
    h("p", { class: "lead" }, "Tout ce qui a bougé dans les listes de la famille. Ce qui concerne vos propres cadeaux reste secret."),
    events.length
      ? [...days].map(([label, items]) => h("section", { class: "group day" }, h("h2", {}, label), h("ul", { class: "list" }, items)))
      : empty("Rien de nouveau pour l'instant.", "Les ajouts, modifications et réservations apparaîtront ici."));
}

// ---------- réglages (administrateur) ----------

async function viewAdmin() {
  const { families, users } = await api("GET", "admin");
  const familyName = (id) => (families.find((f) => f.id === id) || {}).name;
  const count = (fid) => users.filter((u) => u.family_ids.includes(fid)).length;
  render(
    h("h1", {}, "Réglages"),
    h("section", { class: "group" },
      h("div", { class: "group-head" }, h("h2", {}, "Familles"), btn("＋ Nouvelle famille", "secondary", () => familyForm())),
      h("div", { class: "people" }, families.map((f) => h("div", { class: "card row" },
        h("div", {}, h("strong", {}, f.name), h("small", {},
          (f.budget != null ? `Budget : ${euros(f.budget)} par personne` : "Pas de budget") + " · " + plural(count(f.id), "membre"))),
        h("div", { class: "actions" },
          btn("Modifier", "ghost", () => familyForm(f)),
          btn("Supprimer", "danger", () => {
            if (confirm(`Supprimer la famille « ${f.name} » ? Les personnes et leurs listes ne seront pas effacées.`)) act(() => api("DELETE", `admin/families/${f.id}`), "Famille supprimée.");
          })))))),
    h("section", { class: "group" },
      h("div", { class: "group-head" }, h("h2", {}, "Personnes"), btn("＋ Nouvelle personne", "secondary", () => userForm(families))),
      h("div", { class: "people" }, users.map((u) => h("div", { class: "card row" },
        h("div", {}, h("strong", {}, u.name, u.is_admin ? " · admin" : ""),
          h("small", {}, u.family_ids.map(familyName).join(", ") || "Aucune famille")),
        h("div", { class: "actions" },
          btn("Modifier", "ghost", () => userForm(families, u)),
          u.id !== me.id && btn("Supprimer", "danger", () => {
            if (confirm(`Supprimer ${u.name} ? Sa liste et ses réservations seront définitivement effacées.`)) act(() => api("DELETE", `admin/users/${u.id}`), `${u.name} a été supprimé(e).`);
          })))))));
}

function familyForm(f = {}) {
  formDialog(f.id ? "Modifier la famille" : "Nouvelle famille", [
    field("Nom de la famille", h("input", { name: "name", required: true, maxlength: 50, value: f.name })),
    field("Budget par personne, en euros", h("input", { name: "budget", inputmode: "decimal", value: eurosInput(f.budget), placeholder: "Ex. : 30" }), "Facultatif. Montant conseillé pour chaque personne qui reçoit."),
  ], "Enregistrer", (form) => {
    const data = { name: form.elements.name.value, budget: parseEuros(form.elements.budget.value) };
    return f.id ? api("PUT", `admin/families/${f.id}`, data) : api("POST", "admin/families", data);
  }, "Famille enregistrée.");
}

function userForm(families, u = {}) {
  const ownFamilies = u.family_ids || (families.length === 1 ? [families[0].id] : []);
  formDialog(u.id ? `Modifier ${u.name}` : "Nouvelle personne", [
    field("Prénom", h("input", { name: "name", required: true, maxlength: 50, value: u.name })),
    field(u.id ? "Nouveau code secret" : "Code secret",
      h("input", { name: "code", inputmode: "numeric", pattern: "[0-9]{4,12}", required: !u.id, autocomplete: "off", title: "De 4 à 12 chiffres" }),
      u.id ? "Laissez vide pour garder le code actuel." : "De 4 à 12 chiffres. Donnez-le à la personne pour qu'elle se connecte."),
    h("fieldset", { class: "field" },
      h("legend", { class: "label" }, "Familles"),
      families.length
        ? families.map((f) => h("label", { class: "check" },
            h("input", { type: "checkbox", name: "family", value: f.id, checked: ownFamilies.includes(f.id) }), f.name))
        : h("small", {}, "Créez d'abord une famille.")),
    h("label", { class: "check" },
      h("input", { type: "checkbox", name: "is_admin", checked: !!u.is_admin, disabled: u.id === me.id }),
      "Peut gérer les réglages (administrateur)"),
  ], "Enregistrer", (form) => {
    const data = {
      name: form.elements.name.value,
      code: form.elements.code.value,
      is_admin: form.elements.is_admin.checked,
      family_ids: [...form.querySelectorAll("input[name=family]:checked")].map((i) => Number(i.value)),
    };
    return u.id ? api("PUT", `admin/users/${u.id}`, data) : api("POST", "admin/users", data);
  }, "Personne enregistrée.");
}

// ---------- démarrage ----------

window.addEventListener("hashchange", () => {
  if (dialog.open) dialog.close();
  route();
});
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
route();
