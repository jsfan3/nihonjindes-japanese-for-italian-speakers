#!/usr/bin/env python3
"""
ll02_generate_course_from_json.py

Generate/update a YAML LibreLingo course from a single JSON specification and import custom images.

- Words vs phrases: if "ja" contains spaces => Phrase, else Word.
- Stable Skill UUIDs persisted in courses/<course_slug>/.ll_ids.json.
- Japanese-specific function-token glossary injected per-skill via Mini-dictionary (Italian gloss -> Japanese token),
  only for tokens actually used in that skill's phrases and not introduced as New words.
- Emits warnings for unknown tokens used in phrases.
- Images are copied from external paths, made square (crop or pad), resized, and written to apps/web/static/images as:
    <name>.jpg, <name>_tiny.jpg, <name>_tinier.jpg
  YAML references the base name without extension/suffix.

Requires: PyYAML, Pillow
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from PIL import Image

_slug_re = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# A pragmatic Japanese "function token" glossary (token -> Italian gloss).
JP_FUNCTION_GLOSSARY: Dict[str, str] = {
    "ございます": "essere/esistere (forma cortese) (gozaimasu)",
    "のみます": "bere (forma cortese) (nomimasu)",
    "たべます": "mangiare (forma cortese) (tabemasu)",
    # Particles
    "は": "particella di tema (wa)",
    "が": "particella soggetto/focus (ga)",
    "を": "particella oggetto (o)",
    "に": "particella di luogo/tempo/dativo (ni)",
    "へ": "particella direzione (e)",
    "で": "particella di luogo/mezzo (de)",
    "と": "particella 'e' / 'con' / citazione (to)",
    "や": "particella elenco non esaustivo (ya)",
    "も": "particella 'anche' (mo)",
    "の": "particella possessivo/relativo (no)",
    "から": "da / 'a partire da' (kara)",
    "まで": "fino a (made)",
    # Sentence final / nuance
    "か": "particella interrogativa (ka)",
    "ね": "particella conferma/empatia (ne)",
    "よ": "particella enfasi (yo)",
    # Copula / politeness
    "です": "copula (forma cortese)",
    "でした": "copula passato (forma cortese)",
    "だ": "copula (informale)",
    "ます": "desinenza verbale cortese (masu)",
    "ました": "passato cortese (mashita)",
    "ません": "negazione cortese (masen)",
    "ませんでした": "negazione passata cortese (masen deshita)",
    "ください": "per favore (kudasai)",
    # Honorifics
    "さん": "suffisso onorifico (san)",
    "さま": "onorifico elevato (sama)",
    "ちゃん": "diminutivo/affettivo (chan)",
    "くん": "onorifico informale (kun)",
    # Common words often used in starter phrases
    "わたし": "io (watashi)",
    "あなた": "tu/Lei (anata)",
    "これ": "questo (kore)",
    "それ": "quello (sore)",
    "あれ": "quello là (are)",
    "ここ": "qui (koko)",
    "そこ": "lì (soko)",
    "あそこ": "là (asoko)",
    "だれ": "chi (dare)",
    "なに": "che cosa (nani)",
    "どこ": "dove (doko)",
    # Existence (common)
    "ある": "esserci/esistere (inanimati) (aru)",
    "いる": "esserci/esistere (animati) (iru)",
    "います": "esserci (animati) (forma cortese) (imasu)",
}

_PUNCT_STRIP = " \t\r\n\u3000。、！？!?.,;:「」『』（）()[]{}\"'“”‘’…—-・"

def die(msg: str, code: int = 2) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)

def warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)

def info(msg: str) -> None:
    print(f"[INFO] {msg}")

def require(cond: bool, msg: str) -> None:
    if not cond:
        die(msg)

def is_slug(s: str) -> bool:
    return bool(_slug_re.fullmatch(s))

def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"Spec JSON not found: {path}")
    except json.JSONDecodeError as e:
        die(f"Invalid JSON in {path}: {e}")

def detect_git_remote_url(repo_dir: Path, remote: str = "origin") -> Optional[str]:
    """Best-effort: return git remote URL (e.g., for Course.Repository)."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_dir), "config", "--get", f"remote.{remote}.url"],
            stderr=subprocess.DEVNULL,
        )
        url = out.decode("utf-8", errors="replace").strip()
        return url or None
    except Exception:
        return None

def normalize_token(tok: str) -> str:
    return tok.strip(_PUNCT_STRIP)

def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()

@dataclass(frozen=True)
class ImageSizes:
    base: int = 512
    tiny: int = 256
    tinier: int = 128

def _resample_lanczos() -> int:
    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    return Image.LANCZOS  # type: ignore[attr-defined]

def center_crop_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))

def pad_to_square(img: Image.Image, fill_rgb=(255, 255, 255)) -> Image.Image:
    w, h = img.size
    side = max(w, h)
    out = Image.new("RGB", (side, side), fill_rgb)
    left = (side - w) // 2
    top = (side - h) // 2
    out.paste(img, (left, top))
    return out

def load_image_rgb(path: Path) -> Image.Image:
    try:
        img = Image.open(path)
    except FileNotFoundError:
        die(f"Image not found: {path}")
    except Exception as e:
        die(f"Failed to open image {path}: {e}")

    if img.mode in ("RGBA", "LA") or ("transparency" in img.info):
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        alpha = img.split()[-1] if img.mode in ("RGBA", "LA") else None
        bg.paste(img, mask=alpha)
        img = bg.convert("RGB")
    else:
        img = img.convert("RGB")
    return img

def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def save_jpeg(img: Image.Image, path: Path, quality: int = 90) -> None:
    ensure_parent(path)
    img.save(path, format="JPEG", quality=quality, optimize=True, progressive=True)

def validate_spec(spec: Any) -> Dict[str, Any]:
    require(isinstance(spec, dict), "Top-level spec must be a JSON object.")
    require("course" in spec and isinstance(spec["course"], dict), "Spec must contain object field 'course'.")
    course = spec["course"]
    for k in ("slug", "language", "from", "license"):
        require(k in course, f"course.{k} is required.")
    require(isinstance(course["slug"], str) and course["slug"], "course.slug must be a non-empty string.")
    require(is_slug(course["slug"]), f"course.slug must be slug-form. Got: {course['slug']}")

    for lang_key in ("language", "from"):
        require(isinstance(course[lang_key], dict), f"course.{lang_key} must be an object.")
        require(isinstance(course[lang_key].get("name"), str) and course[lang_key]["name"], f"course.{lang_key}.name required.")
        require(isinstance(course[lang_key].get("bcp47"), str) and course[lang_key]["bcp47"], f"course.{lang_key}.bcp47 required.")

    require(isinstance(course["license"], dict), "course.license must be an object.")
    for k in ("name", "short", "link"):
        require(isinstance(course["license"].get(k), str) and course["license"][k], f"course.license.{k} required.")

    require("categories" in spec and isinstance(spec["categories"], list) and spec["categories"], "Spec must contain non-empty list field 'categories'.")
    seen_cat: Set[str] = set()
    for ci, cat in enumerate(spec["categories"]):
        require(isinstance(cat, dict), f"categories[{ci}] must be an object.")
        require(isinstance(cat.get("slug"), str) and is_slug(cat["slug"]), f"categories[{ci}].slug invalid or missing.")
        require(cat["slug"] not in seen_cat, f"Duplicate category slug: {cat['slug']}")
        seen_cat.add(cat["slug"])
        require(isinstance(cat.get("name"), str) and cat["name"], f"categories[{ci}].name required.")
        require(isinstance(cat.get("lessons"), list) and cat["lessons"], f"categories[{ci}].lessons must be a non-empty list.")
        seen_les: Set[str] = set()
        for li, les in enumerate(cat["lessons"]):
            require(isinstance(les, dict), f"categories[{ci}].lessons[{li}] must be an object.")
            require(isinstance(les.get("slug"), str) and is_slug(les["slug"]), f"Lesson slug invalid or missing in {cat['slug']} lessons[{li}].")
            require(les["slug"] not in seen_les, f"Duplicate lesson slug in category '{cat['slug']}': {les['slug']}")
            seen_les.add(les["slug"])
            require(isinstance(les.get("name"), str) and les["name"], f"Lesson name required in {cat['slug']}/{les['slug']}.")
            require(isinstance(les.get("items"), list) and les["items"], f"Lesson items must be a non-empty list in {cat['slug']}/{les['slug']}.")
            for ii, item in enumerate(les["items"]):
                require(isinstance(item, dict), f"Item must be object at {cat['slug']}/{les['slug']} items[{ii}].")
                for k in ("ja", "it", "image"):
                    require(isinstance(item.get(k), str) and item[k].strip(), f"Missing '{k}' at {cat['slug']}/{les['slug']} items[{ii}].")
    return spec  # type: ignore[return-value]

def build_course_yaml(spec: Dict[str, Any], repository_url: Optional[str] = None) -> Dict[str, Any]:
    course = spec["course"]
    modules = [f"{cat['slug']}/" for cat in spec["categories"]]
    out: Dict[str, Any] = {
        "Course": {
            "Language": {"Name": course["language"]["name"], "IETF BCP 47": course["language"]["bcp47"]},
            "For speakers of": {"Name": course["from"]["name"], "IETF BCP 47": course["from"]["bcp47"]},
            "License": {"Name": course["license"]["name"], "Short name": course["license"]["short"], "Link": course["license"]["link"]},
        },
        "Modules": modules,
    }
    # Course.Repository is required by the YAML loader/schema.
    repo = (
        repository_url
        or course.get("repository")
        or course.get("repo")
        or course.get("Repository")
    )
    if not isinstance(repo, str) or not repo.strip():
        repo = "https://example.com/your-repo"
        warn("Course.Repository not provided; using placeholder. Set course.repository in JSON or pass --repository-url.")
    out["Course"]["Repository"] = repo.strip()

    if isinstance(course.get("special_characters"), list) and course["special_characters"]:
        out["Course"]["Special characters"] = course["special_characters"]
    if isinstance(course.get("title"), str) and course["title"].strip():
        out["Course"]["Name"] = course["title"].strip()
    if isinstance(course.get("description"), str) and course["description"].strip():
        out["Course"]["Description"] = course["description"].strip()
    return out

def build_module_yaml(module_name: str, skill_files: List[str]) -> Dict[str, Any]:
    return {"Module": {"Name": module_name}, "Skills": skill_files}

def dump_yaml(path: Path, data: Dict[str, Any]) -> None:
    ensure_parent(path)
    txt = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120, default_flow_style=False)
    path.write_text(txt, encoding="utf-8")

def load_or_init_ids(ids_path: Path) -> Dict[str, str]:
    if ids_path.exists():
        try:
            data = json.loads(ids_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            warn(f"Could not parse {ids_path}; starting with empty ID map.")
    return {}

def save_ids(ids_path: Path, ids_map: Dict[str, str]) -> None:
    ensure_parent(ids_path)
    ids_path.write_text(json.dumps(ids_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def stable_skill_key(category_slug: str, lesson_slug: str) -> str:
    return f"{category_slug}/{lesson_slug}"

def get_or_create_uuid(ids_map: Dict[str, str], key: str) -> str:
    if key in ids_map:
        return ids_map[key]
    new_id = str(uuid.uuid4())
    ids_map[key] = new_id
    return new_id

def make_image_basename(course_slug: str, image_path: str, explicit_id: Optional[str] = None) -> str:
    if explicit_id:
        safe = explicit_id.strip()
        if not safe or not re.fullmatch(r"[a-zA-Z0-9_-]+", safe):
            die(f"Invalid image_id '{explicit_id}'. Use only letters/numbers/_/-.")
        return f"{course_slug}_{safe}"
    h = sha1_hex(f"{course_slug}|{image_path}".encode("utf-8"))[:12]
    return f"{course_slug}_{h}"

def generate_skill_yaml(
    *,
    skill_id: str,
    skill_name: str,
    thumbnails: List[str],
    new_words: List[Dict[str, Any]],
    phrases: List[Dict[str, Any]],
    mini_dictionary: Dict[str, List[Dict[str, str]]],
) -> Dict[str, Any]:
    """Build a skill YAML dict.

    Notes:
      - `Mini-dictionary` is REQUIRED by the JSON exporter for "chips" challenges.
      - The exporter will raise if a word (e.g. an Italian token from a translation)
        has no entry in the mini-dictionary.
    """
    out: Dict[str, Any] = {"Skill": {"Id": skill_id, "Name": skill_name}}
    if thumbnails:
        thumbnails = list(thumbnails)[:3]
        while len(thumbnails) < 3:
            thumbnails.append(thumbnails[0])
        out["Skill"]["Thumbnails"] = thumbnails
    if new_words:
        out["New words"] = new_words
    if phrases:
        out["Phrases"] = phrases
    if mini_dictionary:
        out["Mini-dictionary"] = mini_dictionary
    return out


def prune_course_tree(course_dir: Path, desired_module_slugs: Set[str], desired_skill_files: Set[Path]) -> None:
    for child in list(course_dir.iterdir()):
        if child.is_dir():
            slug = child.name
            if slug.startswith("."):
                continue
            if slug not in desired_module_slugs:
                shutil.rmtree(child)
    for module_slug in desired_module_slugs:
        skills_dir = course_dir / module_slug / "skills"
        if not skills_dir.exists():
            continue
        for f in skills_dir.glob("*.yaml"):
            if f not in desired_skill_files:
                f.unlink()

def resolve_image_path(repo_dir: Path, spec_path: Path, image_str: str) -> Path:
    """Resolve an image path string.

    Supports:
      - ~/...
      - absolute paths
      - relative paths (first relative to repo root, then relative to spec file directory)
    """
    expanded = os.path.expanduser(image_str)
    p = Path(expanded)
    if p.is_absolute():
        return p
    cand1 = (repo_dir / p).resolve()
    if cand1.exists():
        return cand1
    cand2 = (spec_path.parent / p).resolve()
    return cand2

def generate_all(
    *,
    repo_dir: Path,
    spec_path: Path,
    repository_url: Optional[str],
    prune_course: bool,
    crop_mode: str,
    sizes: ImageSizes,
    images_dir: Optional[Path],
    quality: int,
    fail_on_warnings: bool,
) -> int:
    spec = validate_spec(read_json(spec_path))
    course_slug = spec["course"]["slug"]
    target_lang_name = spec["course"]["language"]["name"]
    base_lang_name = spec["course"]["from"]["name"]


    require((repo_dir / "courses").exists(), f"Repo does not look right (missing 'courses/' in {repo_dir}).")
    require((repo_dir / "apps" / "web").exists(), f"Repo does not look right (missing 'apps/web/' in {repo_dir}).")

    course_dir = repo_dir / "courses" / course_slug
    web_images_dir = images_dir or (repo_dir / "apps" / "web" / "static" / "images")
    web_images_dir.mkdir(parents=True, exist_ok=True)
    course_dir.mkdir(parents=True, exist_ok=True)

    ids_path = course_dir / ".ll_ids.json"
    ids_map = load_or_init_ids(ids_path)

    desired_module_slugs: Set[str] = set()
    desired_skill_files: Set[Path] = set()

    warnings_count = 0
    imported_images: Set[str] = set()
    resample = _resample_lanczos()

    repo_url = repository_url or detect_git_remote_url(repo_dir)
    dump_yaml(course_dir / "course.yaml", build_course_yaml(spec, repository_url=repo_url))

    for cat in spec["categories"]:
        cat_slug = cat["slug"]
        cat_name = cat["name"]
        desired_module_slugs.add(cat_slug)

        module_dir = course_dir / cat_slug
        skills_dir = module_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        skill_file_names: List[str] = []

        for les in cat["lessons"]:
            lesson_slug = les["slug"]
            lesson_name = les["name"]

            key = stable_skill_key(cat_slug, lesson_slug)
            skill_uuid = get_or_create_uuid(ids_map, key)

            words: List[Dict[str, Any]] = []
            phrases: List[Dict[str, Any]] = []
            thumbnails: List[str] = []
            thumbs_set: Set[str] = set()
            new_words_ja: Set[str] = set()

            first_img_base: Optional[str] = None

            for item in les["items"]:
                ja = str(item.get("ja") or item.get("jp") or item.get("japanese") or "").strip()
                it = str(item.get("it") or item.get("italian") or item.get("translation") or "").strip()
                img_src = str(item.get("image") or item.get("img") or item.get("image_path") or "").strip()
                require(bool(ja and it and img_src),
                        f"Item in '{cat_slug}/{lesson_slug}' missing ja/it/image (or jp/it/image): {item}")


                img_base = make_image_basename(course_slug, img_src, item.get("image_id"))

                if first_img_base is None:
                    first_img_base = img_base

                if img_base not in imported_images:
                    img_path = resolve_image_path(repo_dir, spec_path, img_src)
                    img = load_image_rgb(img_path)

                    if crop_mode == "crop":
                        sq = center_crop_square(img)
                    elif crop_mode == "pad":
                        sq = pad_to_square(img)
                    else:
                        die(f"Invalid crop mode: {crop_mode}")

                    base_img = sq.resize((sizes.base, sizes.base), resample)
                    tiny_img = sq.resize((sizes.tiny, sizes.tiny), resample)
                    tinier_img = sq.resize((sizes.tinier, sizes.tinier), resample)

                    save_jpeg(base_img, web_images_dir / f"{img_base}.jpg", quality=quality)
                    save_jpeg(tiny_img, web_images_dir / f"{img_base}_tiny.jpg", quality=quality)
                    save_jpeg(tinier_img, web_images_dir / f"{img_base}_tinier.jpg", quality=quality)

                    imported_images.add(img_base)

                if img_base not in thumbs_set and len(thumbnails) < 3:
                    thumbnails.append(img_base)
                    thumbs_set.add(img_base)

                if " " in ja:
                    phrases.append({"Phrase": ja, "Translation": it})
                else:
                    new_words_ja.add(ja)
                    words.append({"Word": ja, "Translation": it, "Images": [img_base, img_base, img_base]})

            # Schema expects exactly 3 thumbnails if the field is present.
            if thumbnails:
                while len(thumbnails) < 3:
                    thumbnails.append(thumbnails[0])
            elif first_img_base:
                thumbnails = [first_img_base, first_img_base, first_img_base]
            else:
                placeholder = f"{course_slug}_placeholder"
                thumbnails = [placeholder, placeholder, placeholder]
                warn(f"No thumbnails could be determined for {course_slug}/{cat_slug}/{lesson_slug}; using placeholder '{placeholder}'.")

                        # Build per-skill Mini-dictionary used by the JSON exporter for "chips" challenges.
            # The exporter will raise if a token appearing in a phrase/translation has no entry.
            ja_word_defs: Dict[str, str] = {nw["Word"]: nw["Translation"] for nw in words}

            ja_tokens: Set[str] = set(ja_word_defs.keys())
            for p in phrases:
                for raw in str(p["Phrase"]).split(" "):
                    tok = normalize_token(raw)
                    if tok:
                        ja_tokens.add(tok)

            it_tokens: Set[str] = set()
            for p in phrases:
                for raw in str(p["Translation"]).split(" "):
                    tok = normalize_token(raw)
                    if tok:
                        it_tokens.add(tok)
                        it_tokens.add(tok.lower())

            for nw in words:
                for raw in str(nw["Translation"]).split(" "):
                    tok = normalize_token(raw)
                    if tok:
                        it_tokens.add(tok)
                        it_tokens.add(tok.lower())

            ja_entries: List[Dict[str, str]] = []
            for tok in sorted(ja_tokens):
                if tok in ja_word_defs:
                    gloss = ja_word_defs[tok]
                else:
                    gloss = JP_FUNCTION_GLOSSARY.get(tok)
                    if gloss is None:
                        warnings_count += 1
                        warn(
                            f"Unknown Japanese token for mini-dictionary in {course_slug}/{cat_slug}/{lesson_slug}: '{tok}' "
                            f"(add it as a Word item or extend JP_FUNCTION_GLOSSARY). Using self-gloss as fallback."
                        )
                        gloss = tok
                ja_entries.append({tok: str(gloss)})

            it_entries: List[Dict[str, str]] = []
            for tok in sorted(it_tokens):
                # Minimal definition to satisfy exporter: identity gloss.
                it_entries.append({tok: tok})

            mini_dict: Dict[str, List[Dict[str, str]]] = {
                target_lang_name: ja_entries,
                base_lang_name: it_entries,
            }
            skill_yaml = generate_skill_yaml(
                skill_id=skill_uuid,
                skill_name=lesson_name,
                thumbnails=thumbnails,
                new_words=words,
                phrases=phrases,
                mini_dictionary=mini_dict,
            )

            skill_file = skills_dir / f"{lesson_slug}.yaml"
            dump_yaml(skill_file, skill_yaml)
            desired_skill_files.add(skill_file)
            skill_file_names.append(f"{lesson_slug}.yaml")

        dump_yaml(module_dir / "module.yaml", build_module_yaml(cat_name, skill_file_names))

    save_ids(ids_path, ids_map)

    if prune_course:
        prune_course_tree(course_dir, desired_module_slugs, desired_skill_files)

    info(f"Course generated: {course_dir}")
    info(f"Images written to: {web_images_dir} (unique images: {len(imported_images)}; 3 variants each)")
    if warnings_count:
        warn(f"Completed with {warnings_count} warning(s).")
        return 1 if fail_on_warnings else 0
    info("Completed with no warnings.")
    return 0

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a LibreLingo YAML course from a JSON spec.")
    p.add_argument("--repo", required=True, help="Path to repo root.")
    p.add_argument("--spec", required=True, help="Path to course spec JSON.")
    p.add_argument("--repository-url", default=None,
                   help="Override Course.Repository (default: detect git remote origin).")
    p.add_argument("--prune-course", action="store_true",
                   help="Delete module directories and skill YAML files not present in the JSON spec.")
    p.add_argument("--crop-mode", choices=["crop", "pad"], default="crop",
                   help="How to make images square: center-crop or pad to square.")
    p.add_argument("--base-size", type=int, default=512)
    p.add_argument("--tiny-size", type=int, default=256)
    p.add_argument("--tinier-size", type=int, default=128)
    p.add_argument("--images-dir", default=None,
                   help="Override images output dir (default: <repo>/apps/web/static/images).")
    p.add_argument("--jpeg-quality", type=int, default=90)
    p.add_argument("--fail-on-warnings", action="store_true",
                   help="Return non-zero exit code if unknown-token warnings occur.")
    return p.parse_args()

def main() -> None:
    args = parse_args()
    repo_dir = Path(os.path.expanduser(args.repo)).resolve()
    spec_path = Path(os.path.expanduser(args.spec)).resolve()
    images_dir = Path(os.path.expanduser(args.images_dir)).resolve() if args.images_dir else None

    sizes = ImageSizes(base=args.base_size, tiny=args.tiny_size, tinier=args.tinier_size)
    require(sizes.base > 0 and sizes.tiny > 0 and sizes.tinier > 0, "Image sizes must be > 0.")
    require(sizes.base >= sizes.tiny >= sizes.tinier, "Expected base >= tiny >= tinier.")

    code = generate_all(
        repo_dir=repo_dir,
        spec_path=spec_path,
        repository_url=args.repository_url,
        prune_course=args.prune_course,
        crop_mode=args.crop_mode,
        sizes=sizes,
        images_dir=images_dir,
        quality=args.jpeg_quality,
        fail_on_warnings=args.fail_on_warnings,
    )
    sys.exit(code)

if __name__ == "__main__":
    main()
