# VPS Setup (Ubuntu Server 24.04) — How to deploy Nihonjindes (LibreLingo web)

This guide describes a **practical production deployment** on a VPS running **Ubuntu Server 24.04**.
It targets the workflow used in this repo:
- course is generated from `data/course-json/jp_course.json`
- exported into the web app
- deployed as a **static site** served by **Nginx**

> Why static? LibreLingo’s web app can be exported and served as static assets. This is the simplest and most stable
> VPS setup (no Node process in production).

---

## Overview

You will end up with:

- `https://example.com/` → a simple landing page (optional)
- `https://example.com/jp/` → the LibreLingo web app with the course (static files served by Nginx)

Local machine (Linux) responsibilities:
- edit JSON + images
- regenerate + export
- build production static output
- rsync the built output to VPS

VPS responsibilities:
- serve static files with Nginx
- provide HTTPS certificates (Let’s Encrypt)
- optional: basic auth / access control

---

## Assumptions

- Domain: `example.com`
- Course path on domain: `/jp/`
- Repo name on your local machine: `nihonjindes-japanese-for-italian-speakers`
- VPS user: `root`

Replace placeholders accordingly.

---

## Part A — DNS

Create the following DNS records:

- `A` record:
  - Name: `@`
  - Value: `<YOUR_VPS_PUBLIC_IP>`

Optional:
- `A` record for `www` → same IP

Wait for DNS propagation.

---

## Part B — VPS base setup (Ubuntu 24.04)

SSH into your VPS:

```bash
ssh root@example.com
```

Update OS and install Nginx + Certbot:

```bash
apt update
apt -y upgrade
apt -y install nginx ufw certbot python3-certbot-nginx
```

Enable firewall (allow SSH + HTTP + HTTPS):

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
ufw status
```

Create web directories:

```bash
mkdir -p /var/www/example.com/landing
mkdir -p /var/www/example.com/jp
chown -R www-data:www-data /var/www/example.com
```

---

## Part C — Nginx site config (subfolder deployment)

Create an Nginx server block:

```bash
nano /etc/nginx/sites-available/example.com
```

Use this template (edit domain + paths):

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    # Landing page (optional)
    root /var/www/example.com/landing;
    index index.html;

    location = / {
        try_files /index.html =404;
    }

    # LibreLingo static app under /jp/
    location /jp/ {
        alias /var/www/example.com/jp/;
        try_files $uri $uri/ /jp/index.html;
    }
}
```

Enable the site:

```bash
ln -sf /etc/nginx/sites-available/example.com /etc/nginx/sites-enabled/example.com
nginx -t
systemctl reload nginx
```

At this point, `http://example.com/` should serve a landing page (if you add one)
and `http://example.com/jp/` will serve whatever you deploy later.

---

## Part D — HTTPS (Let’s Encrypt)

Run Certbot (Nginx plugin):

```bash
certbot --nginx -d example.com -d www.example.com
```

Choose redirect HTTP→HTTPS when prompted.

Test renewal:

```bash
certbot renew --dry-run
```

---

## Part E — Build the production static web app locally

On your **local machine** (Linux), from the repo root:

### 1) Regenerate course + export course JSON
```bash
./.venv/bin/python data/course-script/ll02_generate_course_from_json.py \
  --repo . \
  --spec data/course-json/jp_course.json \
  --prune-course

bash scripts/exportYamlCourse.sh japanese-from-italian
```

### 2) Set the app to run under `/jp/` (SvelteKit base path)

LibreLingo uses SvelteKit. For a subfolder deployment you must build with a base path.

The most robust approach is to set SvelteKit’s base path for production builds.
In many SvelteKit setups this is controlled by `$app/paths` and a `base` configuration in `svelte.config.*`
or via an environment variable.

**Practical approach (recommended):**
- Create a small build script that sets the base path before building
- Or adjust `apps/web/svelte.config.js` / `apps/web/svelte.config.ts` to use `paths.base = '/jp'` for production.

Because upstream config may change, here is a safe, repo-local approach:

Create `apps/web/.env.production`:
```bash
cat > apps/web/.env.production <<'EOF'
PUBLIC_BASE_PATH=/jp
EOF
```

Then ensure your redirect code uses `$app/paths.base` (recommended) and not a hardcoded `/jp`.

> If the upstream build does not read `PUBLIC_BASE_PATH`, you may need to patch the SvelteKit config.
> See “Troubleshooting: subfolder base path” below.

### 3) Build (export) the web app
From repo root:

```bash
npm install
npm run -w @librelingo/web build
```

Now export static output:

```bash
npm run export
```

The build output directory depends on upstream configuration. Common SvelteKit outputs:
- `apps/web/build`
- `apps/web/dist`
- `apps/web/.svelte-kit/output/client`
- `apps/web/.svelte-kit/output/prerendered`

Find it with:
```bash
find apps/web -maxdepth 3 -type d \( -name dist -o -name build \) -print
```

For the remainder of this guide, assume your static output is in:
`apps/web/build` (replace if different).

---

## Part F — Deploy to VPS (rsync)

From your local machine, sync the built site to the VPS:

```bash
rsync -avz --delete apps/web/build/ root@example.com:/var/www/example.com/jp/
```

If you have a landing page, deploy it too:

```bash
rsync -avz --delete landing/ root@example.com:/var/www/example.com/landing/
```

Then on VPS:
```bash
ssh root@example.com 'nginx -t && systemctl reload nginx'
```

Open:
- `https://example.com/jp/`

---

## Troubleshooting

### 1) “404 on refresh” for routes under /jp/
Nginx must fallback to `/jp/index.html` for SPA routing.
This is handled by:

```nginx
try_files $uri $uri/ /jp/index.html;
```

### 2) Assets load from `/` instead of `/jp/`
This is the **SvelteKit base path** issue. You must build with a base path of `/jp`.

Look for config in:
- `apps/web/svelte.config.*`
- `apps/web/vite.config.*`
- `apps/web/src/app.html` or route loaders

Search:
```bash
rg -n "base\s*:\s*'/" apps/web
rg -n "paths\s*:\s*\{" apps/web
```

### 3) Caching during testing
During initial testing we can disable caching in DevTools.

---

## Optional: basic auth (simple protection)

If you want to hide the course behind a password during early testing:

```bash
apt -y install apache2-utils
htpasswd -c /etc/nginx/.htpasswd youruser
```

Then in the `/jp/` location block:

```nginx
auth_basic "Restricted";
auth_basic_user_file /etc/nginx/.htpasswd;
```

Reload Nginx:
```bash
nginx -t && systemctl reload nginx
```

---

## Suggested operational workflow

1) Edit `data/course-json/jp_course.json` and add images in `data/course-img/`
2) Regenerate + export:
   - `ll02_generate_course_from_json.py`
   - `exportYamlCourse.sh`
3) Build web app (production)
4) `rsync` to VPS
5) Reload Nginx

---
