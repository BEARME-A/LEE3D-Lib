# LEE3D-Lib

The **model library** for the LEE3D car-body pipeline. This is a normal,
browsable, version-controlled GitHub repo that stores every input drawing, every
parametric profile, and every generated body. Because it's just git, you get
free history, diffs, and rollback for your whole catalogue of cars.

Repo: `https://github.com/BEARME-A/LEE3D-Lib`

```
LEE3D-Lib/
├── drawings/     ← line drawings / scans of side views (the raw 2D input)
├── photos/       ← photos of drawings or reference cars
├── json/         ← loose profile.json files not tied to a project
├── generated/    ← STL / STEP bodies produced by the backend
├── exports/      ← anything pushed from the frontend "Save to library" button
├── versions/     ← snapshots of profiles over time (provenance)
├── schema/       ← the shared JSON Schemas (validate anything here)
│   ├── profile.schema.json
│   └── manifest.schema.json
└── projects/
    └── <car-name>/
        ├── manifest.json                ← ties it all together (chassis + artifacts)
        └── <car-name>.profile.json      ← the editable body definition
```

## The two file formats

Everything in the system speaks **one profile format**, defined in
[`schema/profile.schema.json`](schema/profile.schema.json). The frontend exports
it, the backend reads it, and it lives here. Validate any file:

```bash
pip install check-jsonschema
check-jsonschema --schemafile schema/profile.schema.json projects/example-charger/example-charger.profile.json
```

A **manifest** ([`schema/manifest.schema.json`](schema/manifest.schema.json))
sits in each project folder and records the real chassis the body must fit plus
links to every drawing, profile, and STL — so a build is reproducible from raw
drawing to printable file.

See the worked example in
[`projects/example-charger/`](projects/example-charger/). That profile has been
run through the geometry engine and verified to produce a **watertight,
manifold** body (21,056 triangles, zero open edges).

## How files get here

- **From the frontend:** the *Save to library* button hands you a `profile.json`
  (and, with the backend running, calls `POST /library/commit`).
- **From the backend:** `POST /generate?commit_to_library=true` writes the STL
  into `generated/<project>/` and `POST /import/image?commit=true` stores the
  drawing under `drawings/` or `photos/`. The backend uses the GitHub Contents
  API, so commits show up here like any other.

Paths follow `storage.library_path()` in the backend, e.g.
`generated/example-charger/example-charger.stl`.

## Note on big binaries

STL/STEP and image files are binary; a busy catalogue will grow git history. For
a hobby project that's fine. If it ever gets heavy, enable **Git LFS** for
`*.stl *.step *.png *.jpg` — nothing else in the workflow changes.
