"""
╔══════════════════════════════════════════════════════╗
║         CEO-AI — BOT TELEGRAM                       ║
║         Powered by Claude (Anthropic)               ║
╚══════════════════════════════════════════════════════╝
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

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY")
MEMORY_FILE      = Path("ceo_memory.json")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("CEO_BOT")

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

FORMAT : Utilise du texte bien structuré avec emojis. Pas de Markdown complexe.
Tu es le CEO. Tu décides. Tu agis."""

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

async def call_ceo(user_id: int, user_message: str) -> str:
    memory = load_memory(user_id)
    memory["history"].append({"role": "user", "content": user_message})
    history = memory["history"][-20:]

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
        memory["history"].append({"role": "assistant", "content": reply})
        if any(w in user_message.lower() for w in ["cycle", "explore", "marché", "niche"]):
            memory["cycles"] += 1
        if "niche" in reply.lower():
            memory["niche"] = "Analysée"
        save_memory(user_id, memory)
        return reply
    except Exception as e:
        log.error(f"Erreur API: {e}")
        return f"❌ Erreur : {e}"

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🤖 *CEO-AI activé* — Bonjour {user.first_name} !\n\n"
        "Je suis ton agent CEO digital autonome.\n\n"
        "Choisis une action ou écris-moi directement 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    save_memory(update.effective_user.id, {"history": [], "cycles": 0, "niche": None, "mrr": 0})
    await update.message.reply_text("🔄 Mémoire effacée.", reply_markup=main_keyboard())

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = load_memory(update.effective_user.id)
    text = (
        f"🧠 *Mémoire CEO-AI :*\n\n"
        f"• Cycles : {mem['cycles']}\n"
        f"• Niche : {mem['niche'] or 'Aucune'}\n"
        f"• Messages : {len(mem['history'])}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = await call_ceo(update.effective_user.id, update.message.text)
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        markup = main_keyboard() if i == len(chunks) - 1 else None
        await update.message.reply_text(chunk, reply_markup=markup)

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "memory":
        mem = load_memory(query.from_user.id)
        await query.message.reply_text(
            f"🧠 Cycles: {mem['cycles']} | Niche: {mem['niche'] or 'Aucune'}",
            reply_markup=main_keyboard()
        )
        return

    if action == "help":
        await query.message.reply_text(
            "Commandes : /start /reset /memory\nOu écris directement !",
            reply_markup=main_keyboard()
        )
        return

    await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    reply = await call_ceo(query.from_user.id, QUICK_MESSAGES.get(action, "Lance une analyse."))
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        markup = main_keyboard() if i == len(chunks) - 1 else None
        await query.message.reply_text(chunk, reply_markup=markup)

def main():
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN manquant !")
        return
    if not ANTHROPIC_KEY:
        log.error("ANTHROPIC_API_KEY manquant !")
        return

    log.info("🚀 CEO-AI Bot démarré...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("✅ Bot actif !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
