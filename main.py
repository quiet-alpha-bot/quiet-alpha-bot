uw_cache[opt_type] = {"time": None, "trade": None}
        else:
            log(f"⌛ TV cache for {opt_type} is too old")
    else:
        log(f"ℹ️ No TV cache yet for {opt_type}")


# ==========================================
# 🌐 Webhook
# ==========================================
@app.route("/webhook", methods=["POST"])
def tv_webhook():
    data = request.get_json(silent=True) or {}
    log(f"🟢 Webhook endpoint hit at {now().strftime('%H:%M:%S')}")
    log(f"📩 TV Alert Received: {data}")

    direction = normalize_side(data.get("direction") or data.get("signal"))
    if direction in ("CALL", "PUT"):
        handle_tv_alert(direction)
    else:
        log("⚠️ TV alert received without valid CALL/PUT direction")

    return jsonify({"status": "received"}), 200


@app.route("/")
def health_check():
    return "Quiet Alpha Engine is Running!"


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({
        "status": "ok",
        "time": now().strftime("%H:%M:%S"),
        "tv_cache": {
            "CALL": str(tv_cache["CALL"]),
            "PUT": str(tv_cache["PUT"]),
        },
        "uw_cache": {
            "CALL": str(uw_cache["CALL"]["time"]),
            "PUT": str(uw_cache["PUT"]["time"]),
        }
    }), 200


# ==========================================
# 🔄 Monitor Loop
# ==========================================
def monitor_loop():
    log("🛰️ Quiet Alpha Monitor Active...")

    while True:
        try:
            trades = fetch_flow_alerts()
            log(f"📡 UW fetched trades: {len(trades)}")

            trades = sorted(
                trades,
                key=lambda x: x.get("created_at", ""),
                reverse=False,
            )

            for trade in trades:
                key = build_trade_key(trade)

                if key in seen_ids:
                    continue

                seen_ids.add(key)
                process_whale_trade(trade)

            if len(seen_ids) > 5000:
                seen_ids.clear()
                sent_matches.clear()
                log("🧹 seen_ids and sent_matches cleared")

        except Exception as e:
            print(f"Quiet Alpha bot error: {e}")

        time.sleep(POLL_SECONDS)


# ==========================================
# 🏁 Main
# ==========================================
def main():
    send_msg(
        "🚀 *Quiet Alpha Match Engine Online*\n"
        "━━━━━━━━━━━━━━\n"
        "📡 TV Webhook Ready\n"
        "🐋 UW Flow Monitor Ready\n"
        "🎯 Waiting for CALL/PUT match\n"
        "━━━━━━━━━━━━━━\n"
        "🤍 جاهزون لأول تطابق"
    )

    Thread(target=monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)


if name == "__main__":
    main()
