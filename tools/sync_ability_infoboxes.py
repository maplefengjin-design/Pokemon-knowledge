from __future__ import annotations

import hashlib
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = (
    ROOT
    / "data"
    / "raw"
    / "upstream"
    / "pokeapi"
    / "data"
    / "v2"
    / "csv"
)
OUTPUT = ROOT / "config" / "ability_infobox_mainline.json"
API_URL = "https://wiki.52poke.com/api.php"
FIELDS = {
    "Skillswap": {
        "identifier": "skill-swap",
        "default": "yes",
        "positive_label_zh": "可以被交换的特性",
        "negative_label_zh": "不能被交换的特性",
        "description_zh": "能否被特性互换交换。",
    },
    "Change": {
        "identifier": "ability-change",
        "default": "yes",
        "positive_label_zh": "可以被其他特性覆盖的特性",
        "negative_label_zh": "不能被其他特性覆盖的特性",
        "description_zh": "能否被招式或特性的改写效果覆盖。",
    },
    "Trace": {
        "identifier": "copyable",
        "default": "yes",
        "positive_label_zh": "可以被其他宝可梦复制的特性",
        "negative_label_zh": "不能被其他宝可梦复制的特性",
        "description_zh": "能否被其他宝可梦的常规特性复制机制取得。",
    },
    "Noability": {
        "identifier": "suppression",
        "default": "yes",
        "positive_label_zh": "受无特性状态影响的特性",
        "negative_label_zh": "不受无特性状态影响的特性",
        "description_zh": "是否会被胃液、化学变化气体等造成的无特性状态压制。",
    },
    "Transform": {
        "identifier": "transform",
        "default": "yes",
        "positive_label_zh": "变身时有效的特性",
        "negative_label_zh": "变身时无效的特性",
        "description_zh": "宝可梦处于变身状态时，该特性是否有效。",
    },
    "Entry": {
        "identifier": "entry",
        "default": "no",
        "positive_label_zh": "入场时发动的特性",
        "negative_label_zh": "不在入场时发动的特性",
        "description_zh": "该特性是否属于入场时发动的特性。",
    },
}


def _request_pages(titles: list[str]) -> list[dict[str, object]]:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
            "titles": "|".join(titles),
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "pokemon-knowledge-pokemmo/0.1 ability-infobox-sync"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["query"]["pages"]


def _infobox_fields(content: str) -> dict[str, str]:
    match = re.search(r"(?ms)^\{\{特性信息框\s*\n(.*?)^\}\}\s*$", content)
    if match is None:
        raise ValueError("Missing 特性信息框")
    return {
        key.strip(): value.strip()
        for key, value in re.findall(r"(?m)^\|([^=\n]+)=(.*)$", match.group(1))
    }


def main() -> None:
    with (CSV_DIR / "abilities.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        abilities = {
            int(row["id"]): row
            for row in csv.DictReader(handle)
            if row["is_main_series"] == "1"
        }
    with (CSV_DIR / "ability_names.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        records = [
            {
                "id": int(row["ability_id"]),
                "identifier": abilities[int(row["ability_id"])]["identifier"],
                "name_zh": row["name"],
            }
            for row in csv.DictReader(handle)
            if row["local_language_id"] == "12"
            and int(row["ability_id"]) in abilities
        ]
    by_title: dict[str, list[dict[str, object]]] = {}
    by_name: dict[str, list[dict[str, object]]] = {}
    for record in records:
        name = str(record["name_zh"]).strip()
        by_title.setdefault(f"{name}（特性）", []).append(record)
        by_name.setdefault(name, []).append(record)
    parsed: list[dict[str, object]] = []
    for offset in range(0, len(by_title), 40):
        requested_titles = list(by_title)[offset : offset + 40]
        for page in _request_pages(requested_titles):
            if page.get("missing"):
                raise ValueError(f"Missing encyclopedia page: {page['title']}")
            revision = page["revisions"][0]
            content = revision["slots"]["main"]["content"]
            fields = _infobox_fields(content)
            local_records = by_title.get(str(page["title"])) or by_name.get(
                str(fields.get("name", "")).strip()
            )
            if local_records is None:
                raise ValueError(f"Cannot map encyclopedia page to local ability: {page['title']}")
            wiki_number = int(fields["n"]) if fields.get("n") else None
            states: dict[str, str] = {}
            explicit_fields: list[str] = []
            for field, definition in FIELDS.items():
                raw_value = fields.get(field)
                if raw_value is None:
                    state = str(definition["default"])
                else:
                    value_match = re.search(r"[A-Za-z]+", raw_value)
                    value = value_match.group(0).lower() if value_match else ""
                    # Mirror the MediaWiki template exactly: Entry is true only
                    # for "yes"; the five default-yes fields are false only for
                    # "no".  This safely handles historical values such as "y".
                    if field == "Entry":
                        state = "yes" if value == "yes" else "no"
                    else:
                        state = "no" if value == "no" else "yes"
                    explicit_fields.append(field)
                states[str(definition["identifier"])] = state
            for local_record in local_records:
                parsed.append(
                    {
                        "ability_id": int(local_record["id"]),
                        "identifier": str(local_record["identifier"]),
                        "wiki_number": wiki_number,
                        "name_zh": fields["name"].strip(),
                        "page_title": page["title"],
                        "page_id": int(page["pageid"]),
                        "revision_id": int(revision["revid"]),
                        "revision_timestamp": revision["timestamp"],
                        "explicit_fields": explicit_fields,
                        "states": states,
                    }
                )
        time.sleep(0.2)

    expected_ids = {int(record["id"]) for record in records}
    actual_ids = {int(record["ability_id"]) for record in parsed}
    if actual_ids != expected_ids:
        raise ValueError(
            f"Ability coverage mismatch; missing={sorted(expected_ids - actual_ids)}, "
            f"unexpected={sorted(actual_ids - expected_ids)}"
        )
    parsed.sort(key=lambda record: int(record["ability_id"]))
    revision_fingerprint = "\n".join(
        f"{record['ability_id']}:{record['revision_id']}" for record in parsed
    )
    payload = {
        "schema_version": 1,
        "source_id": "pokemon-encyclopedia-ability-infobox",
        "source_url": "https://wiki.52poke.com",
        "license": "CC-BY-NC-SA-3.0",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "revision_digest": hashlib.sha256(
            revision_fingerprint.encode("utf-8")
        ).hexdigest(),
        "latest_revision_timestamp": max(
            str(record["revision_timestamp"]) for record in parsed
        ),
        "ruleset_scope": "current-mainline",
        "categories": [
            {
                "source_field": field,
                **definition,
            }
            for field, definition in FIELDS.items()
        ],
        "abilities": parsed,
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(parsed)} ability infobox records to {OUTPUT}")


if __name__ == "__main__":
    main()
