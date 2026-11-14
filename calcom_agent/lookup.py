"""Match structured title terms against API-returned bookings, never user prose."""
from difflib import SequenceMatcher
import re


def title_score(query, title):
    terms=re.findall(r'\w+',query.casefold())
    words=re.findall(r'\w+',title.casefold())
    if not terms: return 1.0
    if not words: return 0.0
    scores=[max(SequenceMatcher(None,term,word).ratio() for word in words) for term in terms]
    return sum(scores)/len(scores) if min(scores)>=0.75 else 0.0
