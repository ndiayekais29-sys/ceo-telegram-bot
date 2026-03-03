"""
CEO-AI — MULTI-AGENTS + SUPABASE + ALERTES MATINALES
"""

import os
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime
import httpx
from dotenv import load_dotenv

import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger("CEO_BOT")

# ─────────────────────────────────────────────────────
#  SUPABASE — MÉMOIRE LONGUE
# ─────────────────────────────────────────────────────

async def db_load(user_id: int) -> dict:
    """Charge la mémoire depuis Supabase."""
    if not SUPABASE_URL:
        return _default_memory()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/memory?user_id=eq.{user_id}",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            )
            data = r.json()
            if data:
                row = data[0]
                return {
                    "history":    row.get("history", []),
                    "cycles":     row.get("cycles", 0),
                    "niche":      row.get("niche"),
                    "last_agent": row.get("last_agent"),
                    "mrr":        row.get("mrr", 0),
                }
    except Exception as e:
        log.error(f"DB load error: {e}")
    return _default_memory()

async def db_save(user_id: int, memory: dict):
    """Sauvegarde la mémoire dans Supabase."""
    if not SUPABASE_URL:
        return
    try:
        payload = {
            "user_id":    user_id,
            "history":    memory.get("history", [])[-50:],  # Max 50 messages
            "cycles":     memory.get("cycles", 0),
            "niche":      memory.get("niche"),
            "last_agent": memory.get("last_agent"),
            "mrr":        memory.get("mrr", 0),
            "updated_at": datetime.now().isoformat(),
        }
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/memory",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                json=payload
            )
    except Exception as e:
        log.error(f"DB save error: {e}")

async def db_get_all_users() -> list:
    """Récupère tous les user_id pour les alertes matinales."""
    if not SUPABASE_URL:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/memory?select=user_id",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            )
            return [row["user_id"] for row in r.json()]
    except:
        return []

def _default_memory():
    return {"history": [], "cycles": 0, "niche": None, "last_agent": None, "mrr": 0}

# ─────────────────────────────────────────────────────
#  PROMPTS AGENTS
# ─────────────────────────────────────────────────────

CEO_PROMPT = """Tu es CEO-AI, l'orchestrateur principal d'un système multi-agents autonome.
LANGUE : Détecte la langue de l'utilisateur et réponds toujours dans la même langue.
Tu coordonnes 9 agents spécialisés. Tu parles comme un vrai CEO : direct, confiant, sans blabla.
Propose toujours une action suivante logique."""

MORNING_REPORT_PROMPT = """Tu es CEO-AI. Génère un rapport matinal motivant et actionnable.

LANGUE : Réponds en français.

Format du rapport :
🌅 Bonjour ! Voici ton brief CEO du jour.

📊 SITUATION ACTUELLE
[résume la situation basée sur la mémoire : cycles, niche, MRR]

🎯 PRIORITÉ DU JOUR
[1 action concrète et précise à faire aujourd'hui]

⚡ 3 TÂCHES RAPIDES
[3 micro-tâches de moins de 30 min chacune]

💡 INSIGHT DU JOUR
[1 conseil stratégique court et percutant]

🚀 OBJECTIF DE LA SEMAINE
[1 objectif mesurable pour la semaine]

Sois motivant, précis et actionnable. Max 200 mots."""

AGENTS = {
    "scout": {
        "emoji": "🔍", "name": "SCOUT",
        "prompt": """Tu es l'agent SCOUT, expert en exploration de marché.
LANGUE : Réponds dans la même langue que l'utilisateur.
Génère et score des niches micro-SaaS. Pour chaque niche : problème urgent, score urgence/10, monétisation/10, facilité/10, score composite, taille marché, concurrents. Génère toujours 20 niches minimum triées par score."""
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
LANGUE : Réponds dans la même langue (code HTML en anglais).
Génère du HTML/CSS complet : hero, problème/solution, features, témoignages, pricing 3 tiers, FAQ, footer. Design sombre #0a0a0a, accent #00ff88. Code 100% fonctionnel."""
    },
    "code": {
        "emoji": "💻", "name": "CODE",
        "prompt": """Tu es l'agent CODE, expert en développement.
LANGUE : Réponds dans la même langue (code en anglais).
Génère du code Python/JS/HTML propre et commenté. Toujours : explication, dépendances, code complet, instructions."""
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
Crée : calendrier éditorial 30 jours, posts par plateforme (LinkedIn/Twitter/Instagram/TikTok), hashtags, meilleurs horaires, 3 idées virales."""
    }
}

ROUTER_PROMPT = """Analyse le message et retourne UNIQUEMENT le nom de l'agent :
scout, oracle, forge, pulse, lens, design, code, spy, social, ceo

- marché, niche, opportunité, explorer → scout
- choisir, sélectionner, stratégie → oracle
- produit, SaaS, features, pricing, MVP → forge
- marketing, posts, emails, scripts → pulse
- métriques, analytics, KPI, scale, pivot → lens
- landing page, site, HTML → design
- code, script, automatisation, Python → code
- concurrents, analyse → spy
- réseaux sociaux, Instagram, LinkedIn, TikTok → social
- tout le reste → ceo

Retourne UNIQUEMENT le mot clé."""

# ─────────────────────────────────────────────────────
#  APPELS IA
# ─────────────────────────────────────────────────────

async def route_message(message: str) -> str:
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

async def call_agent(agent_key: str, history: list) -> str:
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        if agent_key in AGENTS:
            agent = AGENTS[agent_key]
            system = agent["prompt"]
            prefix = f"{agent['emoji']} Agent {agent['name']} activé\n{'─'*30}\n\n"
        else:
            system = CEO_PROMPT
            prefix = "🤖 CEO-AI\n"
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            system=system,
            messages=history[-20:],
        )
        return prefix + response.content[0].text
    except Exception as e:
        return f"❌ Erreur : {e}"

async def generate_morning_report(memory: dict) -> str:
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        context = f"Mémoire CEO : cycles={memory['cycles']}, niche={memory.get('niche','aucune')}, MRR=${memory.get('mrr',0)}, dernier agent={memory.get('last_agent','aucun')}"
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=600,
            system=MORNING_REPORT_PROMPT,
            messages=[{"role": "user", "content": context}]
        )
        return response.content[0].text
    except Exception as e:
        return f"❌ Erreur rapport matinal : {e}"

async def process_message(user_id: int, user_message: str, force_agent: str = None) -> str:
    memory = await db_load(user_id)
    memory["history"].append({"role": "user", "content": user_message})
    agent_key = force_agent or await route_message(user_message)
    memory["last_agent"] = agent_key
    if agent_key == "scout":
        memory["cycles"] += 1
    reply = await call_agent(agent_key, memory["history"])
    memory["history"].append({"role": "assistant", "content": reply})
    await db_save(user_id, memory)
    return reply

# ─────────────────────────────────────────────────────
#  ALERTES MATINALES
# ─────────────────────────────────────────────────────

async def send_morning_reports(bot: Bot):
    """Envoie un rapport matinal à tous les utilisateurs."""
    log.info("📨 Envoi des rapports matinaux...")
    user_ids = await db_get_all_users()
    for user_id in user_ids:
        try:
            memory = await db_load(user_id)
            report = await generate_morning_report(memory)
            await bot.send_message(
                chat_id=user_id,
                text=f"🌅 *BRIEF CEO DU JOUR*\n\n{report}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_keyboard()
            )
            log.info(f"✅ Rapport envoyé à {user_id}")
        except Exception as e:
            log.error(f"Erreur rapport pour {user_id}: {e}")

async def morning_scheduler(bot: Bot):
    """Tourne en arrière-plan et envoie les rapports à 8h00 UTC."""
    while True:
        now = datetime.utcnow()
        # Envoie à 8h00 UTC (= 9h Paris)
        if now.hour == 8 and now.minute == 0:
            await send_morning_reports(bot)
            await asyncio.sleep(61)  # Évite le double envoi
        await asyncio.sleep(30)

# ─────────────────────────────────────────────────────
#  CLAVIER
# ─────────────────────────────────────────────────────

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
         InlineKeyboardButton("📋 Rapport matinal", callback_data="report")],
        [InlineKeyboardButton("🧠 Mémoire", callback_data="memory"),
         InlineKeyboardButton("🔄 Reset", callback_data="reset")],
    ])

QUICK_MESSAGES = {
    "cycle":        "Lance le cycle complet : explore le marché, sélectionne la meilleure niche, conçois le produit et génère le marketing.",
    "agent_scout":  "Explore le marché. Génère 20 niches scorées.",
    "agent_forge":  "Conçois le produit micro-SaaS complet.",
    "agent_pulse":  "Génère le plan marketing complet : posts, emails, scripts, hooks.",
    "agent_lens":   "Métriques : 800 visiteurs, 12 signups, 3 clients à $29/mo. Analyse et décide.",
    "agent_design": "Crée une landing page HTML complète pour mon micro-SaaS.",
    "agent_code":   "Génère un script Python pour automatiser les relances email B2B.",
    "agent_spy":    "Analyse les 5 principaux concurrents du marché des outils B2B.",
    "agent_social": "Crée un calendrier éditorial 30 jours et 10 posts prêts à publier.",
}

FORCE_AGENTS = {k: k.replace("agent_", "") for k in QUICK_MESSAGES if k.startswith("agent_")}

# ─────────────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🤖 *CEO-AI Multi-Agents v2 — Bonjour {user.first_name} !*\n\n"
        "9 agents spécialisés + mémoire permanente + rapport matinal 🌅\n\n"
        "🔍 *SCOUT* — Marchés et niches\n"
        "🎯 *ORACLE* — Stratégie\n"
        "⚡ *FORGE* — Produit\n"
        "📣 *PULSE* — Marketing\n"
        "📊 *LENS* — Métriques\n"
        "🎨 *DESIGN* — Landing pages\n"
        "💻 *CODE* — Scripts\n"
        "🔎 *SPY* — Concurrents\n"
        "📱 *SOCIAL* — Réseaux sociaux\n\n"
        "📋 Rapport automatique chaque matin à 9h !\n\n"
        "Écris-moi directement ou choisis un agent 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = await db_load(update.effective_user.id)
    await update.message.reply_text(
        f"🧠 *Mémoire permanente :*\n\n"
        f"• Cycles complétés : {mem['cycles']}\n"
        f"• Niche active : {mem.get('niche') or 'Aucune'}\n"
        f"• MRR simulé : ${mem.get('mrr', 0)}\n"
        f"• Dernier agent : {mem.get('last_agent') or 'Aucun'}\n"
        f"• Messages mémorisés : {len(mem['history'])}",
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
    user_id = query.from_user.id

    if action == "memory":
        mem = await db_load(user_id)
        await query.message.reply_text(
            f"🧠 Cycles: {mem['cycles']} | Agent: {mem.get('last_agent','Aucun')} | Messages: {len(mem['history'])}",
            reply_markup=main_keyboard()
        )
        return

    if action == "reset":
        await db_save(user_id, _default_memory())
        await query.message.reply_text("🔄 Mémoire effacée.", reply_markup=main_keyboard())
        return

    if action == "report":
        await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
        mem = await db_load(user_id)
        report = await generate_morning_report(mem)
        await query.message.reply_text(
            f"📋 *TON BRIEF CEO*\n\n{report}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard()
        )
        return

    msg = QUICK_MESSAGES.get(action, "Lance une analyse complète.")
    force = FORCE_AGENTS.get(action)
    await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    reply = await process_message(user_id, msg, force_agent=force)
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        await query.message.reply_text(chunk, reply_markup=main_keyboard() if i == len(chunks)-1 else None)

async def post_init(app: Application):
    """Lance le scheduler en arrière-plan après démarrage."""
    asyncio.create_task(morning_scheduler(app.bot))
    log.info("⏰ Scheduler matinal activé (8h00 UTC)")

# ─────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN manquant !")
        return
    if not ANTHROPIC_KEY:
        log.error("ANTHROPIC_API_KEY manquant !")
        return
    if not SUPABASE_URL:
        log.warning("SUPABASE_URL manquant — mémoire locale uniquement")

    log.info("🚀 CEO-AI v2 Multi-Agents + Supabase démarré...")
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("✅ 9 agents + mémoire permanente + alertes matinales actifs !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
