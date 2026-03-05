    """
CEO-AI v4 — GEMINI 2.0 + AGENT AUTONOME COMPLET
13 agents + Chaîne autonome 3 piliers + Supabase + Alertes matinales
"""

import os
import json
import logging
import asyncio
import time
from datetime import datetime
import httpx
from dotenv import load_dotenv

from google import genai
from google.genai import types

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

client_gemini = genai.Client(api_key=GEMINI_KEY)
MODEL = "gemini-2.0-flash"

# ─────────────────────────────────────────────────────
#  PILIER 3 — CONSTANTES QUOTA
# ─────────────────────────────────────────────────────

INTER_AGENT_DELAY = 10
RETRY_DELAY_429   = 65
MAX_RETRIES       = 3
MAX_CONTEXT_CHARS = 400

def truncate_context(text: str) -> str:
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    return text[:MAX_CONTEXT_CHARS] + "...[résumé]"

# ─────────────────────────────────────────────────────
#  PILIER 1 — APPELS GEMINI AVEC AUTO-RETRY
# ─────────────────────────────────────────────────────

def gemini_call_safe(system_prompt: str, history: list, max_tokens: int = 1000):
    """Gemini avec retry automatique 429 + mémoire courte."""
    short_history = history[-6:]
    contents = []
    for msg in short_history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part(text=truncate_context(msg["content"]))]
        ))
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client_gemini.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                )
            )
            return response.text, True
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                wait = RETRY_DELAY_429 * attempt
                log.warning(f"⏳ Quota 429 — Attente {wait}s (tentative {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            elif "503" in err or "unavailable" in err.lower():
                time.sleep(30 * attempt)
            else:
                return f"ERREUR: {err}", False
    return "ERREUR: Quota épuisé après tous les retries", False

def gemini_call(system_prompt: str, history: list, max_tokens: int = 1200) -> str:
    result, _ = gemini_call_safe(system_prompt, history, max_tokens)
    return result

def gemini_quick(system_prompt: str, message: str) -> str:
    try:
        response = client_gemini.models.generate_content(
            model=MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=message)])],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=30,
                temperature=0.1,
            )
        )
        return response.text.strip()
    except Exception as e:
        log.error(f"Gemini quick error: {e}")
        return "ceo"

# ─────────────────────────────────────────────────────
#  SUPABASE
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
        return "❌ Stripe non configuré"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.stripe.com/v1/charges?limit=10",
                auth=(STRIPE_SECRET, "")
            )
            charges = r.json().get("data", [])
            total = sum(c["amount"] for c in charges if c["status"] == "succeeded") / 100
            return f"💳 Dernières 10 transactions : ${total:.2f}"
    except Exception as e:
        return f"❌ Erreur Stripe : {e}"

# ─────────────────────────────────────────────────────
#  PROMPTS 13 AGENTS
# ─────────────────────────────────────────────────────

CEO_PROMPT = """Tu es CEO-AI, l'orchestrateur principal d'un système de 13 agents autonomes.
LANGUE : Détecte la langue de l'utilisateur et réponds toujours dans la même langue.
Tu coordonnes tous les agents. Tu parles comme un vrai CEO : direct, confiant, sans blabla.
Propose toujours une action suivante logique."""

MORNING_REPORT_PROMPT = """Tu es CEO-AI. Génère un rapport matinal motivant en français.
Format :
🌅 Bonjour ! Voici ton brief CEO du jour.
📊 SITUATION : [cycles, niche, MRR]
🎯 PRIORITÉ DU JOUR : [1 action concrète]
⚡ 3 TÂCHES RAPIDES : [moins de 30 min chacune]
💡 INSIGHT : [1 conseil stratégique]
🚀 OBJECTIF SEMAINE : [1 objectif mesurable]
Max 200 mots."""

ROUTER_PROMPT = """Analyse le message et retourne UNIQUEMENT le nom de l'agent parmi :
scout, oracle, forge, pulse, lens, design, code, spy, social, closer, finance, legal, seo, ceo

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

Retourne UNIQUEMENT le mot clé."""

AGENTS = {
    "scout":   {"emoji": "🔍", "name": "SCOUT",   "prompt": "Tu es l'agent SCOUT, expert en exploration de marché.\nLANGUE : Même langue que l'utilisateur.\nGénère 20 niches micro-SaaS scorées : problème urgent, urgence/10, monétisation/10, facilité/10, score composite, taille marché, concurrents. Trie par score décroissant."},
    "oracle":  {"emoji": "🎯", "name": "ORACLE",  "prompt": "Tu es l'agent ORACLE, expert en stratégie.\nLANGUE : Même langue que l'utilisateur.\nSélectionne la meilleure niche : justification 4-5 phrases, risques, ICP précis, positionnement, score confiance /100."},
    "forge":   {"emoji": "⚡", "name": "FORGE",   "prompt": "Tu es l'agent FORGE, expert en conception produit micro-SaaS.\nLANGUE : Même langue que l'utilisateur.\nLivre : nom + tagline, valeur, 5 features MVP, 3 features V2, pricing 3 tiers, stack, temps MVP, MRR cible 3/6/12 mois."},
    "pulse":   {"emoji": "📣", "name": "PULSE",   "prompt": "Tu es l'agent PULSE, expert en marketing.\nLANGUE : Même langue que l'utilisateur.\nGénère : 10 posts LinkedIn/Twitter, 5 emails cold outreach, 3 scripts vidéo, 5 hooks publicitaires, plan acquisition 3 phases."},
    "lens":    {"emoji": "📊", "name": "LENS",    "prompt": "Tu es l'agent LENS, expert en analyse de performance.\nLANGUE : Même langue que l'utilisateur.\nAnalyse métriques vs benchmarks, décide SCALE/OPTIMIZE/PIVOT, donne 3 actions prioritaires avec impact estimé."},
    "design":  {"emoji": "🎨", "name": "DESIGN",  "prompt": "Tu es l'agent DESIGN, expert en landing pages.\nLANGUE : Même langue (code HTML en anglais).\nGénère HTML/CSS complet : hero, problème/solution, features, témoignages, pricing 3 tiers, FAQ, footer. Design sombre #0a0a0a, accent #00ff88."},
    "code":    {"emoji": "💻", "name": "CODE",    "prompt": "Tu es l'agent CODE, expert en développement.\nLANGUE : Même langue (code en anglais).\nGénère code Python/JS/HTML propre et commenté. Toujours : explication, dépendances, code complet, instructions."},
    "spy":     {"emoji": "🔎", "name": "SPY",     "prompt": "Tu es l'agent SPY, expert en analyse concurrentielle.\nLANGUE : Même langue que l'utilisateur.\nAnalyse : top 5 concurrents, prix/features/forces/faiblesses, gaps marché, différenciation, score /10, stratégie 90 jours."},
    "social":  {"emoji": "📱", "name": "SOCIAL",  "prompt": "Tu es l'agent SOCIAL, expert réseaux sociaux.\nLANGUE : Même langue que l'utilisateur.\nCrée : calendrier 30 jours, posts LinkedIn/Twitter/Instagram/TikTok, hashtags, horaires optimaux, 3 idées virales."},
    "closer":  {"emoji": "🎯", "name": "CLOSER",  "prompt": "Tu es l'agent CLOSER, expert en prospection.\nLANGUE : Même langue que l'utilisateur.\nGénère : 20 messages LinkedIn, follow-up J+0/J+3/J+7, scripts d'appel, réponses objections, taux conversion estimé."},
    "finance": {"emoji": "💰", "name": "FINANCE", "prompt": "Tu es l'agent FINANCE, expert en finances SaaS.\nLANGUE : Même langue que l'utilisateur.\nAnalyse : MRR, ARR, churn, CAC, LTV, LTV/CAC, runway, break-even, marges. Recommandations pour optimiser la rentabilité."},
    "legal":   {"emoji": "⚖️", "name": "LEGAL",   "prompt": "Tu es l'agent LEGAL, expert en documents juridiques SaaS.\nLANGUE : Même langue que l'utilisateur.\nGénère : CGV, RGPD, mentions légales, contrats clients. Précise que ce n'est pas un avis juridique officiel."},
    "seo":     {"emoji": "📈", "name": "SEO",     "prompt": "Tu es l'agent SEO, expert en référencement.\nLANGUE : Même langue que l'utilisateur.\nGénère : articles 1500-2000 mots optimisés SEO, meta title, meta description, structure H1/H2/H3, mots-clés cibles."},
}

# ─────────────────────────────────────────────────────
#  PILIER 2 — PENSÉE RÉCURSIVE + AUTO-DÉLÉGATION
# ─────────────────────────────────────────────────────

async def think_aloud(bot: Bot, chat_id: int, agent_name: str, task: str, context: str):
    thought, ok = gemini_call_safe(
        f"Tu es {agent_name}. Réfléchis en 3 points avant d'agir : 💭 Pensées | 🧠 Raisonnement | 📋 Plan. Max 100 mots. Contexte: {truncate_context(context)}",
        [{"role": "user", "content": task}],
        max_tokens=150
    )
    if ok:
        try:
            await bot.send_message(chat_id=chat_id, text=f"🧠 *{agent_name} — Réflexion*\n{'─'*20}\n\n{thought}", parse_mode=ParseMode.MARKDOWN)
        except:
            await bot.send_message(chat_id=chat_id, text=f"🧠 {agent_name}: {thought}")

async def lens_repair(bot: Bot, chat_id: int, failed_agent: str, error: str, task: str) -> str:
    await bot.send_message(chat_id=chat_id, text=f"🔧 *LENS répare {failed_agent}*\nErreur: `{error[:150]}`", parse_mode=ParseMode.MARKDOWN)
    fix, ok = gemini_call_safe(
        "Tu es LENS. Un agent a échoué. Propose une version SIMPLIFIÉE de la tâche (max 80 mots). Réponds uniquement avec: TÂCHE: [ta correction]",
        [{"role": "user", "content": f"Agent: {failed_agent} | Erreur: {error} | Tâche: {task}"}],
        max_tokens=120
    )
    if ok and "TÂCHE:" in fix:
        corrected = fix.split("TÂCHE:")[1].strip()
        await bot.send_message(chat_id=chat_id, text=f"✅ *LENS — Correction*\n{corrected}", parse_mode=ParseMode.MARKDOWN)
        return corrected
    return f"Version simplifiée: {task[:80]}. Sois très concis."

async def check_auto_delegation(result_text: str, current_agent: str, queue: list) -> list:
    triggers = {
        "scout":  (["score 9", "score 10", "opportunité exceptionnelle"], "forge",  "Conçois immédiatement le MVP pour la niche top identifiée par SCOUT."),
        "oracle": (["aucun concurrent", "marché vide", "gap énorme"],      "spy",    "Analyse en urgence les concurrents de la niche sélectionnée."),
        "lens":   (["scale", "taux de conversion élevé", "pipeline plein"],"pulse",  "Génère une campagne marketing agressive basée sur l'analyse LENS."),
    }
    if current_agent not in triggers:
        return queue
    keywords, agent_to_add, task_to_add = triggers[current_agent]
    r = result_text.lower()
    for kw in keywords:
        if kw in r and not any(s[0] == agent_to_add for s in queue):
            log.info(f"🔗 Auto-délégation: {current_agent} → {agent_to_add}")
            return [(agent_to_add, task_to_add)] + queue
    return queue

# ─────────────────────────────────────────────────────
#  CHAÎNE AUTONOME COMPLÈTE
# ─────────────────────────────────────────────────────

async def run_autonomous_chain(user_id: int, bot: Bot, chat_id: int):
    start_time = datetime.now()

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🚀 *CHAÎNE AUTONOME DÉMARRÉE*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🧠 Pensée récursive : activée\n"
            "🔄 Retry auto 429 : activé (65s)\n"
            "⏱️ Délai entre agents : 10s\n"
            "🔗 Auto-délégation : activée\n"
            "🔧 LENS auto-correction : activé\n\n"
            "_Ferme Telegram — je travaille seul._"
        ),
        parse_mode=ParseMode.MARKDOWN
    )

    memory = await db_load(user_id)
    context_summary = ""
    completed = []
    failed = []

    agent_queue = [
        ("scout",  "Explore le marché. Génère 20 niches micro-SaaS scorées. Identifie la top 3."),
        ("oracle", "Sélectionne la meilleure niche parmi celles de SCOUT. Justification + ICP."),
        ("forge",  "Conçois le produit micro-SaaS MVP complet pour la niche choisie."),
        ("pulse",  "Génère 5 posts LinkedIn et 3 emails cold outreach pour ce produit."),
        ("seo",    "Génère 1 article SEO de 1000 mots pour ce produit."),
        ("closer", "Génère 10 messages LinkedIn de prospection pour ce produit."),
    ]

    current_step = 0

    while agent_queue:
        agent_key, task = agent_queue.pop(0)
        current_step += 1
        total = current_step + len(agent_queue)

        if agent_key not in AGENTS:
            continue

        agent = AGENTS[agent_key]
        progress = "▓" * current_step + "░" * len(agent_queue)

        await bot.send_message(
            chat_id=chat_id,
            text=f"⏳ *Étape {current_step}/{total}*\n`{progress}`\n\n{agent['emoji']} Agent *{agent['name']}*",
            parse_mode=ParseMode.MARKDOWN
        )

        await think_aloud(bot, chat_id, agent["name"], task, context_summary)
        await asyncio.sleep(INTER_AGENT_DELAY)

        full_task = task + (f"\n\nContexte: {truncate_context(context_summary)}" if context_summary else "")
        memory["history"].append({"role": "user", "content": full_task})

        reply_text, success = gemini_call_safe(agent["prompt"], memory["history"], max_tokens=900)

        if not success:
            failed.append(agent_key)
            corrected_task = await lens_repair(bot, chat_id, agent["name"], reply_text, task)
            await asyncio.sleep(INTER_AGENT_DELAY)
            memory["history"].append({"role": "user", "content": corrected_task})
            reply_text, success = gemini_call_safe(agent["prompt"], memory["history"], max_tokens=600)
            if not success:
                await bot.send_message(chat_id=chat_id, text=f"⚠️ *{agent['name']} ignoré* après 2 tentatives.", parse_mode=ParseMode.MARKDOWN)
                memory["history"].append({"role": "assistant", "content": f"[{agent['name']} échoué]"})
                continue

        completed.append(agent_key)
        memory["history"].append({"role": "assistant", "content": reply_text})
        context_summary += f" | [{agent['name']}]: {reply_text[:150]}..."
        if agent_key == "scout":
            memory["cycles"] = memory.get("cycles", 0) + 1

        full_reply = f"{agent['emoji']} *Agent {agent['name']}*\n{'─'*25}\n\n{reply_text}"
        for chunk in [full_reply[i:i+3500] for i in range(0, len(full_reply), 3500)]:
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
            except:
                await bot.send_message(chat_id=chat_id, text=chunk)
            await asyncio.sleep(1)

        agent_queue = await check_auto_delegation(reply_text, agent_key, agent_queue)

    memory["last_agent"] = "chain_autonomous"
    await db_save(user_id, memory)

    duration = int((datetime.now() - start_time).total_seconds() / 60)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ *CHAÎNE AUTONOME TERMINÉE*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏱️ Durée : {duration} min\n"
            f"✅ Réussis : {len(completed)} ({', '.join(completed)})\n"
            f"❌ Échoués : {len(failed)} ({', '.join(failed) if failed else 'aucun'})\n\n"
            "📋 Tout est prêt à utiliser."
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )

# ─────────────────────────────────────────────────────
#  TRAITEMENT MESSAGES
# ─────────────────────────────────────────────────────

async def route_message(message: str) -> str:
    result = gemini_quick(ROUTER_PROMPT, message)
    agent = result.strip().lower().split()[0] if result else "ceo"
    return agent if agent in AGENTS or agent == "ceo" else "ceo"

async def call_agent(agent_key: str, history: list) -> str:
    if agent_key in AGENTS:
        agent = AGENTS[agent_key]
        prefix = f"{agent['emoji']} Agent {agent['name']} activé\n{'─'*30}\n\n"
        system = agent["prompt"]
    else:
        prefix = "🤖 CEO-AI\n"
        system = CEO_PROMPT
    return prefix + gemini_call(system, history)

async def generate_morning_report(memory: dict) -> str:
    context = f"Cycles={memory['cycles']}, niche={memory.get('niche','aucune')}, MRR=${memory.get('mrr',0)}, dépenses=${memory.get('expenses',0)}"
    return gemini_call(MORNING_REPORT_PROMPT, [{"role": "user", "content": context}])

async def process_message(user_id: int, user_message: str, force_agent: str = None) -> str:
    memory = await db_load(user_id)
    memory["history"].append({"role": "user", "content": user_message})
    agent_key = force_agent or await route_message(user_message)
    memory["last_agent"] = agent_key
    if agent_key == "scout":
        memory["cycles"] = memory.get("cycles", 0) + 1
    reply = await call_agent(agent_key, memory["history"])
    memory["history"].append({"role": "assistant", "content": reply})
    await db_save(user_id, memory)
    return reply

# ─────────────────────────────────────────────────────
#  ALERTES MATINALES
# ─────────────────────────────────────────────────────

async def send_morning_reports(bot: Bot):
    for user_id in await db_get_all_users():
        try:
            mem = await db_load(user_id)
            report = await generate_morning_report(mem)
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
#  CLAVIER
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
#  HANDLERS
# ─────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🤖 *CEO-AI v4 — Bonjour {user.first_name} !*\n\n"
        "13 agents + chaîne autonome 3 piliers :\n\n"
        "🔍 SCOUT · 🎯 ORACLE · ⚡ FORGE · 📣 PULSE\n"
        "📊 LENS · 🎨 DESIGN · 💻 CODE · 🔎 SPY\n"
        "📱 SOCIAL · 🎯 CLOSER · 💰 FINANCE · ⚖️ LEGAL · 📈 SEO\n\n"
        "🔄 Chaîne autonome · 📋 Rapport 9h · 💳 Stripe\n\n"
        "Écris-moi ou choisis un agent 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = await db_load(update.effective_user.id)
    await update.message.reply_text(
        f"🧠 *Mémoire :*\n\n"
        f"• Cycles : {mem['cycles']}\n"
        f"• Niche : {mem.get('niche') or 'Aucune'}\n"
        f"• MRR : ${mem.get('mrr', 0)}\n"
        f"• Dépenses : ${mem.get('expenses', 0)}\n"
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
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_keyboard() if i == len(chunks)-1 else None)
        except:
            await update.message.reply_text(chunk,
                reply_markup=main_keyboard() if i == len(chunks)-1 else None)

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
        try:
            await query.message.reply_text(f"📋 *BRIEF CEO*\n\n{report}",
                parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())
        except:
            await query.message.reply_text(report, reply_markup=main_keyboard())
        return

    if action == "stripe":
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
        try:
            await query.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_keyboard() if i == len(chunks)-1 else None)
        except:
            await query.message.reply_text(chunk,
                reply_markup=main_keyboard() if i == len(chunks)-1 else None)

async def post_init(app: Application):
    asyncio.create_task(morning_scheduler(app.bot))
    log.info("⏰ Scheduler matinal activé")

# ─────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN or not GEMINI_KEY:
        log.error("TOKEN manquant !")
        return
    log.info("🚀 CEO-AI v4 Gemini 2.0 démarré...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("✅ 13 agents + chaîne autonome + alertes actifs !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
