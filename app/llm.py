"""Routeur de modèle par SENSIBILITÉ + abstraction multi-fournisseurs.

Souveraineté **par construction** : le fournisseur est choisi selon la sensibilité de la
requête, pas selon une clause contractuelle.
  - "public"       → questions sur le corpus public (jurisprudence + Legilux) ;
  - "confidentiel" → documents privés du Vault (données du cabinet).

Fournisseurs : "anthropic" (Claude), "mistral" (API UE souveraine), "local" (Ollama,
air-gap — aucun appel externe). Config par env (`app/config.py`). Dépendances minimales :
seul Anthropic a un SDK (déjà présent) ; Mistral et Ollama sont appelés en HTTP via
`urllib` (stdlib), comme la recherche hybride. Toute panne lève `RuntimeError` — l'appelant
(`rag`) la transforme en **refus gracieux**, jamais un 500.
"""
import json
import urllib.request

import anthropic

from .config import settings


def fournisseur(sensibilite: str) -> str:
    """Fournisseur retenu pour cette sensibilité ("public" par défaut)."""
    brut = (settings.llm_provider_confidential if sensibilite == "confidentiel"
            else settings.llm_provider_public) or "anthropic"
    f = brut.strip().lower()
    return f if f in ("anthropic", "mistral", "local") else "anthropic"


def modele(sensibilite: str) -> str:
    """Nom de modèle correspondant au fournisseur choisi."""
    return {"anthropic": settings.anthropic_model,
            "mistral": settings.mistral_model,
            "local": settings.local_model}[fournisseur(sensibilite)]


def info() -> dict:
    """Vue du routage (observabilité / backoffice)."""
    return {
        "public": {"fournisseur": fournisseur("public"), "modele": modele("public")},
        "confidentiel": {"fournisseur": fournisseur("confidentiel"), "modele": modele("confidentiel")},
    }


def generer(system_text: str, messages: list, temperature: float,
            sensibilite: str = "public") -> str:
    """Génère une complétion texte via le fournisseur choisi pour la sensibilité.

    `messages` = liste `{role, content}` (rôles user/assistant, contenu = str).
    `system_text` = prompt système brut. Renvoie le texte de la réponse.
    Lève `RuntimeError` en cas d'échec (l'appelant refuse gracieusement)."""
    f = fournisseur(sensibilite)
    try:
        if f == "mistral":
            return _mistral(system_text, messages, temperature)
        if f == "local":
            return _local(system_text, messages, temperature)
        return _anthropic(system_text, messages, temperature)
    except RuntimeError:
        raise
    except Exception as e:  # normalise toute panne fournisseur
        raise RuntimeError(f"LLM ({f}) indisponible : {type(e).__name__}") from e


# ---------- fournisseurs ----------
def _anthropic(system_text: str, messages: list, temperature: float) -> str:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        temperature=temperature,
        # préfixe système statique mis en cache (prompt caching) → TTFT + coût réduits
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def _mistral(system_text: str, messages: list, temperature: float) -> str:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY manquante (fournisseur mistral)")
    payload = {
        "model": settings.mistral_model,
        "temperature": temperature,
        "max_tokens": settings.anthropic_max_tokens,
        "messages": [{"role": "system", "content": system_text}] + messages,
    }
    data = _post_json("https://api.mistral.ai/v1/chat/completions", payload,
                      {"Authorization": "Bearer " + settings.mistral_api_key})
    return (data["choices"][0]["message"]["content"] or "")  # format OpenAI-compatible


def _local(system_text: str, messages: list, temperature: float) -> str:
    url = settings.ollama_url.rstrip("/") + "/api/chat"
    payload = {
        "model": settings.local_model,
        "stream": False,
        "options": {"temperature": temperature},
        "messages": [{"role": "system", "content": system_text}] + messages,
    }
    data = _post_json(url, payload, {})
    return (data.get("message") or {}).get("content") or ""


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())
