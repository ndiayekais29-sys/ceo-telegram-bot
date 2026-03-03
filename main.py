"""
CEO-AI — SYSTÈME MULTI-AGENTS TELEGRAM
9 agents spécialisés + CEO orchestrateur
"""

import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY")
MEMORY_FILE    = Path("ceo_memory.json")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger("CEO_BOT")

CEO_PROMPT = """Tu es CEO-AI, l'orchestrateur principal d'un système multi-agents autonome.
LANGUE : Détecte la langue de l'utilisateur et réponds toujours dans la même langue.
Tu coordonnes 9 agents spécialisés. Quand tu réponds directement, tu parles comme un vrai CEO : direct, confiant, sans blabla.
Tu proposes toujours une action suivante logique."""

AGENTS = {
    "scout": {
        "emoji": "🔍", "name": "SCOUT",
        "prompt": """Tu es l'agent SCOUT, expert en exploration de marché.
LANGUE : Réponds dans la même langue que l'utilisateur.
Génère et score des niches micro-SaaS. Pour chaque niche : problème urgent, score urgence/10, monétisation/10, facilité/10, score composite, taille marché, concurrents. Génère toujours 20 niches minimum triées par score. Sois précis et data-driven."""
    },
    "oracle": {
        "emoji": "🎯", "name": "ORACLE",
        "prompt": """Tu es l'agent ORACLE, expert en stratégie.
LANGUE : Réponds dans la même langue que l'utilisateur.
Sélectionne la meilleure niche avec justification 4-5 phrases, risques, ICP précis, positionnement différenciant, score de confiance /100."""
    },
    "forge": {
        "emoji": "⚡", "name": "FORGE",
        "prompt": """Tu es l'agent FORGE, expert en conception produit micro-SaaS.
LANGUE : Réponds dans la même langue que l'utilisateur.
Livre : nom + tagline, proposition de valeur, 5 features MVP, 3 features V2, pricing 3 tiers, stack technique, temps MVP, MRR cible 3/6/12 mois."""
    },
    "pulse": {
        "emoji": "📣", "name": "PULSE",
        "prompt": """Tu es l'agent PULSE, expert en marketing.
LANGUE : Réponds dans la même langue que l'utilisateur.
Génère : 10 posts LinkedIn/Twitter prêts à publier, 5 emails cold outreach, 3 scripts vidéo complets, 5 hooks publicitaires, plan acquisition 3 phases."""
    },
    "lens": {
        "emoji": "📊", "name": "LENS",
        "prompt": """Tu es l'agent LENS, expert en analyse de performance.
LANGUE : Réponds dans la même langue que l'utilisateur.
Analyse les métriques, compare aux benchmarks, décide SCALE/OPTIMIZE/PIVOT, donne 3 actions prioritaires avec impact estimé."""
    },
    "design": {
        "emoji": "🎨", "name": "DESIGN",
        "prompt": """Tu es l'agent DESIGN, expert en landing pages.
LANGUE : Réponds dans la même langue que l'utilisateur (code HTML en anglais).
Génère du HTML/CSS complet : hero, problème/solution, features, témoignages, pricing 3 tiers, FAQ, footer. Design sombre #0a0a0a, accent #00ff88. Code 100% fonctionnel copier-coller prêt."""
    },
    "code": {
        "emoji": "💻", "name": "CODE",
        "prompt": """Tu es l'agent CODE, expert en développement.
LANGUE : Réponds dans la même langue que l'utilisateur (code en anglais).
Génère du code Python/JS/HTML propre et commenté. Toujours : explication 2-3 lignes, dépendances, code complet, instructions d'utilisation."""
    },
    "spy": {
        "emoji": "🔎", "name": "SPY",
        "prompt": """Tu es l'agent SPY, expert en analyse concurrentielle.
LANGUE : Réponds dans la même langue que l'utilisateur.
Analyse : top 5 concurrents avec prix/features/forces/faiblesses, gaps marché, angle différenciation, score faisabilité /10, stratégie 90 jours."""
    },
    "social": {
        "emoji": "📱", "name": "SOCIAL",
        "prompt": """Tu es l'agent SOCIAL, expert réseaux sociaux.
LANGUE : Réponds dans la même langue que l'utilisateur.
Crée : calendrier éditorial 30 jours, posts par plateforme (LinkedIn/Twitter/Instagram/TikTok), hashtags optimaux, meilleurs horaires, 3 idées virales."""
    }
}

ROUTER_PROMPT = """Analyse le message et retourne UNIQUEMENT le nom de l'agent approprié parmi :
scout, oracle, forge, pulse, lens, design, code, spy, social, ceo

Règles :
- marché, niche, opportunité, explorer → scout
- choisir, sélectionner, stratégie → oracle
- produit, SaaS, features, pricing, MVP → forge
- marketing, posts, emails, scripts, publicité → pulse
- métriques, analytics, KPI, scale, pivot → lens
- landing page, site, HTML → design
- code, script, automatisation, Python, API → code
- concurrents, analyse concurrentielle → spy
- réseaux sociaux, Instagram, LinkedIn, TikTok → social
- tout le reste → ceo

Retourne UNIQUEMENT le mot clé, rien d'autre."""

def load_memory(user_id):
    if MEMORY_FILE.exists():
        data = json.loads(MEMORY_FILE.read_text())
        return data.get(str(user_id), {"history": [], "cycles": 0, "niche": None, "last_agent": None})
    return {"history": [], "cycles": 0, "niche": None, "last_agent": None}

def save_memory(user_id, memory):
    data = {}
    if MEMORY_FILE.exists():
        data = json.loads(MEMORY_FILE.read_text())
    data[str(user_id)] = memory
    MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

async def route_message(message):
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            system=ROUTER_PROMPT,
            messages=[{"role": "user", "content": message}]
        )
        agent = response.content[0].text.strip().lower()
        return agent if agent in AGENTS or agent == "ceo" else "ceo"
    except:
        return "ceo"

async def call_agent(agent_key, history):
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        if agent_key in AGENTS:
            agent = AGENTS[agent_key]
            system = agent["prompt"]
            prefix = f"{agent['emoji']} Agent {agent['name']} activé\n{'─'*30}\n\n"
        else:
            system = CEO_PROMPT
            prefix = "🤖 CEO-AI\n{'─'*30}\n\n"

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            system=system,
            messages=history[-20:],
        )
        return prefix + response.content[0].text
    except Exception as e:
        return f"❌ Erreur : {e}"

async def process_message(user_id, user_message, force_agent=None):
    memory = load_memory(user_id)
    memory["history"].append({"role": "user", "content": user_message})
    agent_key = force_agent or await route_message(user_message)
    memory["last_agent"] = agent_key
    if agent_key == "scout":
        memory["cycles"] += 1
    reply = await call_agent(agent_key, memory["history"])
    memory["history"].append({"role": "assistant", "content": reply})
    save_memory(user_id, memory)
    return reply

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Cycle complet", callback_data="cycle"),
         InlineKeyboardButton("🔍 Scout — Marché", callback_data="agent_scout")],
        [InlineKeyboardButton("⚡ Forge — Produit", callback_data="agent_forge"),
         InlineKeyboardButton("📣 Pulse — Marketing", callback_data="agent_pulse")],
        [InlineKeyboardButton("📊 Lens — Métriques", callback_data="agent_lens"),
         InlineKeyboardButton("🔎 Spy — Concurrents", callback_data="agent_spy")],
        [InlineKeyboardButton("🎨 Design — Landing", callback_data="agent_design"),
         InlineKeyboardButton("💻 Code — Scripts", callback_data="agent_code")],
        [InlineKeyboardButton("📱 Social — Réseaux", callback_data="agent_social"),
         InlineKeyboardButton("🧠 Mémoire", callback_data="memory")],
    ])

QUICK_MESSAGES = {
    "cycle":        "Lance le cycle complet : explore le marché, sélectionne la meilleure niche, conçois le produit et génère le marketing.",
    "agent_scout":  "Explore le marché. Génère 20 niches scorées.",
    "agent_forge":  "Conçois le produit micro-SaaS complet pour la niche sélectionnée.",
    "agent_pulse":  "Génère le plan marketing complet : posts, emails, scripts, hooks.",
    "agent_lens":   "Métriques : 800 visiteurs, 12 signups, 3 clients à $29/mo. Analyse et décide.",
    "agent_design": "Crée une landing page HTML complète pour mon micro-SaaS.",
    "agent_code":   "Génère un script Python pour automatiser les relances email B2B.",
    "agent_spy":    "Analyse les 5 principaux concurrents du marché des outils B2B.",
    "agent_social": "Crée un calendrier éditorial 30 jours et 10 posts prêts à publier.",
}

FORCE_AGENTS = {k: k.replace("agent_", "") for k in QUICK_MESSAGES if k.startswith("agent_")}

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🤖 *CEO-AI Multi-Agents — Bonjour {user.first_name} !*\n\n"
        "9 agents spécialisés à ton service :\n\n"
        "🔍 *SCOUT* — Marchés et niches\n"
        "🎯 *ORACLE* — Stratégie et sélection\n"
        "⚡ *FORGE* — Conception produit\n"
        "📣 *PULSE* — Marketing complet\n"
        "📊 *LENS* — Analyse et décisions\n"
        "🎨 *DESIGN* — Landing pages HTML\n"
        "💻 *CODE* — Scripts et automatisations\n"
        "🔎 *SPY* — Analyse concurrentielle\n"
        "📱 *SOCIAL* — Réseaux sociaux\n\n"
        "Écris-moi directement ou choisis un agent 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    save_memory(update.effective_user.id, {"history": [], "cycles": 0, "niche": None, "last_agent": None})
    await update.message.reply_text("🔄 Mémoire effacée.", reply_markup=main_keyboard())

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = load_memory(update.effective_user.id)
    await update.message.reply_text(
        f"🧠 *Mémoire :*\n\n• Cycles : {mem['cycles']}\n• Dernier agent : {mem.get('last_agent','Aucun')}\n• Messages : {len(mem['history'])}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard()
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = await process_message(update.effective_user.id, update.message.text)
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        await update.message.reply_text(chunk, reply_markup=main_keyboard() if i == len(chunks)-1 else None)

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    if action == "memory":
        mem = load_memory(query.from_user.id)
        await query.message.reply_text(
            f"🧠 Cycles: {mem['cycles']} | Agent: {mem.get('last_agent','Aucun')} | Messages: {len(mem['history'])}",
            reply_markup=main_keyboard()
        )
        return
    msg = QUICK_MESSAGES.get(action, "Lance une analyse complète.")
    force = FORCE_AGENTS.get(action)
    await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    reply = await process_message(query.from_user.id, msg, force_agent=force)
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        await query.message.reply_text(chunk, reply_markup=main_keyboard() if i == len(chunks)-1 else None)

def main():
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN manquant !")
        return
    if not ANTHROPIC_KEY:
        log.error("ANTHROPIC_API_KEY manquant !")
        return
    log.info("🚀 CEO-AI Multi-Agents démarré...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("✅ 9 agents actifs !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
