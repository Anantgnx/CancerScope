// Intro page: fanned tray of real slide thumbnails, the urgent one lit; facts count up.

const TRAY_ORDER = ["normal_108", "tumor_107", "normal_150", "tumor_081", "tumor_082", "normal_123", "tumor_091",
                    "normal_109", "tumor_075", "tumor_084"];   // fallback "scanner order" when the API is unavailable

async function slidesOnDisk() {
  try {
    const p = await (await fetch("/api/queue")).json();
    const live = await (await fetch("/api/live")).json();
    const ids = p.slides.map(s => s.slide_id);
    if (live.holdout && !ids.includes(live.holdout)) ids.push(live.holdout);
    return ids.length ? ids : TRAY_ORDER;
  } catch { return TRAY_ORDER; }
}

async function buildTray() {
  const ids = await slidesOnDisk();
  // deterministic "scanner order": shuffle by a fixed key so the urgent slide sits mid-stack
  const order = [...ids].sort((a, b) => (hash(a) % 97) - (hash(b) % 97));
  const urgent = ids.includes("tumor_082") ? "tumor_082" : order[Math.floor(order.length / 2)];
  const tray = document.getElementById("tray");
  order.slice(0, 10).forEach((id, i) => {
    const el = document.createElement("div");
    el.className = "tray-slide" + (id === urgent ? " is-urgent" : "");
    el.style.setProperty("--i", i);
    el.innerHTML = `<img src="/api/slide/${id}/thumbnail" alt=""><span class="mono">${id}</span>`;
    tray.appendChild(el);
  });
  document.getElementById("callout-id").textContent = urgent;
  document.getElementById("tag-slides").textContent = Math.min(order.length, 10);
}

function hash(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; }

async function systemStatus() {
  try {
    const sys = await (await fetch("/api/system")).json();
    const pill = document.getElementById("device-pill");
    pill.classList.toggle("is-offline", !sys.online);
    pill.lastChild.textContent = sys.online ? "On-device · ASUS GX10" : "Offline · still running on-device";
  } catch {}
}

function animate() {
  if (!window.gsap) return;
  const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
  tl.from(".intro-kicker", { opacity: 0, y: 8, duration: 0.5 })
    .from(".intro-title .line", { opacity: 0, y: 28, duration: 0.8, stagger: 0.12 }, "-=0.2")
    .from(".intro-sub, .intro-cta, .intro-tags", { opacity: 0, y: 12, duration: 0.6, stagger: 0.08 }, "-=0.4")
    .from(".tray-slide", { opacity: 0, y: -40, rotate: () => gsap.utils.random(-8, 8), duration: 0.7, stagger: 0.07 }, 0.3)
    .to(".tray-slide.is-urgent", { y: -18, duration: 0.5 }, "+=0.2")
    .add(() => document.querySelector(".tray-slide.is-urgent")?.classList.add("is-lit"))
    .from(".tray-callout", { opacity: 0, x: 16, duration: 0.5 })
    .from(".fact", { opacity: 0, y: 10, duration: 0.5, stagger: 0.08 }, 0.9);

  document.querySelectorAll(".fact-num").forEach(el => {
    const to = +el.dataset.to, pre = el.dataset.prefix || "", suf = el.dataset.suffix || "";
    const o = { v: 0 };
    tl.to(o, { v: to, duration: 1.4, ease: "power2.out",
               onUpdate: () => { el.textContent = pre + Math.round(o.v) + suf; } }, 1.0);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  await buildTray();
  animate();
  systemStatus();
  setInterval(systemStatus, 3000);
});
