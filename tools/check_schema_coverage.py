#!/usr/bin/env python3
"""Check the profile schema against the code that actually reads it.

The schema's `x-read-by` field claims which ends consume each key. That claim is the whole
point of the file — four bugs in one month were all one end knowing about a key the other did
not, and none of them raised an error. A claim nobody checks is worth nothing, so this checks
it.

Three things go wrong, and all three are reported:

  UNDECLARED   the code reads a key the schema has never heard of. The schema has drifted
               behind the product, which is exactly the state it was found in.

  UNREAD       the schema says an end reads a key and that end's source never mentions it.
               THIS IS THE DIVERGENCE BUG. `extraViews` marked [studio, exact] while the
               backend ignored it; `wallTop` the same. Both shipped silently.

  ORPHANED     the schema declares a key nobody reads at all. Usually a rename left behind.

Run it from anywhere:

    python3 tools/check_schema_coverage.py --frontend ../LEE3D-Frontend --backend ../LEE3D-Backend-A

Exits non-zero if anything is wrong, so it can gate a build.

A note on the method: this greps source text rather than parsing it. That is deliberate — the
two codebases are JavaScript and Python and a real parse of both would be a project in itself.
Grep can be fooled (a key named in a comment counts as read), so it is a floor, not a ceiling:
it will not catch every divergence, but everything it does report is real.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STUDIO = "studio"
EXACT = "exact"


def read_sources(root: Path, patterns: list[str]) -> str:
    """All the source text for one end, concatenated."""
    out = []
    for pat in patterns:
        for f in sorted(root.glob(pat)):
            try:
                out.append(f.read_text(encoding="utf8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(out)


def mentions(src: str, key: str) -> bool:
    """Does this source read this key at all? Word-boundary match, so `wall` does not match
    `wallTop` and give a false all-clear on the very keys that went missing."""
    return re.search(r"\b" + re.escape(key) + r"\b", src) is not None


def keys_the_studio_reads(src: str) -> set[str]:
    """Every `p.something` / `profile.something` in the frontend. Over-collects — locals and
    array members come along — so it is only used to find keys the schema has NOT declared,
    never to claim something is unused."""
    found = set()
    for m in re.finditer(r"\b(?:p|prof|profile)\.([A-Za-z_][A-Za-z0-9_]*)", src):
        found.add(m.group(1))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent.parent
    ap.add_argument("--schema", default=str(here / "schema" / "profile.schema.json"))
    ap.add_argument("--frontend", default="")
    ap.add_argument("--backend", default="")
    ap.add_argument("--profiles", default=str(here / "schema"),
                    help="folder of saved .profile.json files to validate")
    args = ap.parse_args()

    here_lib = Path(args.schema).resolve().parent.parent
    schema = json.loads(Path(args.schema).read_text(encoding="utf8"))
    props: dict = schema.get("properties", {})
    print(f"schema: {len(props)} properties declared\n")

    problems = 0

    # ---- 1. every real saved profile must still validate -------------------------------
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        print("! jsonschema not installed — skipping profile validation")
        Draft7Validator = None
    if Draft7Validator is not None:
        Draft7Validator.check_schema(schema)
        v = Draft7Validator(schema)
        folder = Path(args.profiles)
        files = [f for f in sorted(folder.glob("*.json"))
                 if "schema" not in f.name and "manifest" not in f.name]
        bad = 0
        for f in files:
            try:
                p = json.loads(f.read_text(encoding="utf8"))
            except Exception as e:
                print(f"  UNREADABLE  {f.name}: {e}")
                bad += 1
                continue
            errs = list(v.iter_errors(p))
            if errs:
                bad += 1
                print(f"  INVALID     {f.name}: {errs[0].message[:80]}")
        print(f"profiles: {len(files) - bad}/{len(files)} validate")
        if bad:
            print("  ^ a schema that rejects the library it describes is describing something else")
        problems += bad
        print()

    ends = []
    if args.frontend:
        ends.append((STUDIO, "studio", read_sources(Path(args.frontend), ["index.html"])))
    if args.backend:
        ends.append((EXACT, "exact backend", read_sources(Path(args.backend), ["app/*.py"])))
    if not ends:
        print("no source trees given (--frontend / --backend); only the profiles were checked")
        return 1 if problems else 0

    # ---- 2. UNREAD: the schema claims an end reads a key, and it does not ---------------
    print("checking every `x-read-by` claim against the source...")
    for key, spec in sorted(props.items()):
        claimed = spec.get("x-read-by", [])
        for tag, label, src in ends:
            if tag in claimed and not mentions(src, key):
                print(f"  UNREAD      '{key}' is marked [{tag}] but the {label} never reads it")
                problems += 1

    # ---- 3. UNDECLARED: the studio reads something the schema has not heard of ----------
    for tag, label, src in ends:
        if tag != STUDIO:
            continue
        # only report keys that a real saved profile also carries, so locals and array
        # members do not drown the signal
        carried: set[str] = set()
        for f in Path(args.profiles).glob("*.json"):
            if "schema" in f.name or "manifest" in f.name:
                continue
            try:
                carried |= set(json.loads(f.read_text(encoding="utf8")))
            except Exception:
                pass
        for key in sorted(keys_the_studio_reads(src) & carried):
            if key not in props:
                print(f"  UNDECLARED  '{key}' is read by the {label} and saved in real profiles,"
                      f" but the schema does not declare it")
                problems += 1

    # ---- 3b. the pydantic API contract is a THIRD declaration of the same thing ----------
    # `app/schemas.py` is what the backend validates incoming requests against, and it exists
    # in both the backend and the library as identical copies. It currently names 35 fields
    # against the schema's 54, and survives only because `extra="allow"` lets everything else
    # through untouched — a real car round-trips with all 228 of its pockets intact.
    # So this is reported, not failed: nothing is broken, but a contract that does not name
    # `features` or `hullHollow` is not documenting the thing it guards. If `extra` is ever
    # tightened to "ignore" or "forbid", every unnamed key silently vanishes from every
    # request, and this list becomes the damage report.
    for tag, label, _ in ends:
        if tag != EXACT or not args.backend:
            continue
        api = Path(args.backend) / "app" / "schemas.py"
        if not api.exists():
            continue
        text = api.read_text(encoding="utf8", errors="ignore")
        named = set(re.findall(r"^\s{4}([a-zA-Z_][a-zA-Z0-9_]*)\s*:", text, re.M)) - {"model_config"}
        # Match the CONFIG, not the word. My first attempt was
        #     re.search(r'extra\s*=\s*"(ignore|forbid)"', text)
        # which matched the word "ignore" inside a COMMENT explaining that pydantic's default
        # is ignore — and reported a strict contract on a file whose real setting is "allow".
        # This is precisely the grep limitation named at the top of this file, and it caught me
        # inside the checker itself. Read the ConfigDict line, and only that line.
        cfg = re.search(r"model_config\s*=\s*ConfigDict\(([^)]*)\)", text)
        strict = bool(cfg and re.search(r'extra\s*=\s*"(ignore|forbid)"', cfg.group(1)))
        undeclared = sorted(k for k in props if k not in named)
        if undeclared:
            how = "FAILS" if strict else "reports"
            print(f"  {'API-STRICT ' if strict else 'API-NOTE   '} app/schemas.py names "
                  f"{len(named)} fields; {len(undeclared)} schema keys are not among them "
                  f"({how}: extra={'strict' if strict else 'allow'})")
            if strict:
                print(f"              these would be DROPPED from every request: "
                      f"{' '.join(undeclared[:10])}")
                problems += 1

    # ---- 3c. a second copy of the API contract is a place to drift ----------------------
    # `app/schemas.py` existed in BOTH the backend and this library, byte-identical, and
    # nothing in this repo imported it — not the README, not a workflow, not one file. A copy
    # nobody reads cannot be caught being wrong, so it is a liability with no upside: the day
    # the backend's version gains a field and this one does not, the two disagree and only a
    # reader looking at the wrong one would ever know.
    # If the copy is kept anyway, it has to be identical. Checked rather than trusted.
    mine = here_lib / "app" / "schemas.py"
    if args.backend and mine.exists():
        theirs = Path(args.backend) / "app" / "schemas.py"
        if theirs.exists():
            a = mine.read_bytes()
            b = theirs.read_bytes()
            if a != b:
                print(f"  DRIFTED     app/schemas.py differs between this repo and the backend "
                      f"({len(a)} bytes vs {len(b)}). One of them is telling somebody the wrong "
                      f"thing about what a profile is.")
                problems += 1
            else:
                print(f"  COPY-NOTE   app/schemas.py is duplicated here and in the backend, "
                      f"identical for now. Nothing in this repo imports it — deleting the copy "
                      f"removes a drift risk with no cost.")

    # ---- 4. ORPHANED: nobody reads it at all --------------------------------------------
    for key, spec in sorted(props.items()):
        if not any(mentions(src, key) for _, _, src in ends):
            print(f"  ORPHANED    '{key}' is declared but no end reads it")
            problems += 1

    print()
    if problems:
        print(f"{problems} problem(s). Each one is a place the three repos disagree about what a "
              f"profile is.")
        return 1
    print("clean — the schema and both codebases agree on what a profile is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
