# Nihonjindes — Japanese for Italian Speakers (LibreLingo fork)

This repository is a **single-repo** setup that contains:
- the upstream **LibreLingo Community** web app (as a fork),
- a custom Japanese course for Italian speakers,
- scripts + a JSON “source of truth” used to generate all course files.

The course content (text + images) is my own work-in-progress study material.

---

## Repository layout (important folders)

- `data/course-json/jp_course.json`  
  **Source of truth** (“database”) for categories, lessons, items, and image paths.

- `data/course-img/`  
  Custom images referenced by the JSON.

- `data/course-script/`  
  Helper scripts:
  - `ll02_generate_course_from_json.py` → JSON → LibreLingo YAML course (and imports images)
  - (other helper scripts may be added over time)

- `courses/japanese-from-italian/`  
  Generated **LibreLingo YAML course** (do not edit by hand; regenerate from JSON).

- `apps/web/src/courses/japanese-from-italian/`  
  Generated **web-export JSON** (created by `exportYamlCourse.sh`).

---

## Prerequisites

### Required
- **Node.js + npm** (to run the web app)
- **Python 3** for the generator scripts (`ll02`), plus `pip`
- `git` (and optionally `git-lfs` if your fork uses LFS assets)

### Notes about Python environments
LibreLingo’s exporter tooling (invoked by `exportYamlCourse.sh`) uses the repo’s own Python tooling under `src/`.
It may create a dedicated virtualenv under `src/.venv` automatically.

---

## First-time setup (local)

From the repo root:

1) Install Node dependencies:
```bash
npm install
```

2) Create a Python venv for the generator scripts and install requirements:
```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip wheel setuptools
./.venv/bin/pip install -r data/course-script/requirements.txt
```

---

## Course editing workflow (the only thing you should edit)

To update the course, **only edit this JSON file**:

`data/course-json/jp_course.json`

### Images
- The JSON references images (PNG or JPG) we place in `data/course-img/`.
- Images should ideally be **square** and **high-resolution**.
- The generator will create 3 resized variants (for performance) under `apps/web/static/images/`.

---

## Regenerate the course (JSON → YAML → Web export)

After editing `data/course-json/jp_course.json`, run:

1) Generate YAML course + import images:
```bash
./.venv/bin/python data/course-script/ll02_generate_course_from_json.py \
  --repo . \
  --spec data/course-json/jp_course.json \
  --prune-course
```

2) Export the YAML course into the web-app JSON format:
```bash
bash scripts/exportYamlCourse.sh japanese-from-italian
```

This second step is what produces the files the web app needs (e.g. `courseData.json`, challenges, introductions)
under:
`apps/web/src/courses/japanese-from-italian/`

---

## Run the web app (development)

```bash
npm run web-serve
```

Then open:
- `http://localhost:5173/`

### Opening directly into the Japanese course (recommended)
I added this file to redirect directly to the Japanese course:

`apps/web/src/routes/+page.ts`
```ts
import { redirect } from '@sveltejs/kit';
import { base } from '$app/paths';

export const load = () => {
  throw redirect(302, `${base}/course/japanese-from-italian`);
};
```

---

## VPS Deployment

See `README_VPS_UBUNTU_24_04.md`.

---

## Links

- Upstream: LibreLingo Community https://github.com/LibreLingoCommunity/LibreLingo
- Live instance: https://www.nihonjindes.net/
