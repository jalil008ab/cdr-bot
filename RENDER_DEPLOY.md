# Render.com'ga Deploy Qilish

## Nima O'zgardi (Asl → Render versiyasi)

| Narsa | Asl | Render |
|---|---|---|
| Konteyner | 2 ta (bot + worker) | 1 ta (combined) |
| Jarayon boshqaruvi | docker-compose depends_on | supervisord |
| `read_only: true` | Bor | Olib tashlandi (Render qo'llab-quvvatlamaydi) |
| `tmpfs` | Bor | Olib tashlandi |
| `cap_drop: ALL` | Bor | Olib tashlandi |
| `user: 10001` | Bor | Olib tashlandi |
| Caddy | Bor (--profile proxy) | Kerak emas (Render o'zi HTTPS beradi) |
| WORKER_URL | `http://worker:8081` | `http://127.0.0.1:8081` |

## Deploy Qadamlari

### 1. Sirlar Tayyorlash (lokal kompyuterda)
```bash
cp .env.example .env
python scripts/gen_secrets.py
# Chiqqan 3 qatorni .env ga ko'chiring
```

### 2. Render'da Service Yaratish

**A usul - render.yaml bilan (tavsiya etiladi):**
1. GitHub'ga push qiling
2. Render Dashboard → New → Blueprint
3. Reponi tanlang → Deploy

**B usul - qo'lda:**
1. Render Dashboard → New → Web Service
2. Reponi ulang
3. Settings:
   - **Dockerfile Path:** `docker/Dockerfile.combined`
   - **Docker Context:** `.` (root)
   - **Port:** `8080`
4. Disk qo'shing: Mount path `/data`, 1 GB

### 3. Environment Variables Qo'shish
Render Dashboard → Service → Environment:

| Kalit | Qiymat |
|---|---|
| `BOT_TOKEN` | BotFather'dan |
| `WEBHOOK_BASE_URL` | `https://your-app.onrender.com` (Render beradi) |
| `WEBHOOK_SECRET` | gen_secrets.py chiqargan |
| `WORKER_TOKEN` | gen_secrets.py chiqargan |
| `DATA_KEY` | gen_secrets.py chiqargan |
| `ADMIN_IDS` | Telegram user ID'ingiz |
| `WORKER_URL` | `http://127.0.0.1:8081` |
| `DB_PATH` | `/data/bot.db` |
| `PORT` | `8080` |
| `JOB_MEM_MB` | `350` (512MB RAM chegarasidan oshib ketmaslik uchun) |
| `WORKER_CONCURRENCY` | `1` |

### 4. Deploy va Tekshirish
```bash
# Deploy bo'lgandan keyin loglarni ko'ring:
# Render Dashboard → Service → Logs

# Yoki botga /start yuboring
```

## Muammo va Yechimlar

### Bot javob bermayapti
1. Loglarni tekshiring: `worker_ready` xabari chiqyaptimi?
2. WEBHOOK_BASE_URL to'g'rimi? (https:// bilan, oxirida / yo'q)
3. BOT_TOKEN to'g'rimi?

### "ConfigError: WEBHOOK_BASE_URL https:// bilan boshlanishi shart"
WEBHOOK_BASE_URL `https://` bilan boshlanishi kerak.

### Worker tayyor bo'lmaydi
supervisord logida `worker` holati qanday? Restart bo'lyaptimi?

### Free plan - bot uyquga ketadi va Disk masalasi
- Free Render service 15 daqiqa faoliyatsizlikdan keyin uyquga ketadi. Starter ($7/oy) yoki External Cron (masalan, cron-job.org) bilan `/healthz` ga ping yuboring.
- **DIQQAT:** Render Free planda Persistent Disk mavjud emas. Free planda disk qo'shmaysiz (ma'lumotlar bazasi har restartda yangilanadi). Disk faqat Starter planda ishlaydi.

## Xavfsizlik Eslatmasi
Render versiyasida bir nechta Docker hardening sozlamalari o'chirilgan
(`read_only`, `cap_drop`, `tmpfs`, `no-new-privileges`), chunki
Render managed platform o'z izolyatsiyasini ta'minlaydi. Lekin:
- Worker hali ham subprocess'da ishlaydi (rlimit bor)
- Non-root user yo'q (Render root bilan ishlaydi)
- VPS'da ishlatmoqchi bo'lsangiz - asl `docker-compose.yml` ni ishlating
