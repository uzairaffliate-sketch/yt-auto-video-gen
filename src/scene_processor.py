"""
Scene Processor – Splits script into logical scenes, extracts keywords,
and generates thematic visual search queries to avoid irrelevant nature clips.

Two layers of query generation:
1. THEME_MAP – curated phrase -> stock-friendly visual queries (best quality,
   covers common topics). Now matched with substring matching too, so
   "trump" matches inside "donald trump" etc.
2. Generic fallback – if NO theme matched at all for a scene, build queries
   from spaCy entity labels / POS tags (e.g. ORG -> "<org> building",
   PERSON -> "<person> portrait", GPE -> "<place> city skyline") plus a
   final safety-net generic "news broadcast b-roll" style query so that
   ANY topic (not just hardcoded ones) gets a relevant-ish search term
   instead of falling back to generic "mountain/waterfall" stock clips.
"""

import re
import logging
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)

_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.error("spaCy model 'en_core_web_sm' not found. Install with: python -m spacy download en_core_web_sm")
            raise
    return _nlp


# ---------- Visual Theme Mapping ----------
# Maps extracted keywords (or categories) to stock‑friendly visual search phrases.
# This ensures we get political/dark/money visuals instead of nature clips.
# Matching is done both exact AND as a substring of multi-word keywords,
# so e.g. "donald trump" still triggers the "trump" entry.

THEME_MAP = {
    # ---- Politics / government ----
    "trump": ["president", "american politics", "white house speech"],
    "biden": ["president", "american politics", "white house speech"],
    "president": ["political leader", "election debate"],
    "white house": ["white house", "press briefing room"],
    "congress": ["capitol building", "congress session"],
    "senate": ["senate hearing", "capitol building"],
    "election": ["election rally", "voting booth", "campaign event"],
    "politics": ["political debate", "capitol building", "news broadcast"],
    "political": ["political debate", "news broadcast"],
    "government": ["government building", "official meeting"],
    "regulatory framework": ["government building", "legal documents", "courtroom"],
    "policy": ["government building", "press conference"],

    # ---- Military / security ----
    "pentagon": ["military headquarters", "confidential meeting", "security briefing"],
    "military": ["military operation", "soldiers briefing"],
    "classified": ["top secret documents", "intelligence files"],
    "classified information": ["secret files", "intelligence leak"],
    "intelligence": ["intelligence agency", "secret files"],
    "pentagon activities": ["military operation", "situation room"],

    # ---- Gambling / betting ----
    "gambling": ["casino chips", "roulette table", "betting money"],
    "betting": ["sports betting", "gambling addiction", "casino"],
    "bet": ["sports betting", "casino chips"],
    "casino": ["casino floor", "slot machines", "poker table"],
    "poker": ["poker table", "casino chips"],

    # ---- Sports ----
    "nba": ["basketball court", "sports news", "press conference"],
    "basketball": ["basketball court", "sports arena"],
    "coach": ["sports coach", "locker room"],
    "pete rose": ["baseball", "sports betting scandal"],
    "baseball": ["baseball stadium", "baseball game"],

    # ---- Crime / law ----
    "arrested": ["police handcuffs", "prison", "crime scene"],
    "police": ["police lights", "police officer"],
    "prison": ["prison cell", "handcuffs"],
    "investigation": ["detective", "crime evidence", "police lights"],
    "fraud": ["money corruption", "fraud investigation"],
    "insider trading": ["stock market graph", "money corruption", "fraud"],
    "lawsuit": ["courtroom", "legal documents"],
    "court": ["courtroom", "judge gavel"],

    # ---- Finance ----
    "money": ["cash stacks", "finance danger", "dollar bills"],
    "stock market": ["stock market graph", "trading floor"],
    "economy": ["stock market graph", "financial district"],
    "bank": ["bank building", "finance office"],

    # ---- General negative / emotional themes ----
    "danger": ["warning sign", "red alert", "emergency"],
    "addiction": ["person in dark room", "struggle", "depression"],
    "crisis": ["breaking news", "emergency broadcast"],
    "scandal": ["press conference", "breaking news"],

    # ---- Misc / specific entities (kept narrow on purpose) ----
    "jim": [],  # skip name
    "madura": ["latin american politics", "presidential palace"],
    "will trump": ["president trump", "political rally"],
    "trump photographed": ["president photo op", "press conference"],
}

# Map spaCy entity labels to generic visual-query templates used when a
# specific keyword has NO entry in THEME_MAP. "{kw}" is replaced with the
# actual keyword text.
ENTITY_LABEL_QUERIES = {
    "PERSON": ["{kw} portrait", "person speaking podium"],
    "ORG": ["{kw} logo building", "corporate office building"],
    "GPE": ["{kw} city skyline", "{kw} aerial view"],
    "LOC": ["{kw} landscape", "{kw} aerial view"],
    "FAC": ["{kw} building exterior"],
    "EVENT": ["{kw} event", "news broadcast event"],
    "PRODUCT": ["{kw} product closeup"],
    "WORK_OF_ART": ["{kw} closeup"],
}

# Absolute last-resort queries if nothing else matched at all for a scene.
# These are generic "news / talk" b-roll style terms that almost every
# stock site has plenty of, so a scene never ends up with zero results.
GENERIC_FALLBACK_QUERIES = [
    "news broadcast studio",
    "breaking news background",
    "talk show discussion",
]

def _get_thematic_queries(keyword_entries: List[Dict]) -> List[str]:
    """
    Generate visual search phrases based on extracted keywords.

    keyword_entries: list of dicts like {"text": "trump", "label": "PERSON"}
        (label may be "" if it's just a POS-based keyword, not a named entity)

    Strategy:
    1. Exact match against THEME_MAP.
    2. Substring match: if a multi-word keyword CONTAINS a THEME_MAP key
       (e.g. "donald trump" contains "trump"), use that entry too.
    3. If NOTHING from THEME_MAP matched for this scene at all, fall back to
       entity-label-based generic queries (PERSON -> portrait, ORG ->
       building, GPE -> city skyline, etc.) for the top keywords.
    4. If still nothing, use GENERIC_FALLBACK_QUERIES so the scene is never
       left with zero visual queries.
    """
    thematic = []

    for entry in keyword_entries:
        word_lower = entry["text"].lower().strip()

        # 1. Exact match
        if word_lower in THEME_MAP:
            thematic.extend(THEME_MAP[word_lower])
            continue

        # 2. Substring match (either direction) against THEME_MAP keys
        matched = False
        for theme_key, theme_values in THEME_MAP.items():
            if not theme_key:
                continue
            if theme_key in word_lower or word_lower in theme_key:
                thematic.extend(theme_values)
                matched = True
        if matched:
            continue

    # Remove duplicates from THEME_MAP-derived queries, keep order
    seen = set()
    result = []
    for t in thematic:
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    if result:
        return result

    # 3. Nothing in THEME_MAP matched at all -> generic entity-based fallback
    entity_based = []
    for entry in keyword_entries[:5]:
        label = entry.get("label", "")
        kw = entry["text"]
        templates = ENTITY_LABEL_QUERIES.get(label)
        if templates:
            for tmpl in templates:
                entity_based.append(tmpl.format(kw=kw))

    seen = set()
    result = []
    for t in entity_based:
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    if result:
        return result

    # 4. Absolute last resort
    return list(GENERIC_FALLBACK_QUERIES)


def split_script(text: str, sentences_per_scene: int = 3) -> List[str]:
    """
    Break script into a list of scene texts.
    Logic (in order):
    1. Explicit scene markers like 'Scene 1:', 'SCENE 1:', '[SCENE]'.
    2. Blank lines (paragraphs).
    3. If still only 1 scene: use spaCy sentence tokenization and group sentences
       into scenes of <sentences_per_scene> each.
    """
    # Step 1 – Explicit markers
    scene_markers = re.split(r'(?i)^\s*(?:Scene\s*\d+|SCENE\s*\d+|\[SCENE\])\s*[:\-]?', text, flags=re.MULTILINE)
    if len(scene_markers) > 1:
        scenes = [s.strip() for s in scene_markers if s.strip()]
        logger.debug(f"Split by scene markers: {len(scenes)} scenes.")
        return scenes

    # Step 2 – Blank lines
    scenes = [s.strip() for s in re.split(r'\n\s*\n', text) if s.strip()]
    if len(scenes) > 1:
        logger.debug(f"Split by blank lines: {len(scenes)} scenes.")
        return scenes

    # Step 3 – Fallback: sentence-based splitting
    logger.info("No scene markers or blank lines found – using sentence splitting.")
    nlp = _get_nlp()
    doc = nlp(text)
    sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]

    if len(sentences) <= sentences_per_scene:
        return [text.strip()] if sentences else []

    new_scenes = []
    for i in range(0, len(sentences), sentences_per_scene):
        chunk = " ".join(sentences[i:i + sentences_per_scene])
        new_scenes.append(chunk)

    logger.info(f"Split by sentences into {len(new_scenes)} scenes (sentences_per_scene={sentences_per_scene}).")
    return new_scenes


def extract_keywords_and_visual_queries(scene_text: str, max_keywords: int = 10) -> Tuple[List[str], List[str]]:
    """
    Returns:
        keywords: list of extracted nouns/entities (for semantic matching)
        visual_queries: list of thematic stock‑friendly search phrases
    """
    nlp = _get_nlp()
    doc = nlp(scene_text)

    keyword_candidates = []

    for ent in doc.ents:
        if ent.label_ in {"PERSON", "GPE", "LOC", "ORG", "PRODUCT", "EVENT", "WORK_OF_ART", "FAC"}:
            keyword_candidates.append({"text": ent.text.strip().lower(), "priority": 10, "label": ent.label_})

    for token in doc:
        if token.is_stop or token.is_punct or token.is_space:
            continue
        if token.pos_ in {"PROPN"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 8, "label": ""})
        elif token.pos_ in {"NOUN"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 5, "label": ""})
        elif token.pos_ in {"ADJ"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 3, "label": ""})

    keyword_map = {}
    for item in keyword_candidates:
        w = item["text"]
        if w not in keyword_map:
            keyword_map[w] = {"text": w, "priority": item["priority"], "count": 1, "label": item["label"]}
        else:
            keyword_map[w]["priority"] = max(keyword_map[w]["priority"], item["priority"])
            keyword_map[w]["count"] += 1
            # Prefer keeping an entity label if one was found for this word
            if not keyword_map[w]["label"] and item["label"]:
                keyword_map[w]["label"] = item["label"]

    sorted_keywords = sorted(keyword_map.values(), key=lambda x: (x["priority"], x["count"]), reverse=True)
    top_entries = sorted_keywords[:max_keywords]
    keywords = [kw["text"] for kw in top_entries]

    # Generate thematic visual queries from these keywords (with entity-aware fallback)
    visual_queries = _get_thematic_queries(top_entries)

    return keywords, visual_queries
