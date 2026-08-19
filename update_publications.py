import json
import os
import sys
import time
from difflib import SequenceMatcher

import requests
from scholarly_publications import fetch_publications


print("=== Python script started ===", flush=True)


SCHOLAR_ID = "OqIPbg4AAAAJ"
OUTPUT_FILE = "publications.json"

# Your Google Scholar profile currently has about 55 publications.
# If Scholar suddenly returns far fewer, assume the fetch failed.
MIN_EXPECTED_PUBLICATIONS = 40

# Only accept reasonably strong Crossref title matches.
MIN_CROSSREF_SCORE = 0.85

# Replace this with your email address.
CONTACT_EMAIL = "YOUR_EMAIL_HERE"


def normalize(text):
    return " ".join(
        (text or "").lower().strip().split()
    )


def similarity(a, b):
    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


def preserve_existing_metadata(pub, existing_pub):
    """
    If Crossref fails, preserve Authors / Journal / DOI
    from the previous publications.json.
    """

    if not existing_pub:
        return pub

    for key in [
        "authors",
        "journal",
        "doi",
        "crossref_match_score"
    ]:
        if existing_pub.get(key):
            pub[key] = existing_pub[key]

    return pub


def load_existing_publications():
    print(
        f"Checking for existing {OUTPUT_FILE}...",
        flush=True
    )

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
            f"Loaded {len(data)} existing publications.",
            flush=True
        )

        return data

    except Exception as e:
        print(
            f"WARNING: Could not read existing JSON: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

        return []


def make_existing_lookup(existing_publications):
    lookup = {}

    for pub in existing_publications:
        link = pub.get("link")

        if link:
            lookup[link] = pub

    return lookup


def enrich_with_crossref(pub, existing_pub=None):

    title = pub.get("title", "")
    year = pub.get("year", "")

    if not title:
        print(
            "Skipping Crossref lookup because title is empty.",
            flush=True
        )
        return preserve_existing_metadata(
            pub,
            existing_pub
        )


    params = {
        "query.bibliographic": f"{title} {year}",
        "rows": 5,
        "select":
            "DOI,title,author,container-title,published",
        "mailto": CONTACT_EMAIL
    }


    headers = {
        "User-Agent":
            f"AcademicPublicationsUpdater/1.0 "
            f"(mailto:{CONTACT_EMAIL})"
    }


    try:

        response = requests.get(
            "https://api.crossref.org/works",
            params=params,
            headers=headers,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        items = (
            data
            .get("message", {})
            .get("items", [])
        )

    except Exception as e:

        print(
            f"Crossref failed: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

        return preserve_existing_metadata(
            pub,
            existing_pub
        )


    if not items:

        print(
            "No Crossref results.",
            flush=True
        )

        return preserve_existing_metadata(
            pub,
            existing_pub
        )


    best_match = None
    best_score = 0


    for item in items:

        titles = item.get("title", [])

        crossref_title = (
            titles[0]
            if titles
            else ""
        )

        score = similarity(
            title,
            crossref_title
        )

        if score > best_score:
            best_score = score
            best_match = item


    if (
        best_match is None
        or best_score < MIN_CROSSREF_SCORE
    ):

        print(
            f"No confident Crossref match. "
            f"Best score: {best_score:.3f}",
            flush=True
        )

        return preserve_existing_metadata(
            pub,
            existing_pub
        )


    # -----------------------
    # Authors
    # -----------------------

    author_names = []

    for author in best_match.get(
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
            author_names.append(
                full_name
            )


    if author_names:

        pub["authors"] = ", ".join(
            author_names
        )

    elif existing_pub and existing_pub.get(
        "authors"
    ):

        pub["authors"] = (
            existing_pub["authors"]
        )


    # -----------------------
    # Journal
    # -----------------------

    journals = best_match.get(
        "container-title",
        []
    )

    journal = (
        journals[0]
        if journals
        else ""
    )


    if journal:

        pub["journal"] = journal

    elif existing_pub and existing_pub.get(
        "journal"
    ):

        pub["journal"] = (
            existing_pub["journal"]
        )


    # -----------------------
    # DOI
    # -----------------------

    doi = best_match.get(
        "DOI",
        ""
    )

    if doi:

        doi = doi.strip()

        if not doi.startswith("http"):
            doi = (
                "https://doi.org/"
                + doi
            )

        pub["doi"] = doi

    elif existing_pub and existing_pub.get(
        "doi"
    ):

        pub["doi"] = (
            existing_pub["doi"]
        )


    pub["crossref_match_score"] = round(
        best_score,
        3
    )


    print(
        f"Crossref match score: "
        f"{best_score:.3f}",
        flush=True
    )

    return pub


def main():

    print(
        "=== Entered main() ===",
        flush=True
    )

    print(
        f"Scholar ID: {SCHOLAR_ID}",
        flush=True
    )

    print(
        "Calling Google Scholar...",
        flush=True
    )


    try:

        publications = fetch_publications(
            SCHOLAR_ID,
            sortby="pubdate"
        )

        print(
            "Google Scholar call finished.",
            flush=True
        )

    except Exception as e:

        print(
            f"Google Scholar fetch failed: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

        sys.exit(1)


    if publications is None:

        print(
            "ERROR: Google Scholar returned None.",
            flush=True
        )

        sys.exit(1)


    count = len(publications)


    print(
        f"Google Scholar returned "
        f"{count} publications.",
        flush=True
    )


    # -----------------------
    # Safety check
    # -----------------------

    if count < MIN_EXPECTED_PUBLICATIONS:

        print(
            "=== SAFETY STOP ===",
            flush=True
        )

        print(
            f"Expected at least "
            f"{MIN_EXPECTED_PUBLICATIONS} publications "
            f"but received only {count}.",
            flush=True
        )

        print(
            "This probably means Google Scholar "
            "blocked or throttled the request.",
            flush=True
        )

        print(
            "Existing publications.json "
            "will NOT be overwritten.",
            flush=True
        )

        sys.exit(1)


    # -----------------------
    # Load existing JSON
    # -----------------------

    existing_publications = (
        load_existing_publications()
    )

    existing_lookup = (
        make_existing_lookup(
            existing_publications
        )
    )


    # -----------------------
    # Crossref enrichment
    # -----------------------

    enriched = []

    matched = 0
    unmatched = 0


    for index, pub in enumerate(
        publications,
        start=1
    ):

        title = pub.get(
            "title",
            "Untitled"
        )

        print(
            "",
            flush=True
        )

        print(
            f"[{index}/{count}] "
            f"{title}",
            flush=True
        )


        existing_pub = (
            existing_lookup.get(
                pub.get("link")
            )
        )


        result = enrich_with_crossref(
            pub.copy(),
            existing_pub
        )


        if result.get(
            "crossref_match_score"
        ):

            matched += 1

        else:

            unmatched += 1


        enriched.append(
            result
        )


        # Small delay for Crossref
        time.sleep(0.25)


    # -----------------------
    # Final safety check
    # -----------------------

    if len(enriched) < MIN_EXPECTED_PUBLICATIONS:

        print(
            "ERROR: Enriched publication list "
            "is unexpectedly small.",
            flush=True
        )

        print(
            "Existing publications.json "
            "will NOT be overwritten.",
            flush=True
        )

        sys.exit(1)


    # -----------------------
    # Save JSON
    # -----------------------

    print(
        "",
        flush=True
    )

    print(
        "Writing publications.json...",
        flush=True
    )


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
        "=== UPDATE COMPLETED SUCCESSFULLY ===",
        flush=True
    )

    print(
        f"Total publications: {len(enriched)}",
        flush=True
    )

    print(
        f"Crossref matched: {matched}",
        flush=True
    )

    print(
        f"Without new Crossref match: {unmatched}",
        flush=True
    )


if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "",
            flush=True
        )

        print(
            "=== UNEXPECTED ERROR ===",
            flush=True
        )

        print(
            f"{type(e).__name__}: {e}",
            flush=True
        )

        raise
