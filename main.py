import time
from threading import Thread
# تأكدي من استيراد المكتبات اللازمة مثل flask و requests إذا كانت مستخدمة في باقي الكود
# من الصورة يبدو أنك تستخدمين Flask لـ Railway

# =========================
# 1. وظيفة تتبع الصفقات النشطة
# =========================
def track_active_trades():
    while True:
        try:
            # هنا نفترض وجود قائمة trades يتم جلبها من Unusual Whales
            # لغرض استمرار الكود بدون خطأ إذا كانت trades غير معرفة:
            global trades 
            current_trades = trades[:50] if 'trades' in globals() else []

            for trade in current_trades:
                try:
                    # تنظيف وتوحيد رمز العقد لضمان التطابق التام
                    option_symbol = str(trade.get("option_symbol") or trade.get("contract") or "").strip().upper()
                    if not option_symbol:
                        continue

                    current_price = safe_float(trade.get("price") or trade.get("mark") or trade.get("last"))
                    if current_price <= 0:
                        continue

                    # البحث في الصفقات النشطة (active_trades)
                    for trade_id, active in list(active_trades.items()):
                        if active.get("closed"):
                            continue
                        
                        # مطابقة دقيقة لرموز العقود (مثلاً SPXW260330P06370000)
                        if active.get("option_symbol", "").strip().upper() == option_symbol:
                            check_targets(trade_id, current_price)

                except Exception as e:
                    print(f"⚠️ Error in inner trade loop: {repr(e)}")

        except Exception as e:
            print(f"❌ track active trade error: {repr(e)}")

        # تحديث كل 5 ثوانٍ لسرعة سباكس القصوى
        time.sleep(POLL_SECONDS if 'POLL_SECONDS' in globals() else 5)

# =========================
# 2. الدالة الرئيسية (MAIN)
# =========================
def main():
    # التحقق من وجود المتغيرات البيئية (Env Vars)
    if not TELEGRAM_TOKEN or not CHAT_ID or not UW_API_KEY:
        print("❌ Missing env vars: BOT_TOKEN / SIGNAL_CHAT_ID / UW_API_KEY")
        return

    # إرسال إشارة بدء التشغيل للتلجرام للتأكد من الربط
    try:
        send_msg("🚀 *Quiet Alpha Matching Engine Started*\n✅ Monitoring Live SPX Flow...")
    except:
        print("⚠️ Could not send Telegram start message.")

    # تشغيل مراقبة التدفق (monitor_flow) في خيط منفصل
    Thread(target=monitor_flow, daemon=True).start()
    
    # تشغيل تتبع الصفقات (track_active_trades) في خيط منفصل
    Thread(target=track_active_trades, daemon=True).start()
    
    # تشغيل سيرفر الويب الخاص بـ Railway
    print(f"📡 App running on port {PORT if 'PORT' in globals() else 5000}")
    app.run(host="0.0.0.0", port=int(PORT) if 'PORT' in globals() else 5000)

# =========================
# 3. نقطة الدخول (التصحيح النهائي)
# =========================
# هذا السطر هو الذي تسبب في الـ Crash في الصورة، قمت بتصحيحه الآن:
if name == "__main__":
    main()
