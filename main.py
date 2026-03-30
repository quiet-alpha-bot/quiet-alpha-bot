import time
from threading import Thread

# =========================
# إعدادات وتحديث الصفقات النشطة
# =========================
def track_active_trades():
    while True:
        try:
            # افتراضياً: يتم جلب الصفقات من الـ API الخاص بـ Unusual Whales
            # trades = fetch_uw_flow() 
            
            # تحديث الصفقات النشطة بمطابقة رمز الأوبشن / العقد
            for trade in trades[:50]:
                try:
                    # تنظيف رمز العقد لضمان المطابقة (SPXW / SPX)
                    option_symbol = str(trade.get("option_symbol") or trade.get("contract") or "").strip().upper()
                    if not option_symbol:
                        continue

                    current_price = safe_float(trade.get("price") or trade.get("mark") or trade.get("last"))
                    if current_price <= 0:
                        continue

                    # البحث في الصفقات النشطة عن تطابق تام لرمز الأوبشن
                    for trade_id, active in list(active_trades.items()):
                        if active["closed"]:
                            continue
                        
                        # مطابقة دقيقة لضمان وصول التنبيه (حروف كبيرة وبدون مسافات)
                        if active["option_symbol"].strip().upper() == option_symbol:
                            check_targets(trade_id, current_price)

                except Exception as e:
                    print(f"❌ Error tracking trade details: {repr(e)}")

        except Exception as e:
            print(f"❌ General tracking error: {repr(e)}")

        # وقت التحديث (يفضل 5 ثواني لسرعة سباكس)
        time.sleep(POLL_SECONDS if 'POLL_SECONDS' in globals() else 5)

# =========================
# الدالة الرئيسية لتشغيل البوت
# =========================
def main():
    # التحقق من وجود المتغيرات الضرورية لعمل التنبيهات
    if not TELEGRAM_TOKEN or not CHAT_ID or not UW_API_KEY:
        print("❌ Missing environment variables: TELEGRAM_TOKEN / CHAT_ID / UW_API_KEY")
        print("💡 تأكد من ضبط إعدادات التلغرام ومفتاح UW بشكل صحيح.")
        return

    print("🚀 Connecting to Telegram and Unusual Whales...")
    
    # إرسال رسالة ترحيبية للتلغرام للتأكد من الاتصال
    try:
        send_msg("🚀 *Quiet Alpha Matching Engine Started*\n✅ Monitoring SPX Flow...")
    except Exception as e:
        print(f"⚠️ Could not send Telegram start message: {e}")

    # تشغيل مراقبة السيولة في خيط منفصل (Background Thread)
    Thread(target=monitor_flow, daemon=True).start()
    
    # تشغيل تتبع الأهداف في خيط منفصل
    Thread(target=track_active_trades, daemon=True).start()
    
    # تشغيل السيرفر (Flask/App) لاستقبال الويب هوك أو الأوامر
    app.run(host="0.0.0.0", port=PORT if 'PORT' in globals() else 5000)

# =========================
# نقطة الدخول (تصحيح الشرطات السفلية)
# =========================
if name == "__main__":
    main()
