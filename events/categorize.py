"""Sort events into vibes (what kind of time it is) and topics (what it is about).

Upstreams give titles, a presenting calendar with a short description, host names and sometimes
tags, but no descriptions. Keyword rules over those fields are transparent, fast and testable.
Each field has a weight; a category needs a score of THRESHOLD, so one strong hit in the title is
enough, while the presenting calendar alone needs two signals.

To tune: run ``python3 -m events.categorize data/events.db`` to print coverage and samples.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RULES_VERSION = 5
THRESHOLD = 3.0
MAX_PER_KIND = 3
FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "presenter": 2.0, "description": 1.0, "hosts": 0.75}


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    emoji: str
    hint: str
    strong: re.Pattern[str]
    weak: re.Pattern[str] | None = None

    def score(self, fields: dict[str, str]) -> float:
        total = 0.0
        for name, text in fields.items():
            if not text:
                continue
            weight = FIELD_WEIGHTS[name]
            if self.strong.search(text):
                total += weight
            elif self.weak is not None and self.weak.search(text):
                total += weight / 2
        return total

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "emoji": self.emoji, "hint": self.hint}


def _words(*alternatives: str) -> re.Pattern[str]:
    """One case-insensitive pattern matching any alternative as a whole word. Alternatives may use regex syntax."""
    return re.compile(r"(?<![\w-])(?:" + "|".join(f"(?:{alt})" for alt in alternatives) + r")(?![\w])", re.I)


VIBES: tuple[Category, ...] = (
    Category("party", "Party", "🎉", "Parties, DJ sets, dancing and nights out", _words(
        r"part(?:y|ies)", r"rave", r"dj(?:s| set)?", r"dance part(?:y|ies)", r"dancing", r"club night", r"after ?-?part(?:y|ies)",
        r"afters", r"nightlife", r"disco", r"karaoke", r"soir[ée]e", r"celebrat(?:e|ion|ing)", r"bash", r"gala", r"open bar",
        r"halloween", r"nye", r"new year'?s eve", r"birthday", r"rooftop party", r"boat party", r"prom", r"fiesta", r"ball",
        r"costume", r"launch party", r"summer send-?off", r"block party", r"festival", r"fest"),
        _words(r"rooftop", r"night out", r"late night", r"vibes", r"drinks")),
    Category("social", "Meet people", "🤝", "Mixers, meetups, happy hours and community nights", _words(
        r"meet ?-?ups?", r"mixers?", r"networking", r"happy hours?", r"social(?:s)?", r"hang ?outs?", r"hangs?",
        r"get[- ]togethers?", r"meet (?:and|&|n) greet", r"coffee (?:chat|meetup|social|walk)s?", r"gathering",
        r"reunion", r"speed (?:networking|dating|friending)", r"singles", r"dating", r"potluck", r"picnic",
        r"founders? (?:dinner|breakfast|brunch|drinks|meetup|social|circle|night)", r"community (?:night|hang|gathering|day|dinner)",
        r"members(?: night| only)?", r"open house", r"friends(?:giving)?", r"circle", r"salon", r"irl", r"after ?work",
        r"aperitivos?", r"sundays? in", r"community (?:day|lunch|breakfast|brunch)", r"meet the", r"town ?halls?",
        r"new members?", r"onboarding", r"after ?hours", r"open campus", r"an evening (?:for|of|with)", r"ingathering"),
        _words(r"#\d+", r"tech week", r"#sftechweek", r"evenings?", r"nights?", r"pop-?ups?", r"club", r"welcome", r"community", r"connect(?:ing|ions)?", r"drinks", r"brunch", r"dinner", r"sundays?", r"collective", r"commons")),
    Category("learn", "Learn", "🧠", "Talks, panels, workshops and reading groups", _words(
        r"talks?", r"panels?", r"fireside(?: chat)?", r"lectures?", r"seminars?", r"workshops?", r"master ?class(?:es)?",
        r"courses?", r"webinars?", r"reading (?:group|club)", r"paper (?:club|reading|group|night)s?", r"book club",
        r"journal club", r"ama", r"q ?(?:&|and) ?a", r"keynotes?", r"summit", r"conference", r"conf", r"symposium", r"forum",
        r"deep ?-?dive", r"explained", r"how (?:to|we|i)", r"intro(?:duction)? to", r"learn(?:ing)?", r"tutorials?",
        r"discussions?", r"debate", r"study group", r"office hours", r"training", r"briefing", r"conversations?",
        r"lessons?", r"101", r"crash course", r"bootcamp", r"guide to", r"case stud(?:y|ies)", r"lightning talks?",
        r"speaker series", r"book (?:talk|reading)", r"teach(?:-in|ing)", r"unconference", r"expert", r"learnings",
        r"roundtables?", r"study hall", r"introduction", r"playbook", r"interactive (?:practice|session)", r"in conversation",
        r"conversation with", r"talk series", r"lab session", r"ask me anything", r"primer", r"explainer",
        r"(?:claude|chatgpt|gpt|ai|cursor|grok|gemini)(?: code)? for", r"ワークショップ", r"勉強会", r"taller",
        r"why (?:we|i|you|your)"),
        _words(r"class(?:es)?", r"research", r"insights", r"future of", r"state of", r"lessons", r"playbook", r"salon",
        r"tours?", r"series", r"20[2-3]\d", r"group", r"classroom")),
    Category("build", "Build & hack", "🛠️", "Hackathons, build nights and coworking", _words(
        r"hack(?:athons?|ers?|ing)?", r"hack ?(?:night|day|week)s?", r"build(?:ers?|ing|athons?)?", r"co-?work(?:ing)?",
        r"game jam", r"sprints?", r"ship(?:ping)?", r"makerspace", r"code ?(?:along|night|jam)",
        r"coding", r"prototyp(?:e|ing)", r"build night", r"work session", r"hack house", r"buildspace", r"deep work",
        r"work ?-?along", r"focus session", r"impact labs?", r"(?:public|open) hours", r"hands-on",
        r"build session", r"lab night", r"office day", r"\w*hacks", r"blitz", r"lab prep", r"in-person lab"),
        _words(r"agents? day", r"open source", r"devs?", r"jam", r"makers?")),
    Category("showcase", "Demos & pitches", "🚀", "Demo days, pitch nights, launches and showcases", _words(
        r"demo ?(?:day|night|s)", r"demos?", r"pitch(?:es|ing| night| competition| day)?", r"showcase", r"launch(?:es)?",
        r"expo", r"science fair", r"show (?:and|&) tell", r"product hunt", r"shark tank", r"investor day", r"graduation",
        r"final presentations?", r"exhibition"),
        _words(r"fair", r"premiere", r"unveil")),
    Category("culture", "Arts & culture", "🎨", "Music, art, film, comedy and performance", _words(
        r"concerts?", r"live music", r"jazz", r"band", r"gigs?", r"orchestra", r"symphony", r"opera", r"choir", r"music",
        r"songwrit(?:ers?|ing)", r"acoustic", r"singers?", r"piano", r"art(?:s|ists?|work)?", r"galler(?:y|ies)", r"exhibit(?:ion)?s?",
        r"museum", r"painting", r"drawing", r"sketch(?:ing)?", r"crafts?", r"crafting", r"pottery", r"ceramics", r"photo ?walk",
        r"photography", r"poetry", r"poets?", r"writ(?:ing|ers?) (?:club|group|workshop|night)", r"literary", r"author",
        r"film", r"films", r"movie", r"screening", r"cinema", r"documentary", r"comedy", r"stand-?up", r"improv",
        r"theat(?:er|re)", r"performance", r"open mic", r"cabaret", r"drag", r"ballet", r"dance performance", r"zine",
        r"creative", r"design week", r"mural", r"storytelling", r"readings?", r"open studios?", r"opening reception",
        r"vernissage", r"listening (?:session|party)", r"record (?:fair|swap|store)", r"album", r"museum night"),
        _words(r"writing", r"books?", r"culture", r"aesthetics?")),
    Category("active", "Get moving", "🏃", "Runs, hikes, sports, climbing and fitness", _words(
        r"run (?:club|crew)", r"running club", r"(?:group|fun|social|morning|sunset|trail|sunday|saturday|community) run(?:s|ning)?", r"5k", r"10k", r"marathon", r"jog(?:ging)?", r"hikes?", r"hiking", r"yoga", r"pilates",
        r"climb(?:ing)?", r"boulder(?:ing)?", r"cycl(?:e|ing)", r"bikes?", r"biking", r"bike ride", r"surf(?:ing)?", r"swim(?:ming)?",
        r"soccer", r"football", r"basketball", r"volleyball", r"tennis", r"pickleball", r"padel", r"golf", r"sports?", r"fitness",
        r"work ?out", r"crossfit", r"sweat", r"dance class", r"salsa", r"bachata", r"kayak(?:ing)?", r"sail(?:ing)?", r"ski(?:ing)?",
        r"snowboard(?:ing)?", r"frisbee", r"ultimate", r"martial arts", r"jiu[- ]?jitsu", r"boxing", r"7-?aside", r"5-?aside",
        r"rowing", r"paddle(?:board)?", r"walk(?:ing)? club", r"stroll", r"trail", r"gym", r"hyrox", r"triathlon"),
        _words(r"trips?", r"outings?", r"walks?", r"outdoors?", r"beach", r"park", r"active", r"run", r"runs", r"running", r"strength", r"stretch(?:ing)?", r"lift(?:ing)?")),
    Category("wellness", "Chill & recharge", "🧘", "Meditation, sound baths, saunas and slow evenings", _words(
        r"meditat(?:e|ion|ions)", r"sound ?baths?", r"breath ?work", r"sauna", r"cold plunge", r"wellness", r"well-?being",
        r"mindful(?:ness)?", r"retreats?", r"healing", r"reiki", r"journal(?:ing)", r"tea ceremony", r"cacao", r"spa",
        r"recharge", r"self-?care", r"mental health", r"gratitude", r"qi ?gong", r"tai chi", r"restorative", r"somatic",
        r"ecstatic dance", r"slow (?:morning|evening|sunday)", r"unplug(?:ged)?", r"digital detox", r"nature walk", r"stargazing",
        r"onsen", r"hot springs?", r"float(?:ing)? (?:tank|session)", r"anxiety", r"loneliness", r"burnout"),
        _words(r"rest", r"reset", r"sunset", r"sunrise", r"tea", r"chill", r"cozy", r"calm", r"slow", r"quiet", r"reflection", r"intentions?")),
    Category("food", "Food & drink", "🍜", "Dinners, brunches, tastings and supper clubs", _words(
        r"dinners?", r"brunch(?:es)?", r"lunch(?:eon)?", r"breakfast", r"supper(?: club)?", r"potluck", r"tasting",
        r"wine", r"beer", r"cocktails?", r"coffee", r"matcha", r"boba", r"🧋", r"bbq", r"barbecue", r"food", r"cooking",
        r"chef", r"feast", r"picnic", r"dumplings?", r"pizza", r"tacos?", r"ramen", r"sushi", r"cheese", r"bak(?:ing|ery)",
        r"omakase", r"sake", r"whiskey", r"mezcal", r"tequila", r"bagels?", r"pancakes?", r"donuts?", r"ice cream",
        r"dessert", r"cafe", r"caf[ée]", r"eats?", r"foodies?", r"restaurant", r"aperitivos?", r"aperitif",
        r"distiller(?:y|ies)", r"supper"),
        _words(r"snacks?", r"drinks", r"bites", r"happy hour", r"bar")),
    Category("games", "Games & play", "🎲", "Game nights, trivia, puzzles and poker", _words(
        r"game nights?", r"board ?games?", r"poker", r"chess", r"trivia", r"quiz(?:zes)?", r"puzzles?", r"puzzle hunt",
        r"mahjong", r"bingo", r"d&d", r"dungeons", r"escape rooms?", r"video games?", r"e-?sports", r"gaming", r"catan",
        r"werewolf", r"mafia", r"tabletop", r"card games?", r"scavenger hunt", r"arcade", r"ping pong", r"bowling",
        r"mini golf", r"games"),
        _words(r"play", r"game", r"tournaments?")),
)

TOPICS: tuple[Category, ...] = (
    Category("ai", "AI", "🤖", "AI, LLMs, agents and machine learning", _words(
        r"ai", r"a\.i\.", r"agi", r"llms?", r"gpt(?:-?\d)?", r"gen ?ai", r"generative", r"agents?", r"agentic",
        r"machine learning", r"ml", r"deep learning", r"neural", r"claude", r"anthropic", r"openai", r"chatgpt", r"gemini",
        r"llama", r"mistral", r"hugging ?face", r"diffusion", r"rag", r"nlp", r"computer vision", r"multimodal",
        r"foundation models?", r"alignment", r"inference", r"fine-?tun(?:e|ing)", r"embeddings?", r"mcp", r"vibe ?cod(?:e|ing)",
        r"copilot", r"grok", r"xai", r"deepmind", r"gpus?", r"transformers?", r"evals?", r"reinforcement learning",
        r"rlhf", r"prompt(?:ing| engineering)?", r"cursor", r"perplexity", r"superintelligence", r"reasoning models?",
        r"artificial intelligence", r"data science", r"langchain", r"llamaindex", r"vector (?:db|database|search)", r"spacexai",
        r"inteligencia artificial", r"neurosymbolic"),
        _words(r"models?", r"intelligence", r"automation", r"autonomous")),
    Category("crypto", "Crypto", "⛓️", "Crypto, web3 and blockchains", _words(
        r"crypto(?:currency|currencies)?", r"web3", r"blockchains?", r"ethereum", r"eth", r"eth\w*conf", r"ethglobal",
        r"eth(?:sf|denver|cc|berlin)", r"bitcoin", r"btc", r"solana", r"defi", r"nfts?", r"daos?", r"on-?chain",
        r"zk", r"zero[- ]knowledge", r"stablecoins?", r"polygon", r"cosmos", r"l2s?", r"rollups?", r"base chain",
        r"tokeni[sz]ation", r"dapps?", r"smart contracts?", r"memecoins?", r"farcaster", r"lens protocol", r"bnb",
        r"avalanche", r"starknet", r"arbitrum", r"sui", r"hyperliquid", r"digital assets?", r"rwas?", r"aptos", r"near protocol", r"celestia", r"eigenlayer"),
        _words(r"tokens?", r"wallets?", r"decentrali[sz]ed", r"optimism")),
    Category("startups", "Startups & VC", "💼", "Founders, fundraising, VCs and go-to-market", _words(
        r"start-?ups?", r"founders?", r"co-?founders?", r"vcs?", r"venture(?: capital)?", r"investors?", r"investing",
        r"fundrais(?:e|ing)", r"seed (?:round|stage|funding)", r"series a", r"angel invest(?:ing|ors?)", r"yc", r"y combinator",
        r"entrepreneurs?(?:hip)?", r"gtm", r"go-to-market", r"saas", r"b2b", r"product-market fit", r"pmf", r"unicorns?",
        r"accelerator", r"incubator", r"solo ?founders?", r"ceos?", r"operators?", r"sales", r"marketing",
        r"fundraise", r"term sheets?", r"pre-?seed", r"cap table", r"bootstrapp(?:ed|ing)", r"indie hackers?", r"a16z",
        r"sequoia", r"techstars", r"500 global", r"on deck", r"south park commons", r"hf0", r"aum", r"lps?",
        r"\$\d[\d.,]*\s?(?:[kmb]|bn|million|billion)"),
        _words(r"seed", r"angels?", r"growth", r"business", r"company", r"companies", r"tech week", r"#sftechweek", r"sf tech week", r"leaders(?:hip)?")),
    Category("bio", "Bio & health", "🧬", "Biotech, health, medicine and neuroscience", _words(
        r"bio(?:tech|technology|logy|hacker|hackers|punk|hacking|security|engineering)?", r"health(?:care|tech)?",
        r"medic(?:al|ine)", r"longevity", r"neuro(?:science|tech)?", r"genomics?", r"pharma(?:ceutical)?", r"drug discovery",
        r"clinical", r"physicians?", r"doctors?", r"patients?", r"proteins?", r"crispr", r"synbio", r"wet lab", r"hospitals?",
        r"life sciences", r"medtech", r"nutrition", r"aging", r"cancer", r"therapeutics"),
        _words(r"lab", r"brain")),
    Category("climate", "Climate & energy", "🌱", "Climate tech, energy and sustainability", _words(
        r"climate(?: ?tech)?", r"energy", r"sustainab(?:le|ility)", r"carbon", r"solar", r"evs?", r"electric vehicles?",
        r"nuclear", r"fusion", r"clean ?tech", r"grid", r"batter(?:y|ies)", r"environment(?:al)?", r"agri(?:culture|tech)",
        r"food systems", r"decarboni[sz]ation", r"circular economy", r"geothermal", r"hydrogen", r"wind power", r"recycl(?:e|ing)"),
        _words(r"green", r"nature", r"earth", r"ocean")),
    Category("hardware", "Hardware & robotics", "🦾", "Robotics, hardware, chips, space and deep tech", _words(
        r"hardware", r"robot(?:s|ics)?", r"drones?", r"iot", r"chips?", r"semiconductors?", r"3d print(?:ing|ers?)?",
        r"electronics", r"embedded", r"fpga", r"deep ?tech", r"aerospace", r"space ?tech", r"manufactur(?:ing|e)",
        r"physical ai", r"humanoids?", r"self-?driving", r"lidar", r"arduino", r"raspberry pi", r"defen[cs]e tech",
        r"rockets?", r"satellites?", r"autonomous vehicles?", r"mechatronics", r"cad", r"cyberdecks?", r"leds?", r"solder(?:ing)?",
        r"circuits?", r"pcbs?", r"microcontrollers?"),
        _words(r"makers?", r"autonomous", r"space", r"devices?")),
    Category("design", "Design & product", "✏️", "Design, UX and product management", _words(
        r"design(?:ers?|ing)?", r"ux", r"ui/ux", r"figma", r"product (?:management|managers?|design|people|leaders?|folks|school)",
        r"pms", r"brand(?:ing)?", r"typography", r"creative direction", r"user research", r"framer", r"webflow", r"interaction design"),
        _words(r"product", r"craft", r"aesthetics?")),
    Category("dev", "Engineering", "💻", "Software, open source, infra and developer tools", _words(
        r"developers?", r"devs", r"devrel", r"dev ?tools?", r"devops", r"engineer(?:s|ing)?", r"programm(?:ing|ers?)", r"cod(?:e|ing)",
        r"software", r"open[- ]source", r"github", r"python", r"rust", r"javascript", r"typescript", r"react", r"golang",
        r"kubernetes", r"cloud", r"aws", r"gcp", r"azure", r"infra(?:structure)?", r"databases?", r"data engineering",
        r"(?:cyber)?security", r"apis?", r"sdks?", r"full-?stack", r"backend", r"frontend", r"postgres", r"sql", r"docker",
        r"linux", r"web dev", r"hackers?", r"compilers?", r"distributed systems", r"observability", r"sre", r"ios",
        r"android", r"next\.?js", r"vercel", r"supabase", r"webgpu", r"wasm", r"game ?dev(?:elopment|elopers?)?", r"eng",
        r"eng(?:ineering)? leaders?", r"command line", r"cli"),
        _words(r"tech", r"technical", r"builders?", r"stack", r"tools?")),
    Category("science", "Science", "🔭", "Physics, math, space, research and big ideas", _words(
        r"science", r"physics", r"math(?:s|ematics)?", r"astronomy", r"astrophysics", r"chemistry", r"quantum", r"dinosaurs?",
        r"evolution", r"universe", r"cosmology", r"philosophy", r"history", r"papers?", r"research(?:ers?)?", r"scientists?",
        r"academi[ac]", r"big ideas", r"telescope", r"planets?", r"archaeology", r"anthropology", r"psychology", r"linguistics", r"interstellar", r"proxima centauri", r"exoplanets?", r"cogsci"),
        _words(r"ideas", r"theory", r"thinkers?", r"mars", r"moon", r"lectures?")),
    Category("creators", "Creators & media", "🎥", "Content, podcasts, writing and media", _words(
        r"creators?", r"content(?: creation| creators?)?", r"influencers?", r"youtube(?:rs)?", r"tiktok", r"podcast(?:s|ing|ers)?",
        r"media", r"journalis(?:m|ts?)", r"newsletters?", r"substack", r"viral", r"storytelling", r"filmmak(?:ers?|ing)",
        r"video(?:graphy)?", r"streamers?", r"audience", r"creator economy", r"ugc", r"social media", r"writers?", r"writing"),
        _words(r"stories", r"brand", r"views")),
    Category("society", "Policy & society", "🏛️", "Policy, governance, ethics, safety and the future of work", _words(
        r"polic(?:y|ies)", r"governance", r"politic(?:s|al)", r"government", r"civic", r"democracy", r"ethics", r"ai safety",
        r"safe ai", r"regulat(?:ion|ory)", r"law", r"legal", r"economics?", r"society", r"future of work", r"education",
        r"public sector", r"geopolitics", r"national security", r"elections?", r"housing", r"urbanism", r"cities",
        r"nonprofits?", r"philanthropy", r"social impact", r"inclusion", r"diversity", r"women in",
        r"immigration", r"(?:u\.?s\.?|china|us-china|foreign|international) relations", r"foreign policy"),
        _words(r"impact", r"equity", r"safety", r"public", r"community organi[sz]ing", r"justice")),
)

VIBE_IDS = [c.id for c in VIBES]
TOPIC_IDS = [c.id for c in TOPICS]

SIZES = (("intimate", "Intimate", "Under 40 going", 0, 40), ("medium", "Mid-size", "40 to 150 going", 40, 150),
         ("big", "Big", "150+ going", 150, None))


def event_fields(ev: dict, calendar_names: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    presenter = ev.get("presenter") or {}
    calendar_names = " · ".join(n for n in calendar_names if n)
    return {
        "title": ev.get("name") or "",
        "tags": " · ".join(ev.get("tags") or []),
        "presenter": " · ".join(x for x in (presenter.get("name"), calendar_names) if x),
        "description": presenter.get("description") or "",
        "hosts": " · ".join(h.get("name") or "" for h in ev.get("hosts") or []),
    }


# On equal scores the more specific vibe leads, so a run club reads as "Get moving" before "Meet people".
SPECIFICITY = {cid: rank for rank, cid in enumerate(
    ("active", "games", "wellness", "party", "culture", "showcase", "build", "food", "learn", "social"))}


def _pick(categories: tuple[Category, ...], fields: dict[str, str]) -> list[str]:
    scored = [(c.score(fields), SPECIFICITY.get(c.id, index), c.id) for index, c in enumerate(categories)]
    chosen = sorted((s for s in scored if s[0] >= THRESHOLD), key=lambda s: (-s[0], s[1]))
    return [cid for _, _, cid in chosen[:MAX_PER_KIND]]


# A talk-shaped title asks a question, names a speaker ("with Jane Doe", "by Jane Doe"), uses "w/ …" or reads
# "Topic: subtitle".
TALK_HINTS = re.compile(r"\?(?:\s|$)|\bwith (?:dr|prof|professor)\b\.?|(?:^|\s)w/\s|\bfeat(?:uring)?\.?\s|\bft\.\s|\w: \w", re.I)
SPEAKER = re.compile(r"\b(?:with|by) (?:[A-Z][\w.'-]+ ){1,2}[A-Z][\w'-]+")
# Room holds and private bookings that calendars publish but nobody can attend.
PLACEHOLDER = re.compile(r"^\s*(?:hold|blocked|test)\s*[-–—:|]|[\[(]hold[\])]|\bplaceholder\b|\bprivate (?:rental|booking)\b"
                         r"|^\s*(?:private event|busy|tba|tbd|hold|blocked|test)\s*$", re.I)
# Conference names such as "AGNTCon" or "DevConf".
CONFERENCE = re.compile(r"\b[A-Z][A-Za-z0-9]*Conf?\b")


def is_placeholder(ev: dict) -> bool:
    return bool(PLACEHOLDER.search(ev.get("name") or ""))


def _pick_vibes(fields: dict[str, str]) -> list[str]:
    vibes = _pick(VIBES, fields)
    if vibes:
        return vibes
    # With no strong signal, the single best weak one wins, and talk-shaped titles count toward "learn".
    talk = bool(TALK_HINTS.search(fields["title"]) or SPEAKER.search(fields["title"]) or CONFERENCE.search(fields["title"]))
    scored = [(c.score(fields) + (FIELD_WEIGHTS["title"] / 2 if c.id == "learn" and talk else 0),
               -SPECIFICITY.get(c.id, 99), c.id) for c in VIBES]
    best = max(scored)
    return [best[2]] if best[0] >= FIELD_WEIGHTS["title"] / 2 else []


def classify(ev: dict, calendar_names: list[str] | tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """Return (vibes, topics) for an event. ``calendar_names`` are the calendars that listed it."""
    fields = event_fields(ev, calendar_names)
    return _pick_vibes(fields), _pick(TOPICS, fields)


def size_of(guest_count: object) -> str | None:
    if not isinstance(guest_count, int) or guest_count <= 0:
        return None
    for sid, _, _, low, high in SIZES:
        if guest_count >= low and (high is None or guest_count < high):
            return sid
    return None


def taxonomy() -> dict:
    return {
        "version": RULES_VERSION,
        "vibes": [c.to_dict() for c in VIBES],
        "topics": [c.to_dict() for c in TOPICS],
        "sizes": [{"id": sid, "label": label, "hint": hint} for sid, label, hint, _, _ in SIZES],
    }


if __name__ == "__main__":  # coverage report for tuning the rules against real data
    import collections
    import sys

    from .catalog import Catalog
    from .db import Database
    from .store import Store

    store = Store(Database(sys.argv[1] if len(sys.argv) > 1 else "data/events.db"))
    events = Catalog(store).build()["events"]
    vibe_counts = collections.Counter(v for e in events for v in e["vibes"])
    topic_counts = collections.Counter(t for e in events for t in e["topics"])
    print(f"{len(events)} events")
    print("no vibe:", sum(1 for e in events if not e["vibes"]), "· no topic:", sum(1 for e in events if not e["topics"]))
    print("vibes:", dict(vibe_counts.most_common()))
    print("topics:", dict(topic_counts.most_common()))
    mode = sys.argv[2] if len(sys.argv) > 2 else "vibes"
    for e in events:
        if not e[mode]:
            print("  ?", e["name"][:90], "|", (e.get("presenter") or {}).get("name"))
