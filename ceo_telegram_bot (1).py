"""
╔══════════════════════════════════════════════════════╗
║         CEO-AI — BOT TELEGRAM                       ║
║         Powered by Claude (Anthropic)               ║
╚══════════════════════════════════════════════════════╝

INSTALLATION :
    pip install python-telegram-bot anthropic python-dotenv

FICHIER .env à créer :
    TELEGRAM_TOKEN=ton_token_botfather
    ANTHROPIC_API_KEY=sk-ant-...

LANCEMENT :
    python ceo_telegram_bot.py
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY")
MEMORY_FILE      = Path("ceo_memory.json")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("CEO_BOT")

# ─── PROMPT CEO ───────────────────────────────────────
CEO_SYSTEM_PROMPT = """Tu es CEO-AI, un agent stratégique autonome expert en création de micro-SaaS rentables.

Tu as une personnalité forte : direct, analytique, ambitieux. Tu parles comme un vrai CEO — pas comme un assistant.

Tes responsabilités :

1. EXPLORER LE MARCHÉ
- Générer 20 niches avec problème urgent
- Scorer chaque niche : urgence /10, monétisation /10, facilité /10, score composite
- Présenter sous forme de liste structurée

2. CHOISIR UNE NICHE
- Sélectionner la niche avec le score le plus élevé
- Justifier en 3-4 phrases comme un vrai CEO

3. CONCEVOIR LE PRODUIT MICRO-SAAS
- Nom, tagline, proposition de valeur
- 5 features MVP + 3 features V2
- Pricing (3 tiers avec prix et limites)
- Stack technique recommandé
- Temps estimé pour MVP

4. GÉNÉRER LE MARKETING
- 10 posts LinkedIn/Twitter prêts à publier
- 5 emails de cold outreach
- 3 scripts vidéo (hook + contenu + CTA)
- 5 hooks publicitaires
- Plan d'acquisition en 3 phases

5. ANALYSER ET DÉCIDER
Quand on te donne des métriques, tu analyses et décides : SCALE / OPTIMIZE / PIVOT
Tu justifies ta décision avec des chiffres.

6. BOUCLE AUTONOME
Tu te souviens des cycles précédents et tu les appliques.

RÈGLES DE COMMUNICATION :
- Tu commences toujours par une action concrète
- Tu utilises des émojis stratégiquement
- Tu structures avec des sections claires
- Tu donnes des chiffres précis
- Quand on dit "go" ou "lance", tu exécutes directement
- Tu parles à la première personne : "J'ai analysé", "Je choisis", "Mon verdict"
- Ton ton : confiant, précis, sans blabla

FORMAT : Utilise du texte bien structuré avec emojis. Pas de Markdown complexe (Telegram a ses limites).
Tu es le CEO. Tu décides. Tu agis."""

# ─── MÉMOIRE ──────────────────────────────────────────
def load_memory(user_id: int) -> dict:
    if MEMORY_FILE.exists():
        data = json.loads(MEMORY_FILE.read_text())
        return data.get(str(user_id), {"history": [], "cycles": 0, "niche": None, "mrr": 0})
    return {"history": [], "cycles": 0, "niche": None, "mrr": 0}

def save_memory(user_id: int, memory: dict):
    data = {}
    if MEMORY_FILE.exists():
        data = json.loads(MEMORY_FILE.read_text())
    data[str(user_id)] = memory
    MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ─── CLAVIER RAPIDE ───────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Cycle complet", callback_data="cycle"),
            InlineKeyboardButton("🔍 Explorer marché", callback_data="explore"),
        ],
        [
            InlineKeyboardButton("⚡ Concevoir produit", callback_data="product"),
            InlineKeyboardButton("📣 Générer marketing", callback_data="marketing"),
        ],
        [
            InlineKeyboardButton("📊 Analyser métriques", callback_data="analyze"),
            InlineKeyboardButton("🔄 Nouveau cycle", callback_data="newcycle"),
        ],
        [
            InlineKeyboardButton("🧠 Voir mémoire", callback_data="memory"),
            InlineKeyboardButton("❓ Aide", callback_data="help"),
        ],
    ])

QUICK_MESSAGES = {
    "cycle":     "Lance le cycle complet : explore le marché, choisis la meilleure niche, conçois le produit et génère le marketing.",
    "explore":   "Explore le marché maintenant. Génère 20 niches scorées et sélectionne la meilleure.",
    "product":   "Conçois le produit micro-SaaS complet pour la niche sélectionnée.",
    "marketing": "Génère le plan marketing complet : 10 posts, 5 emails, 3 scripts vidéo, 5 hooks et plan d'acquisition.",
    "analyze":   "Voici mes métriques : 800 visiteurs, 12 signups, 3 clients payants à $29/mo. Analyse et dis-moi quoi faire.",
    "newcycle":  "Lance un nouveau cycle en appliquant les leçons du cycle précédent. Explore une nouvelle niche.",
}

# ─── APPEL IA ─────────────────────────────────────────
async def call_ceo(user_id: int, user_message: str) -> str:
    memory = load_memory(user_id)

    # Ajouter message utilisateur
    memory["history"].append({"role": "user", "content": user_message})

    # Limiter l'historique aux 20 derniers messages
    history = memory["history"][-20:]

    # Contexte mémoire
    mem_context = ""
    if memory["niche"]:
        mem_context = f"\n\n[MÉMOIRE CEO] Cycles: {memory['cycles']} | Dernière niche: {memory['niche']} | MRR simulé: ${memory['mrr']}"

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            system=CEO_SYSTEM_PROMPT + mem_context,
            messages=history,
        )
        reply = response.content[0].text

        # Mise à jour mémoire
        memory["history"].append({"role": "assistant", "content": reply})
        if any(w in user_message.lower() for w in ["cycle", "explore", "marché", "niche"]):
            memory["cycles"] += 1
        if "niche" in reply.lower():
            memory["niche"] = "Analysée"
        if "$" in reply and "mrr" in reply.lower():
            memory["mrr"] += 500

        save_memory(user_id, memory)
        return reply

    except Exception as e:
        log.error(f"Erreur API: {e}")
        return f"❌ Erreur de connexion à l'IA : {e}\n\nVérifie ta clé ANTHROPIC_API_KEY dans le fichier .env"

# ─── HANDLERS ─────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🤖 *CEO-AI activé* — Bonjour {user.first_name} !\n\n"
        "Je suis ton agent CEO digital autonome. Je peux :\n\n"
        "🔍 Explorer 20 niches et scorer chaque opportunité\n"
        "⚡ Concevoir ton micro-SaaS complet\n"
        "📣 Générer tout le contenu marketing\n"
        "📊 Analyser tes métriques et décider SCALE/PIVOT\n"
        "🔄 Tourner en boucle autonome et mémoriser\n\n"
        "Choisis une action ou écris-moi directement 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *Commandes disponibles :*\n\n"
        "/start — Démarrer le CEO-AI\n"
        "/reset — Effacer la mémoire\n"
        "/memory — Voir la mémoire actuelle\n"
        "/menu — Afficher le menu rapide\n\n"
        "Ou écris directement ce que tu veux !"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_memory(user_id, {"history": [], "cycles": 0, "niche": None, "mrr": 0})
    await update.message.reply_text(
        "🔄 Mémoire effacée. CEO-AI repart de zéro.",
        reply_markup=main_keyboard()
    )

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mem = load_memory(user_id)
    text = (
        f"🧠 *Mémoire CEO-AI :*\n\n"
        f"• Cycles complétés : {mem['cycles']}\n"
        f"• Dernière niche : {mem['niche'] or 'Aucune'}\n"
        f"• MRR simulé : ${mem['mrr']}\n"
        f"• Messages en mémoire : {len(mem['history'])}\n"
        f"• Dernière activité : {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 *Que veux-tu faire ?*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Indicateur de frappe
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # Diviser les longues réponses (Telegram limite à 4096 chars)
    reply = await call_ceo(user_id, text)

    if len(reply) <= 4000:
        await update.message.reply_text(reply, reply_markup=main_keyboard())
    else:
        # Découper en morceaux
        chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
        for i, chunk in enumerate(chunks):
            markup = main_keyboard() if i == len(chunks) - 1 else None
            await update.message.reply_text(chunk, reply_markup=markup)

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action = query.data

    if action == "help":
        text = (
            "📋 *Commandes :*\n\n"
            "/start — Démarrer\n/reset — Effacer mémoire\n"
            "/memory — Voir mémoire\n/menu — Menu rapide\n\n"
            "Ou écris directement !"
        )
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    if action == "memory":
        mem = load_memory(user_id)
        text = (
            f"🧠 *Mémoire CEO-AI :*\n\n"
            f"• Cycles : {mem['cycles']}\n"
            f"• Niche : {mem['niche'] or 'Aucune'}\n"
            f"• MRR simulé : ${mem['mrr']}\n"
            f"• Messages : {len(mem['history'])}"
        )
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())
        return

    # Actions IA
    msg = QUICK_MESSAGES.get(action, "Lance une analyse du marché.")
    await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)

    reply = await call_ceo(user_id, msg)

    if len(reply) <= 4000:
        await query.message.reply_text(reply, reply_markup=main_keyboard())
    else:
        chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
        for i, chunk in enumerate(chunks):
            markup = main_keyboard() if i == len(chunks) - 1 else None
            await query.message.reply_text(chunk, reply_markup=markup)

# ─── MAIN ─────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN manquant dans .env !")
        return
    if not ANTHROPIC_KEY:
        log.error("ANTHROPIC_API_KEY manquant dans .env !")
        return

    log.info("🚀 CEO-AI Bot Telegram démarré...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("reset",   reset_cmd))
    app.add_handler(CommandHandler("memory",  memory_cmd))
    app.add_handler(CommandHandler("menu",    menu_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("✅ Bot actif — En attente de messages...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
