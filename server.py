#!/usr/bin/env python3
"""GTM Signal MVP — dependency-free local server."""
import html
import ipaddress
import json
import logging
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGGER = logging.getLogger("gtm_signal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_local_env():
    """Load local buildathon credentials without adding a dotenv dependency."""
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except FileNotFoundError:
        pass


load_local_env()


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def meta_content(page, key):
    """Read a meta value regardless of attribute order."""
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        name = re.search(r"(?:name|property)=[\"']" + re.escape(key) + r"[\"']", tag, re.I)
        content = re.search(r"content=[\"']([^\"']+)", tag, re.I)
        if name and content:
            return clean_text(content.group(1))
    return ""


def company_name(page, title, source_url):
    for blob in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", page, re.I | re.S):
        try:
            objects = json.loads(blob)
            objects = objects if isinstance(objects, list) else objects.get("@graph", [objects])
            for item in objects:
                if item.get("@type") in ("Organization", "Corporation", "LocalBusiness") and item.get("name"):
                    return clean_text(item["name"])
        except (json.JSONDecodeError, AttributeError):
            pass
    for key in ("og:site_name", "application-name"):
        if value := meta_content(page, key):
            return value
    label = re.search(r"(?:aria-label|alt)=[\"']([^\"']+(?:logo|wordmark))[\"']", page, re.I)
    if label:
        return re.sub(r"\s+(?:logo|wordmark)$", "", clean_text(label.group(1)), flags=re.I)
    # Page titles frequently append a slogan after a colon, pipe, or dash.
    normalized = re.split(r"\s*(?::|\||–|—| - )\s*", title, maxsplit=1)[0].strip()
    return normalized or urllib.parse.urlparse(source_url).netloc.replace("www.", "")


def code_like(text):
    return re.search(r"(?:\b(?:window|document|location)\.|=>|function\s*\(|\\u[0-9a-f]{4}|[{};]{2,})", text, re.I)


def natural_language_blocks(page):
    """Keep visible prose blocks; never hand raw HTML, script, or config to reasoning."""
    page = re.sub(r"<!--.*?-->|<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>", " ", page, flags=re.I | re.S)
    page = re.sub(r"<(nav|header|footer|form|dialog)[^>]*>.*?</\1>", " ", page, flags=re.I | re.S)
    page = re.sub(r"</?(?:p|h[1-6]|li|article|section|div|main|blockquote)[^>]*>", "\n", page, flags=re.I)
    blocks = []
    for raw in page.splitlines():
        text = clean_text(raw)
        if 35 <= len(text) <= 420 and not code_like(text) and re.search(r"[A-Za-z]{3}", text):
            blocks.append(text)
    return blocks


def best_evidence(blocks, pattern):
    candidates = []
    for block in blocks:
        if code_like(block) or re.search(r"\b(cookie settings|accept all cookies|privacy choices|sign up|log in)\b", block, re.I):
            continue
        if not re.search(pattern, block, re.I):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", block):
            sentence = sentence.strip()
            if not re.search(pattern, sentence, re.I) or len(sentence) < 35 or len(sentence) > 300:
                continue
            if re.search(r"\b(fantastic|great sign|impressive|means existing customers|not only staying)\b", sentence, re.I):
                continue
            if not re.match(r"[A-Z0-9\"']", sentence):
                continue
            # Marketing copy is often a standalone heading without terminal punctuation.
            if not re.search(r"[.!?]$", sentence):
                if not re.search(r"[A-Za-z0-9]$", sentence):
                    continue
                sentence += "."
            score = min(len(sentence), 220) + 80 * bool(re.search(r"\d|Fortune|million|thousand|%", sentence, re.I))
            candidates.append((score, sentence))
    return max(candidates, default=(0, ""))[1]


def evidence_quality(text, source_type):
    return min(len(text), 220) + 80 * bool(re.search(r"\d|Fortune|million|thousand|%|launch|announc", text, re.I)) + (100 if source_type == "official" else 0)


def tavily_candidates(company, company_url, patterns):
    """Run three bounded searches and return cleaned, category-specific public evidence."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    company_host = urllib.parse.urlparse(company_url).hostname.removeprefix("www.")
    queries = {
        "SECURITY": f"{company} security compliance hiring",
        "ENTERPRISE": f"{company} enterprise growth expansion",
        "PRODUCT": f"{company} product launch AI platform",
    }
    seen_urls, candidates = set(), []
    for signal_type, query in queries.items():
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps({"query": query, "search_depth": "basic", "max_results": 2,
                             "include_answer": False, "include_raw_content": False}).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                results = json.loads(response.read()).get("results", [])
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
        for result in results:
            url, content = result.get("url", ""), clean_text(result.get("content", ""))
            parsed = urllib.parse.urlparse(url)
            canonical_url = parsed._replace(query="", fragment="").geturl()
            if not parsed.scheme.startswith("http") or parsed.hostname in ("youtube.com", "www.youtube.com") or canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            source_type = "official" if parsed.hostname and (parsed.hostname.removeprefix("www.") == company_host or parsed.hostname.removeprefix("www.").endswith("." + company_host)) else "public"
            evidence = best_evidence([content], patterns[signal_type])
            if evidence:
                candidates.append({"type": signal_type, "source_url": canonical_url, "source_text": evidence,
                                   "source_type": source_type, "quality": evidence_quality(evidence, source_type)})
    return candidates


def valid_public_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a valid public http(s) company URL.")
    host = parsed.hostname
    if host in ("localhost",) or host.endswith(".local"):
        raise ValueError("Please use a public company URL.")
    try:
        for entry in socket.getaddrinfo(host, None):
            if ipaddress.ip_address(entry[4][0]).is_private or ipaddress.ip_address(entry[4][0]).is_loopback:
                raise ValueError("Please use a public company URL.")
    except socket.gaierror:
        raise ValueError("That company URL could not be resolved.")
    return parsed.geturl()


def fetch_company(url):
    request = urllib.request.Request(url, headers={"User-Agent": "GTM-Signal-MVP/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            if "text/html" not in response.headers.get("Content-Type", ""):
                raise ValueError("The URL did not return a public HTML page.")
            return response.url, response.read(900_000).decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise ValueError("We could not access that public company page. Try its homepage.") from exc


def research(url):
    source_url, page = fetch_company(valid_public_url(url))
    title = clean_text((re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S) or ["", ""])[1])
    description = meta_content(page, "og:description") or meta_content(page, "description")
    name = company_name(page, title, source_url)
    blocks = natural_language_blocks(page)
    patterns = {
        "SECURITY": r"\b(security|SOC ?2|ISO ?27001|compliance|privacy|zero trust)\b",
        "ENTERPRISE": r"\b(enterprise|Fortune \d+|global teams?|at scale|customers?)\b",
        "PRODUCT": r"\b(platform|integrat(?:e|ion)|automation|API|AI-powered|connectivity cloud|product launch|announc(?:e|ed|ement))\b",
    }
    labels = {"SECURITY": "Security or compliance signal", "ENTERPRISE": "Enterprise-scale signal", "PRODUCT": "Product or technology signal"}
    candidates = tavily_candidates(name, source_url, patterns)
    for signal_type, pattern in patterns.items():
        if evidence := best_evidence(blocks, pattern):
            candidates.append({"type": signal_type, "source_url": source_url, "source_text": evidence,
                               "source_type": "official", "quality": evidence_quality(evidence, "official")})
    signals = []
    for signal_type in patterns:
        options = [item for item in candidates if item["type"] == signal_type]
        if options:
            best = max(options, key=lambda item: item["quality"])
            signals.append({"id": signal_type.lower(), "type": signal_type, "title": labels[signal_type],
                            "description": best["source_text"], "source_url": best["source_url"],
                            "source_text": best["source_text"], "source_type": best["source_type"]})
    return {"company": {"name": name, "industry": "Not established from the available page", "description": description or title or "No public description found.", "location": "Not established from the available page", "size_signal": "Not established from the available page"}, "signals": signals}


def fallback_result(data, base):
    signals = base["signals"]
    evidence = [{"signal_id": item["id"], "signal_type": item["type"], "source_url": item["source_url"], "source_text": item["source_text"], "source_type": item["source_type"]} for item in signals]
    types = ", ".join(item["title"].lower() for item in signals[:2]) or "the available public company context"
    persona = data["target_persona"]
    product = data["our_product"]
    hypothesis = f"Possible prioritization pressure for {persona}"
    return {**base, "evidence": evidence,
      "pain_hypothesis": {"title": hypothesis, "description": f"This is an inference, not a verified fact: the observed {types} may create a need to standardize or accelerate work owned by {persona}.", "reasoning": "The hypothesis is based only on the cited website evidence. No internal process or pain point was verified.", "confidence": 38 if len(signals) < 2 else 52},
      "recommended_angle": f"Lead with how {product} helps {persona} act on the visible company priorities, then validate the operational challenge rather than assuming it.",
      "outreach": {"email": f"Subject: A question on {base['company']['name']}\n\nHi {{first_name}},\n\nI saw {base['company']['name']}'s public {signals[0]['type'].lower() if signals else 'company'} signal. For a {persona}, that may be worth exploring as priorities evolve.\n\n{product} is built for teams working on related outcomes. Is this an active area for your team?\n\nBest,\n{{sender_name}}", "linkedin": f"Hi {{first_name}} — I noticed {base['company']['name']}'s public {signals[0]['type'].lower() if signals else 'company'} signal. We help {persona} teams with {product}. Is this a priority worth comparing notes on?"},
      "roi_hook": {"statement": "Illustrative planning estimate: if an engineering or security organization recovered 2 hours per person per month through automated compliance evidence collection, the recovered capacity could be approximately 24 hours per person per year.", "assumptions": ["Illustrative 2 hours saved per person per month.", "Actual employee count was not established from public evidence.", "Current manual compliance effort was not established from public evidence."]}}


def output_text(value, keys=()):
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return ""


def is_message(value):
    return (len(value) >= 60 and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)
            and bool(re.search(r"\b(hi|hello|dear|subject:)\b", value, re.I)))


def normalize_model_output(derived, data, base):
    """Keep the UI contract stable when a free model returns loosely typed JSON."""
    fallback = fallback_result(data, base)
    if not isinstance(derived, dict):
        return {key: fallback[key] for key in ("pain_hypothesis", "recommended_angle", "outreach", "roi_hook")}
    pain = derived.get("pain_hypothesis") if isinstance(derived.get("pain_hypothesis"), dict) else {}
    confidence = pain.get("confidence")
    if isinstance(confidence, bool):
        confidence = None
    elif isinstance(confidence, str):
        try:
            confidence = float(confidence.strip())
        except ValueError:
            confidence = None
    if not isinstance(confidence, (int, float)):
        confidence = fallback["pain_hypothesis"]["confidence"]
    confidence = max(0, min(100, confidence))
    confidence = int(confidence) if float(confidence).is_integer() else float(confidence)
    angle = output_text(derived.get("recommended_angle"), ("angle", "message", "text", "description", "title")) or fallback["recommended_angle"]
    outreach = derived.get("outreach") if isinstance(derived.get("outreach"), dict) else {}
    email = output_text(outreach.get("email"))
    linkedin = output_text(outreach.get("linkedin"))
    return {
        "pain_hypothesis": {"title": output_text(pain.get("title")) or fallback["pain_hypothesis"]["title"], "description": output_text(pain.get("description")) or fallback["pain_hypothesis"]["description"], "reasoning": output_text(pain.get("reasoning")) or fallback["pain_hypothesis"]["reasoning"], "confidence": confidence},
        "recommended_angle": angle,
        "outreach": {"email": email if is_message(email) else fallback["outreach"]["email"], "linkedin": linkedin if is_message(linkedin) else fallback["outreach"]["linkedin"]},
        "roi_hook": fallback["roi_hook"],
    }


def llm_reasoning(data, base):
    """Use OpenRouter JSON mode; the no-key fallback stays evidence-only."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return fallback_result(data, base)
    prompt = ("You are an evidence-first B2B account researcher. The supplied evidence has been cleaned from public pages; use only it. "
              "Separate cited facts from inference. Do not invent internal company problems or infer a specific operational burden unless the evidence supports it. "
              "When evidence is weak, produce a cautious discovery hypothesis. Keep hypotheses explicitly labeled as inference. "
              "Generate outreach from the structured evidence and hypothesis, not by copying source text verbatim; it must be grammatical and appropriately tentative. "
              "ROI must be visibly an estimate and include assumptions.\n\nINPUT:\n" + json.dumps({"brief": data, "company": base["company"], "signals": base["signals"]}))
    contract = ("Return only valid JSON with exactly these top-level keys: pain_hypothesis, recommended_angle, outreach, roi_hook. "
                "pain_hypothesis must include title, description, reasoning, confidence; outreach must include email, linkedin; "
                "roi_hook must include statement, assumptions. confidence must be a number from 0 to 100; recommended_angle must be plain text; assumptions must be an array of strings. "
                "email and linkedin must be actual messages, never contact details, lists, or audience descriptions. Do not use markdown or extra keys. Keep the response concise.")
    last_error = None
    for model in ("google/gemma-4-26b-a4b-it:free", "nvidia/nemotron-nano-9b-v2:free"):
        payload = {"model": model, "max_tokens": 1200, "messages": [{"role": "system", "content": contract}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
        request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "HTTP-Referer": "http://localhost:8000", "X-Title": "GTM Signal"}, method="POST")
        try:
            LOGGER.info("OpenRouter request started endpoint=%s model=%s", request.full_url, model)
            with urllib.request.urlopen(request, timeout=60) as response:
                raw_response = response.read().decode("utf-8", "replace")
                LOGGER.info("OpenRouter response status=%s body=%s", response.status, raw_response.lstrip()[:700])
                content = json.loads(raw_response)["choices"][0]["message"].get("content")
                if not content:
                    raise KeyError("OpenRouter response contained no message content")
                derived = json.loads(content)
            derived = normalize_model_output(derived, data, base)
            evidence = [{"signal_id": item["id"], "signal_type": item["type"], "source_url": item["source_url"], "source_text": item["source_text"], "source_type": item["source_type"]} for item in base["signals"]]
            return {**base, "evidence": evidence, **derived}
        except urllib.error.HTTPError as exc:
            last_error = exc
            LOGGER.error("OpenRouter HTTP status=%s model=%s body=%s", exc.code, model, exc.read().decode("utf-8", "replace")[:700])
        except (urllib.error.URLError, KeyError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            LOGGER.exception("OpenRouter reasoning exception model=%s type=%s message=%s", model, type(exc).__name__, str(exc))
    raise ValueError("OpenRouter reasoning failed. Please try again.") from last_error


class App(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_POST(self):
        if self.path != "/api/analyze-account":
            self.send_error(404); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            for field in ("company_url", "target_persona", "our_product", "icp"):
                if not isinstance(data.get(field), str) or not data[field].strip():
                    raise ValueError(f"{field.replace('_', ' ').title()} is required.")
            result = llm_reasoning(data, research(data["company_url"]))
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_response(400); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode())


if __name__ == "__main__":
    print("GTM Signal running at http://localhost:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), App).serve_forever()
