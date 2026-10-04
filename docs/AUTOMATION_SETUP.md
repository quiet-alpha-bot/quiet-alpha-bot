# QUIET ALPHA — Private Automation: التشغيل خطوة بخطوة

## ما الذي أُضيف؟ وما الذي لم يتغير؟

**لم يتغير:** كل البطاقات (القوالب، الخطوط، الخانات، الألوان، الحسابات) وكل الأوامر
`/c /call /p /put /win /success /max /highest /stop /loss /status /start` بنفس الصيغة والوظيفة.

**أضيف إلى `main.py` فقط:**
1. حماية `OWNER_USER_ID` (Fail Closed) على `/c /p /win /max /stop /status`.
2. منع طباعة رابط Telegram الذي يحتوي `BOT_TOKEN` في السجل (`httpx` → WARNING).
3. دالة `setup_private_automation` تُستدعى فقط إذا `AUTOMATION_ENABLED=true`.

**أضيف كمجلدات جديدة:** `automation/` (المحرك)، `tools/uw_basic_probe.py`، `tests/`، `docs/`.

---

## 0) قبل أي شيء: نقطة الرجوع

في نسخة الـRepository على جهازك (قبل تطبيق أي ملف):

```bash
git tag stable-manual-before-automation
git push origin stable-manual-before-automation
```

للرجوع الكامل لاحقًا: Redeploy لهذا الـTag من Railway، أو:
```bash
git revert --no-edit stable-manual-before-automation..HEAD
```

وللرجوع **بدون** أي تغيير في الكود: `AUTOMATION_ENABLED=false` → البوت اليدوي فقط.

---

## 1) أضيفي `OWNER_USER_ID` إلى Railway **قبل** أول Deploy

الأوامر اليدوية أصبحت Fail Closed: إذا غاب `OWNER_USER_ID` أو كان غير صالح، **لن يقبل البوت أي أمر**.

- القيمة = **Telegram User ID الخاص بحسابك** (رقم موجب). يمكن معرفته من `@userinfobot`.
- ليس `SIGNAL_CHAT_ID` (القناة الخاصة تبدأ عادةً بـ`-100`).

---

## 2) تطبيق الملفات

**الطريقة A (Git):** في مجلد الـRepository:
```bash
git am /path/to/patches/*.patch
git push
```

**الطريقة B (GitHub Web):** ارفعي الملفات من الحزمة كما هي في نفس المسارات
(واستبدلي `main.py` و`requirements.txt`).

---

## 3) إعداد Railway

| الإعداد | القيمة |
|---|---|
| PostgreSQL | `+ New` → `Database` → `PostgreSQL` داخل نفس المشروع |
| `DATABASE_URL` | في متغيرات خدمة البوت: Reference → `${{Postgres.DATABASE_URL}}` |
| `UW_API_KEY` | مفتاح UW (Basic / Trial) |
| `TV_WEBHOOK_SECRET` | نص عشوائي طويل (مثلًا 32 حرفًا) |
| `OWNER_USER_ID` | User ID الخاص بك |
| Public Domain | Service → Settings → Networking → **Generate Domain** على نفس قيمة `PORT` |
| Python | تأكدي أن `NIXPACKS_PYTHON_VERSION` = 3.11 أو 3.12 أو 3.13 (مكتبة `asyncpg` 0.30 مجمّعة لهذه الإصدارات) |

كل الـFlags تبقى `false` حتى نفعّلها خطوة بخطوة (القسم 6).

فحص سريع بعد التشغيل مع `AUTOMATION_ENABLED=true`: افتحي
`https://<domain>/health` → يجب أن تري `"automation_ready": true`.

---

## 4) TradingView Alert

- **Condition:** إشارة CALL (ثم Alert ثانٍ لـPUT) من مؤشر Quiet Alpha.
- **Trigger:** Once Per Bar Close.
- **Webhook URL:** `https://<domain>/webhook/tradingview`
- **Message** (CALL؛ ونفسها لـPUT مع `"direction": "PUT"`):

```json
{
  "secret": "TV_WEBHOOK_SECRET_VALUE",
  "direction": "CALL",
  "signal_time": "{{time}}",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "price": {{close}},
  "event_type": "INDICATOR"
}
```

`signal_id` يُشتق تلقائيًا من (ticker|timeframe|direction|signal_time|event_type)،
فإعادة إرسال نفس الشمعة تُرفض كتكرار.

> قبل أي تعديل على Pine Script: أرسلي أسطر متغيرات CALL/PUT النهائية وأي
> `alertcondition()` / `alert()` موجودة، لنربطها بدون تغيير منطق المؤشر.

---

## 5) اختبار UW Basic (أثناء جلسة SPX)

على جهازك (Python 3.10+، بدون تثبيت مكتبات):
```bash
export UW_API_KEY=...        # لا تضعيه في أي ملف
python tools/uw_basic_probe.py headers
python tools/uw_basic_probe.py chain --direction call
python tools/uw_basic_probe.py flow
python tools/uw_basic_probe.py track --symbol <SPXW...> --interval 5 --duration 900 --entry 3.40 --out track.csv
python tools/uw_basic_probe.py burst --n 20 --interval 0.5 --yes   # اختياري: اكتشاف حد الدقيقة
```
لا يرسل Telegram، لا يلمس قاعدة البيانات، لا يرسل أوامر، ولا يطبع المفتاح.

---

## 6) التفعيل خطوة بخطوة (STEP 1 → 20)

| STEP | ما يُفعَّل | ما نتحقق منه |
|---|---|---|
| 1 | لا شيء (`AUTOMATION_ENABLED=false`) | `/c /p /win /max /stop` تعمل كما كانت (منك أنتِ) |
| 2 | — | من حساب آخر: كل الأوامر ترد `⛔ غير مصرح.` |
| 3 | `AUTOMATION_ENABLED=true` | رسالة `🟢 QUIET ALPHA Automation بدأ` في القناة + `/health` + `/qa_status` |
| 4 | `TV_INTAKE_ENABLED=true` | Alert تجريبي → صف في `signals` بحالة `RECORDED`؛ إعادة نفس الـAlert = duplicate |
| 5 | (UW_API_KEY) | `/qa_status` يُظهر رؤوس UW بعد أول طلب |
| 6 | `SELECTION_ENABLED=true` | رسالة `Signal CALL — SELECTED SPXW… (proposals disabled)` + جدول `contract_candidates` |
| 7 | `UW_WATCH_ENABLED=true` (+`UW_WATCH_NOTIFY=true` اختياري) | صفوف في `uw_watch_observations` كل دقيقة؛ Watch عند التدفق القوي |
| 8 | `MATCHING_ENABLED=true` | تصنيف كل إشارة في `signal_matches` (INDICATOR_ONLY / BOTH_CONFIRMED / CONFLICT / UW_ONLY) |
| 9–10 | (مع 6) | أسباب الاختيار + `entry_decisions` (ENTER / WAIT / SKIP) |
| 11–12 | `PROPOSALS_ENABLED=true` | اقتراح بأزرار APPROVE / REJECT؛ RECONFIRM / CANCEL عند تغيّر السعر |
| 13 | (مع 12) | بطاقة CALL/PUT الحالية في القناة بعد APPROVE |
| 14–15 | `TRACKING_ENABLED=true` | رسالة LIVE TRACKING واحدة تُعدَّل |
| 16 | `MILESTONES_ENABLED=true` | إشعار منفصل لكل +10% جديد (حتى عند القفز)، بالترتيب، مرة واحدة |
| 17 | `SUCCESS_CARD_ENABLED=true` | بطاقة Successful Trade عند Observed Bid ≥ Entry + $1.00، مرة واحدة |
| 18 | `HIGHEST_CARD_ENABLED=true` (Trigger: `none` افتراضيًا) | `/qa_highest <id>` يرسل بطاقة Highest الحالية |
| 19 | Redeploy أثناء صفقة | المتابعة تستمر، لا تكرار لأي رسالة |
| 20 | ضغط الأزرار مرتين / Alert مكرر | لا تكرار |

---

## 7) أوامر المالك الجديدة

| الأمر | الوظيفة |
|---|---|
| `/qa_status` | حالة الأتمتة، الـFlags، الصفقات المتابَعة، رؤوس UW، آخر الأخطاء |
| `/qa_close <trade_id>` | إيقاف متابعة صفقة يدويًا |
| `/qa_highest <trade_id>` | إرسال بطاقة Highest Price الحالية (مرة واحدة لكل صفقة) |

---

## 8) التعريفات المعتمدة (UW Basic)

- **Observed Bid** = أحدث `nbbo_bid` أعاده UW REST وقت الـpoll (ليس ادعاءً بقابلية التنفيذ بحجم محدد).
- **Entry** = Ask وقت قرار ENTER، ثم يُعاد التسعير عند APPROVE (`approved_entry`).
- **+10%**: `Observed Bid ≥ approved_entry × (1 + n/100)` — كل مستوى إشعار مستقل، مرة واحدة فقط، ولا يُعاد بعد هبوط السعر وعودته.
- **SUCCESS**: `Observed Bid ≥ approved_entry + $1.00` (+$100 gross/contract) — حدث مستقل عن +10%، يرسل بطاقة Successful Trade الحالية مرة واحدة، ولا ينهي المتابعة.
- **القفزة في Poll واحد**: كل المستويات المتجاوزة تُنشر **بترتيب أسعار الـThresholds**.
  مثال: Entry $4.00 والـBid قفز من $4.20 إلى $5.30 →
  `+10%` ($4.40) ثم `+20%` ($4.80) ثم **بطاقة SUCCESS** ($5.00) ثم `+30%` ($5.20).
  الـBid المعروض في كل إشعار هو الـObserved Bid الفعلي الذي اكتُشف عنده التجاوز ($5.30)، وليس سعرًا مفترضًا لكل مستوى.
- **التخزين**: كل حدث يُكتشف ويُحفظ أولًا (`highest_milestone_reached`, `milestone_detections`) ثم يُنشر. لكل حدث مفتاح مستقل في جدول `events`: `(trade, MILESTONE, 10)`، `(trade, MILESTONE, 20)`، `(trade, SUCCESS_CARD)`، `(trade, MILESTONE, 30)`.
  إذا تعذّر نشر حدث، تنتظر الأحداث التي بعده للحفاظ على الترتيب. بعد Restart/Redeploy تُستكمل الأحداث غير المنشورة فقط، ولا يُعاد أي حدث منشور.
- **Highest**: `highest_bid` (+ `highest_mid`, `highest_last`) مع الوقت.

كل الأرقام غير المحسومة (Thresholds، النوافذ، الأوزان، الفترات) قيم
**DEVELOPMENT / SHADOW DEFAULTS** في `automation/config.py`، وكلها قابلة للتغيير من متغيرات Railway (انظري `.env.example`).

---

## 9) حدود معروفة

- العطل الرسمية والأيام القصيرة غير مُمثَّلة (أيام الأسبوع + 9:30–16:00 ET فقط).
- UW Basic لا يوفر تاريخ Quotes للـBackfill: إذا توقف البوت أثناء صفقة، ما حدث في فترة التوقف لا يُسترجع.
- دقة رصد المستويات = فترة الـPolling (`TRACKING_POLL_SECONDS`).
- أسماء Endpoints الـFlow (`net-prem-ticks`, `flow-alerts`) من التوثيق الرسمي؛ تُؤكَّد بأمر `flow` في أداة الاختبار.
- التاريخ على البطاقات بتوقيت الرياض كما في البوت الحالي (لم يُغيَّر).

---

## 10) الاختبارات

```bash
python -m unittest discover -s tests -t . -v                       # المنطق النقي
QA_TEST_PSQL_ARGS="-h HOST -p PORT -U USER" python -m unittest tests.test_integration_pg -v
PSQL_ARGS="-h HOST -p PORT -U USER" python tests/validate_sql.py qa_validate
```
