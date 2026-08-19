import json
import os
import time
from difflib import SequenceMatcher

import requests


ORCID_ID = "0000-0002-7255-2790"

CLIENT_ID = os.environ.get("ORCID_CLIENT_ID")
CLIENT_SECRET = os.environ.get("ORCID_CLIENT_SECRET")

OUTPUT_FILE = "publications.json"

ORCID_API = "https://pub.orcid.org/v3.0"
ORCID_TOKEN_URL = "https://orcid.org/oauth/token"

MIN_CROSSREF_SCORE = 0.85

print("=== ORCID publication updater started ===", flush=True)


def normalize(text):
    return " ".join((text or "").lower().strip().split())


def similarity(a, b):
    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


def load_existing_publications():
    if not os.path.exists(OUTPUT_FILE):
        return []

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load existing JSON: {e}", flush=True)
        return []


def existing_lookup(publications):
    lookup = {}

    for pub in publications:
        doi = pub.get("doi", "")
        title = pub.get("title", "")

        if doi:
            lookup["doi:" + normalize(doi)] = pub

        if title:
            lookup["title:" + normalize(title)] = pub

    return lookup


def get_access_token():
    print("Getting ORCID access token...", flush=True)

    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "ORCID_CLIENT_ID or ORCID_CLIENT_SECRET is missing."
        )

    response = requests.post(
        ORCID_TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "/read-public"
        },
        headers={
            "Accept": "application/json"
        },
        timeout=30
    )

    print(
        f"ORCID token response: {response.status_code}",
        flush=True
    )

    response.raise_for_status()

    data = response.json()

    token = data.get("access_token")

    if not token:
        raise RuntimeError(
            "ORCID did not return an access token."
        )

    print("ORCID access token received.", flush=True)

    return token


def get_work_groups(token):
    print("Fetching ORCID works...", flush=True)

    response = requests.get(
        f"{ORCID_API}/{ORCID_ID}/works",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        },
        timeout=30
    )

    print(
        f"ORCID works response: {response.status_code}",
        flush=True
    )

    response.raise_for_status()

    data = response.json()
    groups = data.get("group", [])

    print(
        f"ORCID returned {len(groups)} work groups.",
        flush=True
    )

    return groups


def extract_year(summary):
    publication_date = summary.get("publication-date")

    if not publication_date:
        return None

    year_obj = publication_date.get("year")

    if not year_obj:
        return None

    try:
        return int(year_obj.get("value"))
    except Exception:
        return None


def extract_external_ids(summary):
    ids = {}

    external_ids = summary.get("external-ids", {})

    for item in external_ids.get("external-id", []):
        id_type = item.get("external-id-type", "").lower()
        value = item.get("external-id-value", "")

        url_obj = item.get("external-id-url")
        url = ""

        if url_obj:
            url = url_obj.get("value", "")

        if id_type and value:
            ids[id_type] = {
                "value": value,
                "url": url
            }

    return ids


def parse_orcid_works(groups):
    publications = []

    for group in groups:
        summaries = group.get("work-summary", [])

        if not summaries:
            continue

        summary = summaries[0]

        title_obj = summary.get("title", {})
        title = (
            title_obj
            .get("title", {})
            .get("value", "")
        )

        if not title:
            continue

        year = extract_year(summary)

        journal_obj = summary.get("journal-title")
        journal = ""

        if journal_obj:
            journal = journal_obj.get("value", "")

        ids = extract_external_ids(summary)

        doi = ""

        if "doi" in ids:
            doi_value = ids["doi"]["value"]

            if doi_value.startswith("http"):
                doi = doi_value
            else:
                doi = "https://doi.org/" + doi_value

        url = doi

        if not url:
            for value in ids.values():
                if value.get("url"):
                    url = value["url"]
                    break

        publications.append({
            "title": title,
            "year": year,
            "journal": journal,
            "doi": doi,
            "url": url,
            "citations": 0
        })

    return publications


def preserve_previous(pub, previous):
    if not previous:
        return pub

    for field in [
        "authors",
        "journal",
        "doi"
    ]:
        if previous.get(field) and not pub.get(field):
            pub[field] = previous[field]

    return pub


def enrich_with_crossref(pub, previous=None):
    title = pub.get("title", "")

    if not title:
        return preserve_previous(pub, previous)

    params = {
        "query.bibliographic":
            f"{title} {pub.get('year', '')}",
        "rows": 5,
        "select":
            "DOI,title,author,container-title"
    }

    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params=params,
            headers={
                "User-Agent":
                    "AcademicPublicationsUpdater/1.0"
            },
            timeout=30
        )

        response.raise_for_status()

        items = (
            response.json()
            .get("message", {})
            .get("items", [])
        )

    except Exception as e:
        print(
            f"Crossref failed for '{title}': {e}",
            flush=True
        )
        return preserve_previous(pub, previous)

    best = None
    best_score = 0

    for item in items:
        titles = item.get("title", [])

        if not titles:
            continue

        score = similarity(
            title,
            titles[0]
        )

        if score > best_score:
            best_score = score
            best = item

    if not best or best_score < MIN_CROSSREF_SCORE:
        print(
            f"No confident Crossref match: {title}",
            flush=True
        )
        return preserve_previous(pub, previous)

    author_names = []

    for author in best.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")

        full_name = f"{given} {family}".strip()

        if full_name:
            author_names.append(full_name)

    if author_names:
        pub["authors"] = ", ".join(author_names)

    if not pub.get("journal"):
        journals = best.get("container-title", [])

        if journals:
            pub["journal"] = journals[0]

    if not pub.get("doi"):
        doi = best.get("DOI", "")

        if doi:
            if not doi.startswith("http"):
                doi = "https://doi.org/" + doi

            pub["doi"] = doi

    pub["crossref_match_score"] = round(
        best_score,
        3
    )

    return preserve_previous(pub, previous)


def main():
    token = get_access_token()

    groups = get_work_groups(token)

    publications = parse_orcid_works(groups)

    print(
        f"Parsed {len(publications)} publications.",
        flush=True
    )

    if len(publications) == 0:
        raise RuntimeError(
            "ORCID returned zero usable publications."
        )

    old_publications = load_existing_publications()
    old_lookup = existing_lookup(old_publications)

    enriched = []

    for i, pub in enumerate(
        publications,
        start=1
    ):
        print(
            f"[{i}/{len(publications)}] "
            f"{pub['title']}",
            flush=True
        )

        previous = None

        if pub.get("doi"):
            previous = old_lookup.get(
                "doi:" + normalize(pub["doi"])
            )

        if not previous:
            previous = old_lookup.get(
                "title:" + normalize(pub["title"])
            )

        enriched_pub = enrich_with_crossref(
            pub,
            previous
        )

        enriched.append(enriched_pub)

        time.sleep(0.2)

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            enriched,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        "=== UPDATE COMPLETED ===",
        flush=True
    )

    print(
        f"Saved {len(enriched)} publications.",
        flush=True
    )


if __name__ == "__main__":
    main()
