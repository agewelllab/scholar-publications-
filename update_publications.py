import json
import os
import time
from difflib import SequenceMatcher
from urllib.parse import quote

import requests


# ============================================================
# SETTINGS
# ============================================================

ORCID_ID = "0000-0002-7255-2790"

ORCID_CLIENT_ID = os.environ.get("ORCID_CLIENT_ID")
ORCID_CLIENT_SECRET = os.environ.get("ORCID_CLIENT_SECRET")

ORCID_API = "https://pub.orcid.org/v3.0"
ORCID_TOKEN_URL = "https://orcid.org/oauth/token"

OUTPUT_FILE = "publications.json"

# Put your real email here.
# Crossref recommends supplying a valid contact email.
CONTACT_EMAIL = "hello.agewelllab@gmail.com"

MIN_CROSSREF_SCORE = 0.85


print("=== ORCID publication updater started ===", flush=True)


# ============================================================
# TEXT MATCHING
# ============================================================

def normalize(text):
    return " ".join(
        (text or "")
        .lower()
        .strip()
        .split()
    )


def similarity(a, b):
    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


# ============================================================
# LOAD OLD JSON
# ============================================================

def load_existing_publications():

    if not os.path.exists(OUTPUT_FILE):
        print(
            "No existing publications.json found.",
            flush=True
        )
        return []

    try:
        with open(
            OUTPUT_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        print(
            f"Loaded {len(data)} previous publications.",
            flush=True
        )

        return data

    except Exception as e:

        print(
            f"Could not read existing publications.json: {e}",
            flush=True
        )

        return []


def make_existing_lookup(publications):

    lookup = {}

    for pub in publications:

        title = pub.get("title", "")

        if title:
            lookup[
                "title:" + normalize(title)
            ] = pub

        doi = pub.get("doi", "")

        if doi:
            clean_doi = (
                doi
                .replace("https://doi.org/", "")
                .replace("http://doi.org/", "")
                .lower()
                .strip()
            )

            lookup[
                "doi:" + clean_doi
            ] = pub

    return lookup


# ============================================================
# ORCID AUTHENTICATION
# ============================================================

def get_orcid_token():

    print(
        "Getting ORCID access token...",
        flush=True
    )

    if not ORCID_CLIENT_ID:
        raise RuntimeError(
            "ORCID_CLIENT_ID is missing from GitHub Secrets."
        )

    if not ORCID_CLIENT_SECRET:
        raise RuntimeError(
            "ORCID_CLIENT_SECRET is missing from GitHub Secrets."
        )

    response = requests.post(
        ORCID_TOKEN_URL,
        data={
            "client_id": ORCID_CLIENT_ID,
            "client_secret": ORCID_CLIENT_SECRET,
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

    print(
        "ORCID access token received.",
        flush=True
    )

    return token


# ============================================================
# GET ORCID WORKS
# ============================================================

def get_orcid_work_groups(token):

    print(
        "Fetching ORCID works...",
        flush=True
    )

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


# ============================================================
# PARSE ORCID METADATA
# ============================================================

def extract_year(summary):

    date = summary.get(
        "publication-date"
    )

    if not date:
        return None

    year = date.get("year")

    if not year:
        return None

    try:
        return int(
            year.get("value")
        )

    except Exception:
        return None


def extract_external_ids(summary):

    results = {}

    external_ids = summary.get(
        "external-ids",
        {}
    )

    for item in external_ids.get(
        "external-id",
        []
    ):

        id_type = (
            item
            .get(
                "external-id-type",
                ""
            )
            .lower()
        )

        value = item.get(
            "external-id-value",
            ""
        )

        url_object = item.get(
            "external-id-url"
        )

        url = ""

        if url_object:
            url = url_object.get(
                "value",
                ""
            )

        if id_type and value:

            results[id_type] = {
                "value": value,
                "url": url
            }

    return results


def parse_orcid_works(groups):

    publications = []

    for group in groups:

        summaries = group.get(
            "work-summary",
            []
        )

        if not summaries:
            continue

        # ORCID groups duplicate representations
        # of a single work. We use the first one.
        summary = summaries[0]

        title = (
            summary
            .get("title", {})
            .get("title", {})
            .get("value", "")
        )

        if not title:
            continue

        year = extract_year(
            summary
        )

        journal_object = summary.get(
            "journal-title"
        )

        journal = ""

        if journal_object:
            journal = journal_object.get(
                "value",
                ""
            )

        external_ids = extract_external_ids(
            summary
        )

        doi = ""

        if "doi" in external_ids:

            doi_value = (
                external_ids["doi"]
                ["value"]
                .strip()
            )

            if doi_value.startswith(
                "http"
            ):
                doi = doi_value

            else:
                doi = (
                    "https://doi.org/"
                    + doi_value
                )

        url = ""

        if doi:
            url = doi

        else:
            for ext in external_ids.values():

                if ext.get("url"):
                    url = ext["url"]
                    break

        publications.append({
            "title": title,
            "year": year,
            "journal": journal,
            "doi": doi,
            "url": url,

            # ORCID does not supply Google Scholar
            # citation counts.
            "citations": 0
        })

    return publications


# ============================================================
# PRESERVE OLD DATA
# ============================================================

def preserve_previous(
    pub,
    previous
):

    if not previous:
        return pub

    for field in [
        "authors",
        "journal",
        "doi",
        "crossref_match_score"
    ]:

        if (
            previous.get(field)
            and not pub.get(field)
        ):
            pub[field] = (
                previous[field]
            )

    return pub


# ============================================================
# CROSSREF REQUEST WITH RETRY
# ============================================================

def crossref_get(
    url,
    params=None
):

    headers = {
        "User-Agent":
            f"AcademicPublicationsUpdater/1.0 "
            f"(mailto:{CONTACT_EMAIL})"
    }

    for attempt in range(5):

        try:

            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=30
            )

        except requests.RequestException as e:

            wait = 2 ** attempt

            print(
                f"Crossref network error. "
                f"Waiting {wait}s: {e}",
                flush=True
            )

            time.sleep(wait)
            continue


        if response.status_code == 429:

            wait = 2 ** attempt

            print(
                f"Crossref rate limited us. "
                f"Waiting {wait}s...",
                flush=True
            )

            time.sleep(wait)
            continue


        if response.status_code >= 500:

            wait = 2 ** attempt

            print(
                f"Crossref server error "
                f"{response.status_code}. "
                f"Waiting {wait}s...",
                flush=True
            )

            time.sleep(wait)
            continue


        response.raise_for_status()

        return response


    raise RuntimeError(
        "Crossref request failed after multiple retries."
    )


# ============================================================
# APPLY CROSSREF METADATA
# ============================================================

def apply_crossref_metadata(
    pub,
    item,
    previous=None,
    score=None
):

    # ---------------- Authors ----------------

    names = []

    for author in item.get(
        "author",
        []
    ):

        given = author.get(
            "given",
            ""
        )

        family = author.get(
            "family",
            ""
        )

        full_name = (
            f"{given} {family}"
            .strip()
        )

        if full_name:
            names.append(
                full_name
            )

    if names:

        pub["authors"] = ", ".join(
            names
        )


    # ---------------- Journal ----------------

    journals = item.get(
        "container-title",
        []
    )

    if (
        journals
        and not pub.get("journal")
    ):

        pub["journal"] = (
            journals[0]
        )


    # ---------------- DOI ----------------

    crossref_doi = item.get(
        "DOI",
        ""
    )

    if (
        crossref_doi
        and not pub.get("doi")
    ):

        if not crossref_doi.startswith(
            "http"
        ):

            crossref_doi = (
                "https://doi.org/"
                + crossref_doi
            )

        pub["doi"] = (
            crossref_doi
        )


    if score is not None:

        pub[
            "crossref_match_score"
        ] = round(
            score,
            3
        )


    return preserve_previous(
        pub,
        previous
    )


# ============================================================
# CROSSREF ENRICHMENT
# ============================================================

def enrich_with_crossref(
    pub,
    previous=None
):

    title = pub.get(
        "title",
        ""
    )

    doi = pub.get(
        "doi",
        ""
    )


    # --------------------------------------------------------
    # 1. BEST CASE: ORCID already has DOI
    # --------------------------------------------------------

    if doi:

        clean_doi = (
            doi
            .replace(
                "https://doi.org/",
                ""
            )
            .replace(
                "http://doi.org/",
                ""
            )
            .strip()
        )

        print(
            "   Crossref: exact DOI lookup",
            flush=True
        )

        try:

            response = crossref_get(
                "https://api.crossref.org/works/"
                + quote(
                    clean_doi,
                    safe=""
                ),
                params={
                    "mailto":
                        CONTACT_EMAIL
                }
            )

            item = (
                response
                .json()
                .get(
                    "message",
                    {}
                )
            )

            return apply_crossref_metadata(
                pub,
                item,
                previous,
                1.0
            )

        except Exception as e:

            print(
                f"   DOI lookup failed: {e}",
                flush=True
            )


    # --------------------------------------------------------
    # 2. FALLBACK: search by title
    # --------------------------------------------------------

    if not title:

        return preserve_previous(
            pub,
            previous
        )


    print(
        "   Crossref: title search",
        flush=True
    )


    params = {
        "query.bibliographic":
            f"{title} {pub.get('year', '')}",

        "rows": 5,

        "select":
            "DOI,title,author,container-title",

        "mailto":
            CONTACT_EMAIL
    }


    try:

        response = crossref_get(
            "https://api.crossref.org/works",
            params=params
        )

        items = (
            response
            .json()
            .get(
                "message",
                {}
            )
            .get(
                "items",
                []
            )
        )

    except Exception as e:

        print(
            f"   Crossref search failed: {e}",
            flush=True
        )

        return preserve_previous(
            pub,
            previous
        )


    if not items:

        print(
            "   No Crossref results.",
            flush=True
        )

        return preserve_previous(
            pub,
            previous
        )


    best = None
    best_score = 0


    for item in items:

        titles = item.get(
            "title",
            []
        )

        if not titles:
            continue

        score = similarity(
            title,
            titles[0]
        )

        if score > best_score:

            best_score = score
            best = item


    if (
        not best
        or best_score
        < MIN_CROSSREF_SCORE
    ):

        print(
            f"   No confident Crossref match. "
            f"Best score: {best_score:.3f}",
            flush=True
        )

        return preserve_previous(
            pub,
            previous
        )


    print(
        f"   Crossref match: "
        f"{best_score:.3f}",
        flush=True
    )


    return apply_crossref_metadata(
        pub,
        best,
        previous,
        best_score
    )


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():

    token = get_orcid_token()

    groups = get_orcid_work_groups(
        token
    )

    orcid_publications = (
        parse_orcid_works(
            groups
        )
    )


    print(
        f"Parsed "
        f"{len(orcid_publications)} "
        f"ORCID publications.",
        flush=True
    )


    if len(orcid_publications) == 0:

        raise RuntimeError(
            "ORCID returned zero usable publications."
        )


    old_publications = (
        load_existing_publications()
    )

    old_lookup = (
        make_existing_lookup(
            old_publications
        )
    )


    enriched = []


    for i, pub in enumerate(
        orcid_publications,
        start=1
    ):

        print(
            "",
            flush=True
        )

        print(
            f"[{i}/"
            f"{len(orcid_publications)}] "
            f"{pub['title']}",
            flush=True
        )


        previous = None


        # Try matching old metadata by DOI first.

        if pub.get("doi"):

            clean_doi = (
                pub["doi"]
                .replace(
                    "https://doi.org/",
                    ""
                )
                .replace(
                    "http://doi.org/",
                    ""
                )
                .lower()
                .strip()
            )

            previous = (
                old_lookup.get(
                    "doi:" + clean_doi
                )
            )


        # Fall back to title.

        if not previous:

            previous = (
                old_lookup.get(
                    "title:"
                    + normalize(
                        pub["title"]
                    )
                )
            )


        enriched_pub = (
            enrich_with_crossref(
                pub,
                previous
            )
        )


        enriched.append(
            enriched_pub
        )


        # Crossref currently limits list/search
        # requests. One second is intentionally
        # conservative for a weekly job.
        time.sleep(1.1)


    # ========================================================
    # SAVE FILE
    # ========================================================

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
        "",
        flush=True
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
