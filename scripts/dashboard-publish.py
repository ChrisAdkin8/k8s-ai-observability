#!/usr/bin/env python3
"""
dashboard-publish.py — derive the grafana.com upload from a repo dashboard.

    python3 scripts/dashboard-publish.py          # -> dist/grafana-com/*.json
    task dashboards

WHY THIS EXISTS
---------------
`manifests/dashboards/*.json` stays the single source of truth — install.sh wraps
each file in a ConfigMap, and the compose path mounts the directory. This adds a
third derived form, on the same terms as the ConfigMap: generated, never edited,
never committed.

It exists because the repo files cannot be uploaded to grafana.com as-is. They
bind every panel to a `datasource`-type template variable, which is the correct
design in-cluster: the sidecar provisions the board and the variable resolves to
whatever Prometheus is there. The catalog wants the opposite — the `__inputs`
block Grafana 3.0 introduced, so it can prompt the importer for a datasource.
Upload a file without it and the uploader rejects it:

    Warning: Old dashboard JSON format. Read about Importing & Sharing with
    Grafana 2.x or 3.0

Grafana's own "Export for sharing externally" does not help here, and the reason
is worth knowing: that exporter works by rewriting a CONCRETE datasource uid into
an input placeholder. A datasource *variable* has already abstracted the uid
away, so the exporter finds nothing to rewrite and emits no `__inputs` at all.
The cleaner the repo file is, the more certainly the catalog rejects it.

WHAT IT CHANGES, AND NOTHING ELSE
---------------------------------
  * adds __inputs   — declares DS_PROMETHEUS, the block the uploader requires
  * adds __requires — Grafana floor, the Prometheus plugin, and every panel
                      plugin actually present (collected, not hardcoded, so a
                      new panel type cannot be silently omitted)
  * rewrites ${datasource} -> ${DS_PROMETHEUS} everywhere, including inside
    query-variable definitions, which are easy to miss by hand — the LLM board
    has 22 such references
  * drops the now-redundant datasource template variable, so an importer is not
    shown a picker beside the import-time prompt

It also FAILS rather than emitting a broken upload if a panel has picked up a
hardcoded datasource uid, which is what a board edited in the Grafana UI and
pasted back will do. That check used to live as a copy-paste snippet in
manifests/dashboards/README.md; running it as part of the thing that consumes
the result means it cannot be skipped.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# A floor, not the version we happen to run. schemaVersion 39 is Grafana 10+;
# claiming anything newer excludes importers for no reason.
GRAFANA_FLOOR = "10.0.0"

SRC_VAR = "${datasource}"
DST_VAR = "${DS_PROMETHEUS}"

DEFAULT_SRC = "manifests/dashboards/*.json"
DEFAULT_OUT = "dist/grafana-com"


def panel_types(node, found):
    """Panel plugin ids, for __requires.

    A panel is a dict carrying BOTH `type` and `gridPos`. Matching on `type`
    alone also catches templating variables, targets and threshold steps, which
    would put junk like "query" and "dashboard" in __requires.
    """
    if isinstance(node, dict):
        if "type" in node and "gridPos" in node:
            found.add(node["type"])
        for value in node.values():
            panel_types(value, found)
    elif isinstance(node, list):
        for value in node:
            panel_types(value, found)


def fixed_datasource_uids(raw, own_uid):
    """Datasource uids that are literals rather than ${...} references.

    The dashboard's own top-level uid is excluded — it is a literal by design.
    Anything else means a panel is pinned to one specific Prometheus instance,
    so the board would import against a datasource the importer does not have.
    """
    found = set(re.findall(r'"uid"\s*:\s*"([^"$][^"]*)"', raw))
    return sorted(found - {own_uid})


def retarget(node):
    """Point every datasource reference at the declared input."""
    if isinstance(node, dict):
        return {k: (DST_VAR if (k == "uid" and v == SRC_VAR) else retarget(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [retarget(value) for value in node]
    return DST_VAR if node == SRC_VAR else node


def convert(path):
    # Closed explicitly rather than left to refcounting: CPython frees the handle
    # at once, other runtimes do not, and this repo's scripts are meant to be
    # boring everywhere.
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    dash = json.loads(raw)

    own_uid = dash.get("uid", "")
    pinned = fixed_datasource_uids(raw, own_uid)
    if pinned:
        raise SystemExit(
            f"{path}: hardcoded datasource uid(s) {pinned}.\n"
            "  A panel is pinned to one specific datasource, so this board cannot be\n"
            "  imported by anyone else. Usually caused by editing in the Grafana UI and\n"
            "  pasting the JSON Model back. Repoint the panel at the ${datasource}\n"
            "  variable before publishing."
        )

    types = set()
    panel_types(dash, types)
    if not types:
        raise SystemExit(f"{path}: no panels found — is this a dashboard?")

    dash = retarget(dash)
    remaining = json.dumps(dash).count(SRC_VAR)
    if remaining:
        raise SystemExit(f"{path}: {remaining} unconverted {SRC_VAR} reference(s)")

    # The variable's job is done: DS_PROMETHEUS replaces it.
    tpl = dash.get("templating", {}).get("list", [])
    dash.setdefault("templating", {})["list"] = [
        v for v in tpl if v.get("type") != "datasource"
    ]

    requires = [
        {"type": "grafana", "id": "grafana", "name": "Grafana", "version": GRAFANA_FLOOR},
        {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"},
    ] + [{"type": "panel", "id": t, "name": t, "version": ""} for t in sorted(types)]

    # __inputs and __requires lead the object. Grafana tolerates any order, but
    # every published board puts them first, and diffing against one is easier
    # when this matches.
    out = {
        "__inputs": [{
            "name": "DS_PROMETHEUS",
            "label": "Prometheus",
            "description": "",
            "type": "datasource",
            "pluginId": "prometheus",
            "pluginName": "Prometheus",
        }],
        "__requires": requires,
    }
    out.update(dash)

    # An `id` makes the upload an UPDATE to whatever board holds that id. Absent
    # in this repo; popped so a future edit cannot reintroduce it silently.
    out.pop("id", None)
    return out, sorted(types)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("files", nargs="*", help=f"default: {DEFAULT_SRC}")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    files = args.files or sorted(glob.glob(DEFAULT_SRC))
    if not files:
        raise SystemExit(f"no dashboards matched {DEFAULT_SRC}")

    os.makedirs(args.out, exist_ok=True)
    for src in files:
        dash, types = convert(src)
        dst = os.path.join(args.out, os.path.basename(src))
        with open(dst, "w") as fh:
            json.dump(dash, fh, indent=2)
            fh.write("\n")
        print(f"  {dst}\n      uid={dash['uid']}  panels={', '.join(types)}")

    print(f"\n{len(files)} board(s) ready. Upload these, NOT the files in "
          f"manifests/dashboards/ — see manifests/dashboards/README.md.")
    # ⚠️ Explicit, because `sys.exit(main())` below CLAIMS a status code and got
    # `None` — which exits 0. Correct here only because every failure path
    # raises SystemExit with a message instead of returning. Three other
    # scripts in this repo return a code properly; these two did not.
    return 0


if __name__ == "__main__":
    sys.exit(main())
