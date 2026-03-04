"""
CEO-AI v4 — GEMINI EDITION
13 agents + Chaîne autonome + Supabase + Alertes matinales
Utilise google-generativeai (Gemini 1.5 Flash) — GRATUIT
"""

import os
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime
import httpx
from dotenv import load_dotenv

import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY     = os.getenv("GEMINI_API_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
STRIPE_SECRET  = os.getenv("STRIPE_SECRET_KEY")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger("CEO_BOT")

# Configure Gemini
genai.configure(api_key=GEMINI_KEY)

# ─────────────────────────────────────────────────────
#  APPEL GEMINI
# ─────────────────────────────────────────────────────

def gemini_call(system_prompt: str, history: list, max_tokens: int = 1500) -> str:
    """Appelle Gemini 1.5 Flash avec historique de conversation."""
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=system_prompt
        )

        # Convertir l'historique au format Gemini
        gemini_history = []
        messages = history[-20:]

        for msg in messages[:-1]:  # Tout sauf le dernier
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat = model.start_chat(history=gemini_history)

        # Dernier message
        last_msg = messages[-1]["content"] if messages else "Bonjour"
        response = chat.send_message(last_msg)
        return response.text

    except Exception as e:
        log.error(f"Gemini error: {e}")
        return f"❌ Erreur Gemini : {e}"

def gemini_quick(system_prompt: str, message: str, max_tokens: int = 100) -> str:
    """Appel Gemini simple sans historique (pour le routeur)."""
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=system_prompt
        )
        response = model.generate_content(message)
        return response.text.strip()
    except Exception as e:
        return "ceo"

# ─────────────────────────────────────────────────────
#  SUPABASE — MÉMOIRE LONGUE
# ─────────────────────────────────────────────────────

def _default_memory():
    return {"history": [], "cycles": 0, "niche": None, "last_agent": None, "mrr": 0, "expenses": 0}

async def db_load(user_id: int) -> dict:
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
                    "expenses":   row.get("expenses", 0),
                }
    except Exception as e:
        log.error(f"DB load error: {e}")
    return _default_memory()

async def db_save(user_id: int, memory: dict):
    if not SUPABASE_URL:
        return
    try:
        payload = {
            "user_id":    user_id,
            "history":    memory.get("history", [])[-50:],
            "cycles":     memory.get("cycles", 0),
            "niche":      memory.get("niche"),
            "last_agent": memory.get("last_agent"),
            "mrr":        memory.get("mrr", 0),
            "expenses":   memory.get("expenses", 0),
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

# ─────────────────────────────────────────────────────
#  STRIPE
# ─────────────────────────────────────────────────────

async def check_stripe_revenue() -> str:
    if not STRIPE_SECRET:
        return "❌ Stripe non configuré — ajoute STRIPE_SECRET_KEY dans les variables"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.stripe.com/v1/charges?limit=10",
                auth=(STRIPE_SECRET, "")
            )
            data = r.json()
            charges = data.get("data", [])
            total = sum(c["amount"] for c in charges if c["status"] == "succeeded") / 100
            return f"💳 Dernières 10 transactions Stripe : ${total:.2f}"
    except Exception as e:
        return f"❌ Erreur Stripe : {e}"

# ─────────────────────────────────────────────────────
#  PROMPTS DES 13 AGENTS
# ─────────────────────────────────────────────────────

CEO_PROMPT = """Tu es CEO-AI, l'orchestrateur principal d'un système de 13 agents autonomes.
LANGUE : Détecte la langue de l'utilisateur et réponds toujours dans la même langue.
Tu coordonnes tous les agents. Tu parles comme un vrai CEO : direct, confiant, sans blabla.
Propose toujours une action suivante logique."""

MORNING_REPORT_PROMPT = """Tu es CEO-AI. Génère un rapport matinal motivant et actionnable en français.
Format :
🌅 Bonjour ! Voici ton brief CEO du jour.
📊 SITUATION : [cycles, niche, MRR]
🎯 PRIORITÉ DU JOUR : [1 action concrète]
⚡ 3 TÂCHES RAPIDES : [3 micro-tâches moins de 30 min]
💡 INSIGHT : [1 conseil stratégique]
🚀 OBJECTIF SEMAINE : [1 objectif mesurable]
Max 200 mots."""

ROUTER_PROMPT = """Analyse le message et retourne UNIQUEMENT le nom de l'agent parmi :
scout, oracle, forge, pulse, lens, design, code, spy, social, closer, finance, legal, seo, ceo

Règles :
- marché, niche, opportunité, explorer → scout
- choisir, sélectionner, stratégie → oracle
- produit, SaaS, features, pricing, MVP → forge
- marketing, posts, scripts, publicité → pulse
- métriques, analytics, KPI, scale, pivot → lens
- landing page, site, HTML → design
- code, script, automatisation, Python → code
- concurrents, analyse → spy
- réseaux sociaux, Instagram, LinkedIn posts → social
- prospection, DM, closing, vente → closer
- finance, MRR, ARR, rentabilité, churn → finance
- juridique, CGV, contrat, RGPD → legal
- SEO, article, blog, référencement → seo
- tout le reste → ceo

Retourne UNIQUEMENT le mot clé, rien d'autre."""

AGENTS = {
    "scout": {
        "emoji": "🔍", "name": "SCOUT",
        "prompt": "Tu es l'agent SCOUT, expert en exploration de marché.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nGénère et score 20 niches micro-SaaS minimum. Pour chaque : problème urgent, urgence/10, monétisation/10, facilité/10, score composite, taille marché, concurrents. Trie par score décroissant."
    },
    "oracle": {
        "emoji": "🎯", "name": "ORACLE",
        "prompt": "Tu es l'agent ORACLE, expert en stratégie.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nSélectionne la meilleure niche : justification 4-5 phrases, risques, ICP précis, positionnement différenciant, score confiance /100."
    },
    "forge": {
        "emoji": "⚡", "name": "FORGE",
        "prompt": "Tu es l'agent FORGE, expert en conception produit micro-SaaS.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nLivre : nom + tagline, proposition de valeur, 5 features MVP, 3 features V2, pricing 3 tiers, stack technique, temps MVP, MRR cible 3/6/12 mois."
    },
    "pulse": {
        "emoji": "📣", "name": "PULSE",
        "prompt": "Tu es l'agent PULSE, expert en marketing.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nGénère : 10 posts LinkedIn/Twitter prêts à publier, 5 emails cold outreach, 3 scripts vidéo complets, 5 hooks publicitaires, plan acquisition 3 phases."
    },
    "lens": {
        "emoji": "📊", "name": "LENS",
        "prompt": "Tu es l'agent LENS, expert en analyse de performance.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nAnalyse les métriques vs benchmarks, décide SCALE/OPTIMIZE/PIVOT, donne 3 actions prioritaires avec impact estimé."
    },
    "design": {
        "emoji": "🎨", "name": "DESIGN",
        "prompt": "Tu es l'agent DESIGN, expert en landing pages.\nLANGUE : Réponds dans la même langue (code HTML en anglais).\nGénère du HTML/CSS complet : hero, problème/solution, features, témoignages, pricing 3 tiers, FAQ, footer. Design sombre #0a0a0a, accent #00ff88. Code 100% fonctionnel."
    },
    "code": {
        "emoji": "💻", "name": "CODE",
        "prompt": "Tu es l'agent CODE, expert en développement.\nLANGUE : Réponds dans la même langue (code en anglais).\nGénère du code Python/JS/HTML propre et commenté. Toujours : explication, dépendances, code complet, instructions."
    },
    "spy": {
        "emoji": "🔎", "name": "SPY",
        "prompt": "Tu es l'agent SPY, expert en analyse concurrentielle.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nAnalyse : top 5 concurrents avec prix/features/forces/faiblesses, gaps marché, angle différenciation, score faisabilité /10, stratégie 90 jours."
    },
    "social": {
        "emoji": "📱", "name": "SOCIAL",
        "prompt": "Tu es l'agent SOCIAL, expert réseaux sociaux.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nCrée : calendrier éditorial 30 jours, posts par plateforme (LinkedIn/Twitter/Instagram/TikTok), hashtags, meilleurs horaires, 3 idées virales."
    },
    "closer": {
        "emoji": "🎯", "name": "CLOSER",
        "prompt": "Tu es l'agent CLOSER, expert en prospection et closing.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nGénère : 20 messages LinkedIn/DM personnalisés, séquences follow-up J+0/J+3/J+7, scripts d'appel, réponses aux objections, taux de conversion estimé."
    },
    "finance": {
        "emoji": "💰", "name": "FINANCE",
        "prompt": "Tu es l'agent FINANCE, expert en finances SaaS.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nAnalyse : MRR, ARR, churn rate, CAC, LTV, ratio LTV/CAC, runway, break-even, marges. Recommandations pour optimiser la rentabilité."
    },
    "legal": {
        "emoji": "⚖️", "name": "LEGAL",
        "prompt": "Tu es l'agent LEGAL, expert en documents juridiques SaaS.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nGénère : CGV, politique de confidentialité RGPD, mentions légales, contrats clients. Précise que ce n'est pas un avis juridique officiel."
    },
    "seo": {
        "emoji": "📈", "name": "SEO",
        "prompt": "Tu es l'agent SEO, expert en référencement.\nLANGUE : Réponds dans la même langue que l'utilisateur.\nGénère : articles de blog 1500-2000 mots optimisés SEO, meta titles, meta descriptions, structure H1/H2/H3, mots-clés cibles. Contenu prêt à publier."
    },
}

# ─────────────────────────────────────────────────────
#  CHAÎNE AUTONOME
# ─────────────────────────────────────────────────────

CHAIN_STEPS = [
    ("scout",   "Explore le marché. Génère 20 niches scorées et identifie la meilleure."),
    ("oracle",  "Analyse les niches et sélectionne la meilleure avec justification complète."),
    ("forge",   "Conçois le produit micro-SaaS complet pour la niche sélectionnée."),
    ("pulse",   "Génère le plan marketing complet : 10 posts, 5 emails, 3 scripts, 5 hooks."),
    ("seo",     "Génère un article SEO de 1500 mots pour attirer du trafic vers ce produit."),
    ("closer",  "Génère 20 messages de prospection LinkedIn pour ce produit."),
]

async def run_autonomous_chain(user_id: int, bot: Bot, chat_id: int):
    await bot.send_message(
        chat_id=chat_id,
        text="🔄 *Chaîne autonome lancée — 6 agents vont s'exécuter automatiquement*",
        parse_mode=ParseMode.MARKDOWN
    )
    memory = await db_load(user_id)
    context_summary = ""

    for i, (agent_key, task) in enumerate(CHAIN_STEPS):
        agent = AGENTS[agent_key]
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏳ Étape {i+1}/6 — {agent['emoji']} Agent {agent['name']} en cours..."
        )
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        full_task = task
        if context_summary:
            full_task += f"\n\nContexte des étapes précédentes :\n{context_summary}"

        memory["history"].append({"role": "user", "content": full_task})

        reply_text = gemini_call(agent["prompt"], memory["history"])
        prefix = f"{agent['emoji']} *Agent {agent['name']}*\n{'─'*25}\n\n"
        reply = prefix + reply_text

        memory["history"].append({"role": "assistant", "content": reply_text})
        context_summary += f"\n[{agent['name']}] : {reply_text[:200]}..."

        chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
        for chunk in chunks:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)

        await asyncio.sleep(2)

    memory["cycles"] += 1
    memory["last_agent"] = "chain"
    await db_save(user_id, memory)

    await bot.send_message(
        chat_id=chat_id,
        text="✅ *Chaîne autonome terminée ! 6 étapes complétées.*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )

# ─────────────────────────────────────────────────────
#  TRAITEMENT DES MESSAGES
# ─────────────────────────────────────────────────────

async def route_message(message: str) -> str:
    result = gemini_quick(ROUTER_PROMPT, message)
    agent = result.strip().lower().split()[0] if result else "ceo"
    return agent if agent in AGENTS or agent == "ceo" else "ceo"

async def call_agent(agent_key: str, history: list) -> str:
    if agent_key in AGENTS:
        agent = AGENTS[agent_key]
        system = agent["prompt"]
        prefix = f"{agent['emoji']} Agent {agent['name']} activé\n{'─'*30}\n\n"
    else:
        system = CEO_PROMPT
        prefix = "🤖 CEO-AI\n"
    reply = gemini_call(system, history)
    return prefix + reply

async def generate_morning_report(memory: dict) -> str:
    context = f"Cycles={memory['cycles']}, niche={memory.get('niche','aucune')}, MRR=${memory.get('mrr',0)}, dépenses=${memory.get('expenses',0)}, dernier agent={memory.get('last_agent','aucun')}"
    return gemini_call(MORNING_REPORT_PROMPT, [{"role": "user", "content": context}])

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
        except Exception as e:
            log.error(f"Erreur rapport {user_id}: {e}")

async def morning_scheduler(bot: Bot):
    while True:
        now = datetime.utcnow()
        if now.hour == 8 and now.minute == 0:
            await send_morning_reports(bot)
            await asyncio.sleep(61)
        await asyncio.sleep(30)

# ─────────────────────────────────────────────────────
#  CLAVIER TELEGRAM
# ─────────────────────────────────────────────────────

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Chaîne autonome", callback_data="chain"),
         InlineKeyboardButton("🔍 Scout", callback_data="agent_scout")],
        [InlineKeyboardButton("⚡ Forge", callback_data="agent_forge"),
         InlineKeyboardButton("📣 Pulse", callback_data="agent_pulse")],
        [InlineKeyboardButton("📊 Lens", callback_data="agent_lens"),
         InlineKeyboardButton("🔎 Spy", callback_data="agent_spy")],
        [InlineKeyboardButton("🎨 Design", callback_data="agent_design"),
         InlineKeyboardButton("💻 Code", callback_data="agent_code")],
        [InlineKeyboardButton("📱 Social", callback_data="agent_social"),
         InlineKeyboardButton("🎯 Closer", callback_data="agent_closer")],
        [InlineKeyboardButton("💰 Finance", callback_data="agent_finance"),
         InlineKeyboardButton("⚖️ Legal", callback_data="agent_legal")],
        [InlineKeyboardButton("📈 SEO", callback_data="agent_seo"),
         InlineKeyboardButton("📋 Rapport", callback_data="report")],
        [InlineKeyboardButton("🧠 Mémoire", callback_data="memory"),
         InlineKeyboardButton("💳 Stripe", callback_data="stripe")],
    ])

QUICK_MESSAGES = {
    "agent_scout":   "Explore le marché. Génère 20 niches scorées.",
    "agent_oracle":  "Sélectionne la meilleure niche avec justification complète.",
    "agent_forge":   "Conçois le produit micro-SaaS complet.",
    "agent_pulse":   "Génère le plan marketing complet.",
    "agent_lens":    "Métriques : 800 visiteurs, 12 signups, 3 clients à $29/mo. Analyse et décide.",
    "agent_design":  "Crée une landing page HTML complète.",
    "agent_code":    "Génère un script Python pour automatiser les relances email B2B.",
    "agent_spy":     "Analyse les 5 concurrents principaux du marché B2B SaaS.",
    "agent_social":  "Crée un calendrier éditorial 30 jours et 10 posts.",
    "agent_closer":  "Génère 20 messages LinkedIn de prospection pour mon SaaS B2B.",
    "agent_finance": "Analyse mes finances : MRR $2,400, churn 5%, CAC $45, dépenses $800/mois.",
    "agent_legal":   "Génère les CGV et politique de confidentialité RGPD pour mon SaaS.",
    "agent_seo":     "Génère un article SEO de 1500 mots sur l'automatisation B2B.",
}

FORCE_AGENTS = {k: k.replace("agent_", "") for k in QUICK_MESSAGES if k.startswith("agent_")}

# ─────────────────────────────────────────────────────
#  HANDLERS TELEGRAM
# ─────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"🤖 *CEO-AI v4 Gemini — Bonjour {user.first_name} !*\n\n"
        "13 agents spécialisés + chaîne autonome :\n\n"
        "🔍 SCOUT · 🎯 ORACLE · ⚡ FORGE · 📣 PULSE\n"
        "📊 LENS · 🎨 DESIGN · 💻 CODE · 🔎 SPY\n"
        "📱 SOCIAL · 🎯 CLOSER · 💰 FINANCE\n"
        "⚖️ LEGAL · 📈 SEO\n\n"
        "🔄 Chaîne autonome — 6 agents s'enchaînent seuls\n"
        "📋 Rapport matinal automatique à 9h\n"
        "💳 Suivi Stripe en temps réel\n\n"
        "Powered by Gemini 1.5 Flash\n\n"
        "Écris-moi ou choisis un agent 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = await db_load(update.effective_user.id)
    margin = mem.get("mrr", 0) - mem.get("expenses", 0)
    await update.message.reply_text(
        f"🧠 *Mémoire CEO-AI :*\n\n"
        f"• Cycles : {mem['cycles']}\n"
        f"• Niche : {mem.get('niche') or 'Aucune'}\n"
        f"• MRR simulé : ${mem.get('mrr', 0)}\n"
        f"• Dépenses : ${mem.get('expenses', 0)}\n"
        f"• Marge : ${margin}\n"
        f"• Dernier agent : {mem.get('last_agent') or 'Aucun'}\n"
        f"• Messages : {len(mem['history'])}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = await process_message(update.effective_user.id, update.message.text)
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        await update.message.reply_text(
            chunk,
            reply_markup=main_keyboard() if i == len(chunks) - 1 else None
        )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    user_id = query.from_user.id

    if action == "memory":
        mem = await db_load(user_id)
        await query.message.reply_text(
            f"🧠 Cycles: {mem['cycles']} | MRR: ${mem.get('mrr',0)} | Agent: {mem.get('last_agent','Aucun')}",
            reply_markup=main_keyboard()
        )
        return

    if action == "report":
        await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
        mem = await db_load(user_id)
        report = await generate_morning_report(mem)
        await query.message.reply_text(
            f"📋 *BRIEF CEO*\n\n{report}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_keyboard()
        )
        return

    if action == "stripe":
        await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
        result = await check_stripe_revenue()
        await query.message.reply_text(result, reply_markup=main_keyboard())
        return

    if action == "chain":
        asyncio.create_task(run_autonomous_chain(user_id, ctx.bot, query.message.chat_id))
        return

    msg = QUICK_MESSAGES.get(action, "Lance une analyse complète.")
    force = FORCE_AGENTS.get(action)
    await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    reply = await process_message(user_id, msg, force_agent=force)
    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
    for i, chunk in enumerate(chunks):
        await query.message.reply_text(
            chunk,
            reply_markup=main_keyboard() if i == len(chunks) - 1 else None
        )

async def post_init(app: Application):
    asyncio.create_task(morning_scheduler(app.bot))
    log.info("⏰ Scheduler matinal activé")

# ─────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN manquant !")
        return
    if not GEMINI_KEY:
        log.error("GEMINI_API_KEY manquant !")
        return

    log.info("🚀 CEO-AI v4 Gemini démarré...")
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
    log.info("✅ 13 agents Gemini + chaîne autonome + alertes actifs !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
