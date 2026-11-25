// AppCore — подпись для людей. Cloudflare Worker.
// Сайт: залил IPA → выбрал сертификат → GitHub подписал → «Установить». Файлы приватные, живут 2 часа.
import HTML from "./page.html";

const CHUNK = 40 * 1024 * 1024;
const TTL = 2 * 60 * 60;
const ID = /^[a-f0-9]{20}$/;

const json = (d, s = 200) =>
  new Response(JSON.stringify(d), { status: s, headers: { "content-type": "application/json; charset=utf-8" } });
const fail = (s, m) => json({ error: m }, s);

const gh = (env, extra = {}) => ({
  Authorization: `Bearer ${env.GH_TOKEN}`,
  "User-Agent": "ota-installer",
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  ...extra,
});

let releaseCache = null;
async function releaseId(env) {
  if (releaseCache) return releaseCache;
  const r = await fetch(`https://api.github.com/repos/${env.UPLOADS_REPO}/releases/tags/uploads`, { headers: gh(env) });
  if (!r.ok) throw new Error("нет релиза uploads в " + env.UPLOADS_REPO);
  releaseCache = (await r.json()).id;
  return releaseCache;
}

const getJob = async (env, id) => JSON.parse((await env.JOBS.get(id)) || "null");
const putJob = (env, id, d) => env.JOBS.put(id, JSON.stringify(d), { expirationTtl: TTL });

function esc(s) {
  return String(s ?? "").replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c]));
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.pathname;
    let m;
    try {
      if (req.method === "GET" && p === "/")
        return new Response(HTML, { headers: { "content-type": "text/html; charset=utf-8" } });

      // список живых сертификатов (отозванные сервер уже убрал)
      if (req.method === "GET" && p === "/certs") {
        const r = await fetch(`${env.SITE}/certs.json?t=${Date.now()}`);
        const d = r.ok ? await r.json() : { menu: [], ids: {} };
        const list = (d.menu || []).filter((x) => d.ids && d.ids[x]).map((x) => ({ name: x, id: d.ids[x] }));
        return json({ certs: list });
      }

      // новая задача
      if (req.method === "POST" && p === "/new") {
        const id = crypto.randomUUID().replace(/-/g, "").slice(0, 20);
        await putJob(env, id, { status: "uploading", t: Date.now() });
        return json({ id, chunk: CHUNK });
      }

      // кусок файла → приватный релиз на GitHub
      if (req.method === "PUT" && (m = p.match(/^\/up\/([a-f0-9]{20})\/(\d{1,2})$/))) {
        const job = await getJob(env, m[1]);
        if (!job || job.status !== "uploading") return fail(404, "задача не найдена");
        const len = req.headers.get("content-length");
        if (!len || +len > CHUNK + 1024 || +len < 1) return fail(413, "неверный размер куска");
        const rel = await releaseId(env);
        const name = `${m[1]}.part${m[2].padStart(2, "0")}`;
        const r = await fetch(
          `https://uploads.github.com/repos/${env.UPLOADS_REPO}/releases/${rel}/assets?name=${name}`,
          { method: "POST", headers: gh(env, { "content-type": "application/octet-stream", "content-length": len }), body: req.body }
        );
        if (!r.ok) return fail(502, "GitHub не принял кусок: " + (await r.text()).slice(0, 200));
        return json({ ok: true });
      }

      // запустить подпись
      if (req.method === "POST" && (m = p.match(/^\/sign\/([a-f0-9]{20})$/))) {
        const job = await getJob(env, m[1]);
        if (!job || job.status !== "uploading") return fail(404, "задача не найдена");
        const { parts, cert } = await req.json();
        if (!/^[A-F0-9]{8}$/.test(cert || "")) return fail(400, "выбери сертификат");
        if (!Number.isInteger(parts) || parts < 1 || parts > 60) return fail(400, "нет файла");
        const r = await fetch(
          `https://api.github.com/repos/${env.MAIN_REPO}/actions/workflows/sign-upload.yml/dispatches`,
          { method: "POST", headers: gh(env), body: JSON.stringify({ ref: "main", inputs: { req: m[1], parts: String(parts), cert } }) }
        );
        if (!r.ok) return fail(502, "GitHub не запустил подпись: " + (await r.text()).slice(0, 200));
        await putJob(env, m[1], { status: "signing", t: Date.now() });
        return json({ ok: true });
      }

      // статус
      if (req.method === "GET" && (m = p.match(/^\/job\/([a-f0-9]{20})$/))) {
        const job = await getJob(env, m[1]);
        if (!job) return fail(404, "задача не найдена или устарела");
        const out = { status: job.status, name: job.name, version: job.version, message: job.message };
        if (job.status === "done")
          out.installURL = "itms-services://?action=download-manifest&url=" + encodeURIComponent(`${url.origin}/m/${m[1]}.plist`);
        return json(out);
      }

      // ответ от GitHub Actions
      if (req.method === "POST" && (m = p.match(/^\/done\/([a-f0-9]{20})$/))) {
        if (req.headers.get("x-key") !== env.CALLBACK_KEY) return fail(403, "нет");
        const d = await req.json();
        await putJob(env, m[1], { ...d, t: Date.now() });
        return json({ ok: true });
      }

      // манифест для iOS
      if (req.method === "GET" && (m = p.match(/^\/m\/([a-f0-9]{20})\.plist$/))) {
        const job = await getJob(env, m[1]);
        if (!job || job.status !== "done") return fail(404, "нет");
        const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>items</key><array><dict>
<key>assets</key><array><dict><key>kind</key><string>software-package</string><key>url</key><string>${url.origin}/ipa/${m[1]}.ipa</string></dict></array>
<key>metadata</key><dict><key>bundle-identifier</key><string>${esc(job.bundle)}</string><key>bundle-version</key><string>${esc(job.version)}</string><key>kind</key><string>software</string><key>title</key><string>${esc(job.name)}</string></dict>
</dict></array></dict></plist>`;
        return new Response(plist, { headers: { "content-type": "application/xml" } });
      }

      // сам IPA (из приватного репо, через нас)
      if (req.method === "GET" && (m = p.match(/^\/ipa\/([a-f0-9]{20})\.ipa$/))) {
        const job = await getJob(env, m[1]);
        if (!job || job.status !== "done" || !job.asset) return fail(404, "файл удалён или ещё не готов");
        const r = await fetch(`https://api.github.com/repos/${env.UPLOADS_REPO}/releases/assets/${job.asset}`, {
          headers: gh(env, { Accept: "application/octet-stream" }),
          redirect: "manual",
        });
        const loc = r.headers.get("location");
        const file = loc ? await fetch(loc) : r;
        if (!file.ok) return fail(502, "не смог достать файл");
        const h = new Headers({ "content-type": "application/octet-stream" });
        if (file.headers.get("content-length")) h.set("content-length", file.headers.get("content-length"));
        return new Response(file.body, { headers: h });
      }

      return fail(404, "нет такого");
    } catch (e) {
      return fail(500, String(e.message || e));
    }
  },
};
