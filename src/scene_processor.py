"""
Scene Processor – Splits script into logical scenes, extracts keywords,
and NOW generates thematic visual search queries to avoid irrelevant nature clips.
"""

import re
import logging
from typing import List, Tuple

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

THEME_MAP = {
    "trump": ["president", "american politics", "white house speech"],
    "president": ["political leader", "election debate"],
    "pentagon": ["military headquarters", "confidential meeting", "security briefing"],
    "classified": ["top secret documents", "intelligence files"],
    "gambling": ["casino chips", "roulette table", "betting money"],
    "betting": ["sports betting", "gambling addiction", "casino"],
    "nba": ["basketball court", "sports news", "press conference"],
    "coach": ["sports coach", "locker room"],
    "arrested": ["police handcuffs", "prison", "crime scene"],
    "insider trading": ["stock market graph", "money corruption", "fraud"],
    "money": ["cash stacks", "finance danger", "dollar bills"],
    "danger": ["warning sign", "red alert", "emergency"],
    "politics": ["political debate", "capitol building", "news broadcast"],
    "regulatory framework": ["government building", "legal documents", "courtroom"],
    "investigation": ["detective", "crime evidence", "police lights"],
    "addiction": ["person in dark room", "struggle", "depression"],
    "jim": [],  # skip name
    "madura": [],  # specific entity, may fall back to generic politics
    "will trump": ["president trump", "political rally"],
    "trump photographed": ["president photo op", "press conference"],
    "pentagon activities": ["military operation", "situation room"],
    "classified information": ["secret files", "intelligence leak"],
    "pete rose": ["baseball", "sports betting scandal"],
}

def _get_thematic_queries(keywords: List[str]) -> List[str]:
    """Generate visual search phrases based on extracted keywords."""
    thematic = []
    for word in keywords:
        word_lower = word.lower()
        if word_lower in THEME_MAP:
            thematic.extend(THEME_MAP[word_lower])
    # Remove duplicates, keep order
    seen = set()
    result = []
    for t in thematic:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


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
            keyword_candidates.append({"text": ent.text.strip().lower(), "priority": 10})

    for token in doc:
        if token.is_stop or token.is_punct or token.is_space:
            continue
        if token.pos_ in {"PROPN"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 8})
        elif token.pos_ in {"NOUN"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 5})
        elif token.pos_ in {"ADJ"}:
            keyword_candidates.append({"text": token.text.strip().lower(), "priority": 3})

    keyword_map = {}
    for item in keyword_candidates:
        w = item["text"]
        if w not in keyword_map:
            keyword_map[w] = {"text": w, "priority": item["priority"], "count": 1}
        else:
            keyword_map[w]["priority"] = max(keyword_map[w]["priority"], item["priority"])
            keyword_map[w]["count"] += 1

    sorted_keywords = sorted(keyword_map.values(), key=lambda x: (x["priority"], x["count"]), reverse=True)
    keywords = [kw["text"] for kw in sorted_keywords[:max_keywords]]

    # Generate thematic visual queries from these keywords
    visual_queries = _get_thematic_queries(keywords)

    return keywords, visual_queries
